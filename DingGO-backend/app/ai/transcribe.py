"""转写链路：火山 LAS 转写 → 多段合并 → 角色标注（销售/客户/旁人）→ 有效性判断。

从 lark-sales-audio-asr-scripted/scripts/pipeline.py 和 classify-sales-call-roles 移植，去掉飞书读写。
角色标注的三个脚本（画像、审计、应用）原样放在 app/ai/roles/，用子进程调用，与原流水线一致。
LAS 通过 lasutil 命令行调用；worker 把 task_id 存进 visit_pipeline，已有 task_id 不会再次提交（防重复计费）。
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Protocol

from ..config import get_settings
from . import core

AI_DIR = Path(__file__).resolve().parent
ROLES_DIR = AI_DIR / "roles"
ROLE_BUILD, ROLE_APPLY, ROLE_AUDIT = (ROLES_DIR / f"{n}.py" for n in ("build_role_profile", "apply_role_map", "audit_role_map"))
ROLE_PROMPT = AI_DIR / "prompts" / "role-prompt.md"
VALIDITY_PROMPT = AI_DIR / "prompts" / "validity-prompt.md"

ALLOWED_ROLES = {"销售", "客户", "旁人"}
INLINE_SPEAKER = re.compile(r"\[spk(\d+)\]\[(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\]")
LINE_RE = re.compile(
    r"^\[(?P<start>\d\d:\d\d(?:\:\d\d)?\.\d{3})–"
    r"(?P<end>\d\d:\d\d(?:\:\d\d)?\.\d{3})\] "
    r"(?P<role>销售|客户|旁人)：(?P<text>.+)$"
)


class SubmitUncertain(RuntimeError):
    """提交请求已经发出，但无法确认服务端是否已创建任务（可能已计费）：禁止自动重提"""


class LasClient(Protocol):
    def upload(self, path: str) -> str: ...
    def submit(self, url: str, audio_format: str) -> dict: ...
    def poll(self, task_id: str) -> dict: ...


def json_from_output(output: str, label: str) -> dict[str, Any]:
    text = output.strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise RuntimeError(f"{label} 未返回 JSON: {text[:300]}")
        value = json.loads(text[start : end + 1])
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} 返回值不是 JSON 对象")
    return value


def deep_find(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = deep_find(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = deep_find(child, key)
            if found is not None:
                return found
    return None



def utterances_from(payload: dict[str, Any]) -> list[dict[str, Any]]:
    current: Any = payload
    for key in ("data", "result", "utterances"):
        if not isinstance(current, dict) or key not in current:
            break
        current = current[key]
    if isinstance(current, list):
        return current
    if isinstance(payload.get("utterances"), list):
        return payload["utterances"]
    raise ValueError("ASR 结果中没有 utterances")


def speaker_of(item: dict[str, Any]) -> str:
    additions = item.get("additions")
    if isinstance(additions, dict) and additions.get("speaker") is not None:
        return str(additions["speaker"])
    for key in ("speaker_id", "speaker", "spk"):
        if item.get(key) is not None:
            return str(item[key])
    return "unknown"


def collect_speakers(payload: dict[str, Any]) -> list[str]:
    speakers: set[str] = set()
    for item in utterances_from(payload):
        speakers.add(speaker_of(item))
        speakers.update(match.group(1) for match in INLINE_SPEAKER.finditer(str(item.get("text") or "")))
    return sorted(speakers, key=lambda value: (not value.isdigit(), int(value) if value.isdigit() else value))


def time_of(item: dict[str, Any], start: bool) -> int:
    keys = ("start_time", "start_ms") if start else ("end_time", "end_ms")
    for key in keys:
        if item.get(key) is not None:
            return int(item[key])
    return 0


def set_speaker(item: dict[str, Any], value: str) -> None:
    additions = item.get("additions")
    if isinstance(additions, dict) and additions.get("speaker") is not None:
        additions["speaker"] = value
        return
    for key in ("speaker_id", "speaker", "spk"):
        if item.get(key) is not None:
            item[key] = value
            return
    item["speaker"] = value


def shift_time(item: dict[str, Any], offset_ms: int) -> None:
    for key in ("start_time", "end_time", "start_ms", "end_ms"):
        if item.get(key) is not None:
            item[key] = int(item[key]) + offset_ms


def renumber_speaker(segment_index: int, speaker: str) -> str:
    """多段录音的 speaker ID 各自独立：段 n 的 speaker s 重编号为 n*100+s，
    避免不同段的同一 ID 被角色层当成同一人。"""
    try:
        return str(segment_index * 100 + int(speaker))
    except (TypeError, ValueError):
        return f"{segment_index}_{speaker}"


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型输出没有 JSON 对象")
    value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("模型 JSON 顶层不是对象")
    return value



def result_container(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("result"), dict):
        return data["result"]
    if isinstance(payload.get("result"), dict):
        return payload["result"]
    return payload



def validate_role_map(value: dict[str, Any], speakers: list[str]) -> dict[str, Any]:
    if "default" not in value:
        value = {"default": value, "segments": [], "fallback": "旁人"}
    default = {str(key): role for key, role in value.get("default", {}).items()}
    missing = sorted(set(speakers) - set(default))
    if missing:
        raise ValueError(f"角色映射缺少 speaker: {missing}")
    invalid = sorted({role for role in default.values() if role not in ALLOWED_ROLES})
    if invalid:
        raise ValueError(f"存在非法角色: {invalid}")
    if len(speakers) == 1 and set(default.values()) == {"旁人"}:
        raise ValueError("单 speaker 销售录音不能将唯一说话人标为旁人")
    if len(speakers) >= 2 and not {"销售", "客户"}.issubset(set(default.values())):
        raise ValueError("多说话人录音必须至少识别出销售和客户")
    segments = value.get("segments", [])
    if not isinstance(segments, list):
        raise ValueError("segments 必须是数组")
    normalized_segments: list[dict[str, Any]] = []
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get("map", {}), dict):
            raise ValueError("segment 格式错误")
        start = int(segment.get("start_ms") or 0)
        raw_end = segment.get("end_ms")
        end = None if raw_end is None else int(raw_end)
        if start < 0 or (end is not None and end <= start):
            raise ValueError("segment 时间范围无效")
        segment_map = {str(key): role for key, role in segment.get("map", {}).items()}
        unknown = sorted(set(segment_map) - set(speakers))
        if unknown:
            raise ValueError(f"segment 包含未知 speaker: {unknown}")
        if any(role not in ALLOWED_ROLES for role in segment_map.values()):
            raise ValueError("segment 存在非法角色")
        normalized_segments.append({
            "start_ms": start,
            "end_ms": end,
            "map": segment_map,
        })
    for speaker in speakers:
        intervals = sorted(
            (
                (segment["start_ms"], segment["end_ms"])
                for segment in normalized_segments
                if speaker in segment["map"]
            ),
            key=lambda item: item[0],
        )
        previous_end: int | None = -1
        for start, end in intervals:
            if previous_end is None or start < previous_end:
                raise ValueError(f"speaker {speaker} 的 segment 时间段重叠")
            previous_end = end
    fallback = value.get("fallback", "旁人")
    if fallback not in ALLOWED_ROLES:
        raise ValueError("fallback 非法")
    return {
        "default": default,
        "segments": sorted(normalized_segments, key=lambda item: item["start_ms"]),
        "fallback": fallback,
    }


def profile_scores(profile_text: str) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    current: str | None = None
    for line in profile_text.splitlines():
        match = re.match(r"## speaker (.+)", line)
        if match:
            current = match.group(1).strip()
            continue
        match = re.search(r"evidence_scores: sales=(\d+), customer=(\d+), bystander=(\d+)", line)
        if current and match:
            result[current] = {
                "sales": int(match.group(1)),
                "customer": int(match.group(2)),
                "bystander": int(match.group(3)),
            }
    return result


def fallback_role_map(speakers: list[str], profile_text: str) -> dict[str, Any]:
    scores = profile_scores(profile_text)
    if len(speakers) == 1:
        speaker = speakers[0]
        values = scores.get(speaker, {})
        role = (
            "销售"
            if values.get("sales", 0) >= values.get("customer", 0)
            else "客户"
        )
        return {"default": {speaker: role}, "segments": [], "fallback": "旁人"}
    sales = max(speakers, key=lambda s: scores.get(s, {}).get("sales", 0))
    remaining = [speaker for speaker in speakers if speaker != sales]
    customer = max(remaining, key=lambda s: scores.get(s, {}).get("customer", 0))
    mapping = {speaker: "旁人" for speaker in speakers}
    mapping[sales] = "销售"
    mapping[customer] = "客户"
    for speaker in speakers:
        if speaker in {sales, customer}:
            continue
        values = scores.get(speaker, {})
        if values.get("customer", 0) > max(values.get("sales", 0), values.get("bystander", 0)):
            mapping[speaker] = "客户"
        elif values.get("sales", 0) > max(values.get("customer", 0), values.get("bystander", 0)):
            mapping[speaker] = "销售"
    return {"default": mapping, "segments": [], "fallback": "旁人"}



def validate_transcript(path: Path) -> str:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("角色转写为空")
    previous = -1.0
    for index, line in enumerate(text.splitlines(), 1):
        match = LINE_RE.match(line)
        if not match:
            raise ValueError(f"第 {index} 行格式错误: {line[:100]}")
        stamp = match.group("start").split(":")
        seconds = (
            float(stamp[-1])
            + int(stamp[-2]) * 60
            + (int(stamp[-3]) * 3600 if len(stamp) == 3 else 0)
        )
        if seconds < previous:
            raise ValueError(f"第 {index} 行时间戳倒退")
        previous = seconds
    if re.search(r"\[spk\d+\]", text):
        raise ValueError("最终转写仍残留 speaker 标签")
    return text



def transcript_duration(labeled: str) -> float | None:
    """从转写最后一行 end 时间戳兜底估算总时长（秒）；解析不到返回 None。"""
    last_end: float | None = None
    for line in labeled.splitlines():
        match = LINE_RE.match(line)
        if not match:
            continue
        stamp = match.group("end").split(":")
        seconds = (
            float(stamp[-1])
            + int(stamp[-2]) * 60
            + (int(stamp[-3]) * 3600 if len(stamp) == 3 else 0)
        )
        if last_end is None or seconds > last_end:
            last_end = seconds
    return last_end


# ---------------------------------------------------------------------------
# 以下为后端新增：LAS 客户端、多段合并、角色标注、有效性判断
# ---------------------------------------------------------------------------

def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


class LasutilClient:
    """火山 LAS：调用 lasutil 命令行（file-upload / submit / poll），密钥来自环境 LAS_API_KEY"""

    def __init__(self) -> None:
        self.cfg = get_settings()

    def _run(self, *args: str) -> dict[str, Any]:
        import os
        import shutil
        if not self.cfg.las_api_key:
            raise RuntimeError("缺少 LAS_API_KEY")
        exe = self.cfg.lasutil_path or shutil.which("lasutil")
        if not exe:
            raise RuntimeError("找不到 lasutil，请设置 LASUTIL_PATH 或安装 las_sdk")
        env = {**os.environ, "LAS_API_KEY": self.cfg.las_api_key, "LAS_REGION": self.cfg.las_region}
        proc = subprocess.run([exe, *args], capture_output=True, encoding="utf-8", errors="replace", env=env)
        if proc.returncode:
            raise RuntimeError(f"lasutil 失败({proc.returncode}): {(proc.stderr or proc.stdout)[:500]}")
        return json_from_output(proc.stdout, "lasutil")

    def upload(self, path: str) -> str:
        url = deep_find(self._run("file-upload", path), "presigned_url")
        if not isinstance(url, str) or not url.startswith("http"):
            raise RuntimeError("上传成功但没有 presigned_url")
        return url

    def submit(self, url: str, audio_format: str) -> dict[str, Any]:
        payload = {
            "audio": {"url": url, "format": audio_format},
            "request": {
                "model_name": "bigmodel", "enable_itn": True, "enable_punc": True, "enable_ddc": False,
                "enable_speaker_info": True, "show_utterances": True, "show_speech_rate": True, "show_volume": True,
                "enable_lid": True, "enable_emotion_detection": True, "enable_gender_detection": True, "enable_denoise": True,
            },
        }
        try:
            return self._run("submit", "--version", self.cfg.las_operator_version, self.cfg.las_operator_id,
                             json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        except Exception as exc:  # 请求已发出但没拿到明确结果：可能已建任务
            raise SubmitUncertain(str(exc)) from exc

    def poll(self, task_id: str) -> dict[str, Any]:
        return self._run("poll", "--version", self.cfg.las_operator_version, self.cfg.las_operator_id, task_id)


def task_id_of(response: dict[str, Any]) -> str:
    task_id = deep_find(response, "task_id")
    if not task_id:
        raise RuntimeError("提交转写没有返回 Task ID")
    return str(task_id)


def task_status(response: dict[str, Any]) -> str:
    return str(deep_find(response, "task_status") or "UNKNOWN").upper()


def check_completed(response: dict[str, Any]) -> None:
    """已完成的转写结果验收：业务码为 0、文本和 utterances 非空"""
    code = str(deep_find(response, "business_code") or "")
    text = deep_find(response, "text")
    if code not in {"0", ""} or not isinstance(text, str) or not text.strip() or not utterances_from(response):
        raise RuntimeError(f"转写结果验收失败：business_code={code}")


def merge_payloads(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    """多段合并：按顺序拼接 utterances，说话人重编号，时间轴整体后移"""
    if len(payloads) == 1:
        return payloads[0]
    merged: list[dict[str, Any]] = []
    offset_ms = 0
    for index, payload in enumerate(payloads, 1):
        segment_end = 0
        for item in utterances_from(payload):
            new_item = copy.deepcopy(item)
            set_speaker(new_item, renumber_speaker(index, speaker_of(item)))
            shift_time(new_item, offset_ms)
            segment_end = max(segment_end, time_of(item, False))
            merged.append(new_item)
        offset_ms += segment_end
    return {"data": {"result": {"utterances": merged, "text": "".join(str(i.get("text") or "") for i in merged)}}}


def _run_script(script: Path, *args: str, label: str) -> None:
    proc = subprocess.run([sys.executable, str(script), *args], capture_output=True, encoding="utf-8", errors="replace")
    if proc.returncode:
        raise RuntimeError(f"{label} 失败: {(proc.stderr or proc.stdout)[:500]}")


def _role_llm(system: str, user: str) -> tuple[str, dict]:
    st = get_settings()
    return core.call_llm(system, user, st.role_llm_model or st.llm_model, 0)


def label_roles(work_dir: Path, payload: dict[str, Any]) -> tuple[str, str, dict, list[dict]]:
    """给说话人标销售/客户/旁人，返回 (带角色的转写文本, 来源, 审计摘要, 各次模型用量)"""
    work_dir.mkdir(parents=True, exist_ok=True)
    result_path = work_dir / "result.json"
    atomic_json(result_path, payload)
    profile_path, role_map_path, transcript_path = work_dir / "role-profile.md", work_dir / "role-map.json", work_dir / "labeled-transcript.txt"
    _run_script(ROLE_BUILD, str(result_path), "--output", str(profile_path), label="生成说话人画像")
    profile_text = profile_path.read_text(encoding="utf-8")
    speakers = collect_speakers(payload)
    usages: list[dict] = []
    role_prompt = ROLE_PROMPT.read_text(encoding="utf-8")
    source = "llm"
    try:
        raw, usage = _role_llm(role_prompt, "请根据以下 speaker 画像输出 role-map JSON：\n\n" + profile_text)
        usages.append(usage)
        role_map = validate_role_map(extract_json_object(raw), speakers)
    except Exception as exc:
        source = f"deterministic_fallback:{type(exc).__name__}"
        role_map = validate_role_map(fallback_role_map(speakers, profile_text), speakers)

    def audit_of(role_map: dict[str, Any], name: str) -> dict[str, Any]:
        map_path, audit_path = work_dir / f"role-map.{name}.json", work_dir / f"role-audit.{name}.json"
        atomic_json(map_path, role_map)
        _run_script(ROLE_AUDIT, str(result_path), "--role-map", str(map_path), "--output", str(audit_path), label="审计角色映射")
        return load_json(audit_path)

    def conflicts(audit: dict[str, Any]) -> int:
        return int(audit.get("summary", {}).get("conflict_windows") or 0)

    current_map, current_audit = role_map, audit_of(role_map, "initial")
    for attempt in range(1, 3):
        if not current_audit.get("summary", {}).get("needs_repair"):
            break
        try:
            raw, usage = _role_llm(role_prompt, (
                "请修正当前 role-map，只处理审计指出的连续高置信冲突。输出完整 role-map JSON。\n\nSpeaker profile:\n" + profile_text
                + "\n\nCurrent role-map:\n" + json.dumps(current_map, ensure_ascii=False)
                + "\n\nRole-map audit:\n" + json.dumps(current_audit, ensure_ascii=False)))
            usages.append(usage)
            repaired = validate_role_map(extract_json_object(raw), speakers)
        except Exception:
            break
        repaired_audit = audit_of(repaired, f"repair-{attempt}")
        if conflicts(repaired_audit) >= conflicts(current_audit):
            break
        current_map, current_audit = repaired, repaired_audit
        source += f"+audit_repair_{attempt}"
    atomic_json(role_map_path, current_map)
    if current_audit.get("summary", {}).get("severe"):
        raise ValueError(f"角色映射审计仍有严重语义冲突；conflict_windows={current_audit.get('summary', {}).get('conflict_windows')}")
    _run_script(ROLE_APPLY, str(result_path), "--role-map", str(role_map_path), "--output", str(transcript_path), label="应用角色映射")
    return validate_transcript(transcript_path), source, current_audit.get("summary", {}), usages


def judge_validity(labeled: str, duration_seconds: float | None = None) -> tuple[dict[str, Any], list[dict]]:
    """两层判定：规则（时长不足、转写为空）+ 模型判断内容是否属于业务场景。模型失败时保守判内容无效。
    返回 ({value: 有效|录音过短|内容无效, reason, source}, 模型用量)"""
    st = get_settings()
    duration = duration_seconds if duration_seconds else transcript_duration(labeled)
    if duration is not None and duration < st.valid_min_seconds:
        return {"value": "录音过短", "reason": f"录音时长 {duration:.0f} 秒，不足 {st.valid_min_seconds} 秒", "source": "rule"}, []
    if not [m for m in (LINE_RE.match(line) for line in labeled.splitlines()) if m]:
        return {"value": "内容无效", "reason": "转写为空或格式无法解析", "source": "rule"}, []
    user = (f"录音时长约 {duration:.0f} 秒。\n\n" if duration is not None else "") + f"以下为角色转写全文：\n\n{labeled}"
    try:
        raw, usage = _role_llm(VALIDITY_PROMPT.read_text(encoding="utf-8"), user)
        verdict = extract_json_object(raw)
        value = verdict.get("result")
        if value not in {"有效", "内容无效"}:
            raise ValueError(f"模型输出 result 非法: {value!r}")
        return {"value": value, "reason": str(verdict.get("reason") or "").strip()[:200], "source": "llm"}, [usage]
    except Exception as exc:
        return {"value": "内容无效", "reason": f"模型判断失败: {exc}", "source": "model_error"}, []
