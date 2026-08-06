#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""飞书销售录音批量转写：准备、计费确认、LAS 异步任务、角色归类与安全回写。"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import tencent_flash

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"
CONFIG = ROOT / "config.local.json"
DIALECT_PROMPT = ROOT / "references" / "dialect-to-mandarin.md"
ROLE_SKILL = Path(r"C:\Users\12909\.codex\skills\classify-sales-call-roles")
ROLE_BUILD = ROLE_SKILL / "scripts" / "build_role_profile.py"
ROLE_APPLY = ROLE_SKILL / "scripts" / "apply_role_map.py"
ROLE_AUDIT = ROLE_SKILL / "scripts" / "audit_role_map.py"
ROLE_PROMPT = ROLE_SKILL / "references" / "role-prompt.md"
ALLOWED_ROLES = {"销售", "客户", "旁人"}
ACTIVE_ASR = {"submitted", "pending", "running"}
TERMINAL = {"written", "asr_failed"}
INLINE_SPEAKER = re.compile(r"\[spk(\d+)\]\[(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\]")
LINE_RE = re.compile(
    r"^\[(?P<start>\d\d:\d\d(?:\:\d\d)?\.\d{3})–"
    r"(?P<end>\d\d:\d\d(?:\:\d\d)?\.\d{3})\] "
    r"(?P<role>销售|客户|旁人)：(?P<text>.+)$"
)
STATE_LOCK = threading.Lock()


class SubmitUncertain(RuntimeError):
    """Submit 请求已经开始，但无法确认服务端是否创建了任务。"""


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} 顶层必须是 JSON 对象")
    return value


def load_config() -> dict[str, Any]:
    if not CONFIG.exists():
        raise RuntimeError(f"缺少配置文件: {CONFIG}")
    return load_json(CONFIG)


def merged_linked_config(path: Path, seen: set[Path] | None = None) -> dict[str, Any]:
    seen = seen or set()
    path = path.resolve()
    if path in seen:
        raise RuntimeError(f"模型配置循环引用: {path}")
    seen.add(path)
    cfg = load_json(path)
    linked = cfg.get("llm_config_path")
    inherited: dict[str, Any] = {}
    if linked:
        linked_path = Path(str(linked))
        if not linked_path.is_absolute():
            linked_path = path.parent / linked_path
        if linked_path.exists():
            inherited = merged_linked_config(linked_path, seen)
    inherited.update({key: value for key, value in cfg.items() if value not in (None, "")})
    return inherited


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def append_event(run_dir: Path, event: dict[str, Any]) -> None:
    item = {"at": now(), **event}
    with STATE_LOCK:
        with (run_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")


def save_state(run_dir: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = now()
    with STATE_LOCK:
        atomic_json(run_dir / "state.json", state)


def resolve_lark() -> str:
    found = shutil.which("lark-cli") or "lark-cli"
    if found.lower().endswith((".cmd", ".bat")):
        try:
            match = re.search(
                r'"([^"]*lark-cli\.exe)"',
                Path(found).read_text(encoding="utf-8", errors="replace"),
            )
            if match and Path(os.path.expandvars(match.group(1))).exists():
                return os.path.expandvars(match.group(1))
        except OSError:
            pass
    return found


LARK = resolve_lark()


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


def lark(*args: str, retries: int = 3, cwd: Path | None = None) -> dict[str, Any]:
    command = [LARK, *args, "--as", "user", "--format", "json"]
    last = ""
    for attempt in range(retries):
        proc = subprocess.run(
            command,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(cwd) if cwd else None,
        )
        try:
            response = json_from_output(proc.stdout or proc.stderr, "lark-cli")
        except Exception as exc:
            last = f"{exc}; stderr={proc.stderr[:300]}"
            time.sleep(attempt + 1)
            continue
        if not response.get("ok"):
            raise RuntimeError(json.dumps(response.get("error"), ensure_ascii=False))
        return response["data"]
    raise RuntimeError(last or "lark-cli 连续失败")


def rows_of(data: dict[str, Any]) -> list[dict[str, Any]]:
    fields = data.get("fields", [])
    ids = data.get("record_id_list", [])
    rows = []
    for index, values in enumerate(data.get("data", [])):
        row = dict(zip(fields, values))
        row["_record_id"] = ids[index] if index < len(ids) else None
        rows.append(row)
    return rows


def is_blank(value: Any) -> bool:
    return value is None or value == "" or value == []


def profile_of(cfg: dict[str, Any], name: str) -> dict[str, Any]:
    try:
        return cfg["profiles"][name]
    except KeyError as exc:
        raise RuntimeError(f"配置中没有 profile={name}") from exc


def validate_schema(profile: dict[str, Any]) -> None:
    data = lark(
        "base", "+field-list",
        "--base-token", profile["base_token"],
        "--table-id", profile["table_id"],
    )
    fields = {item["name"]: item for item in data.get("fields", [])}
    required = {
        profile["attachment_field"],
        profile["transcript_field"],
        *profile.get("identity_fields", []),
    }
    missing = sorted(required - set(fields))
    if missing:
        raise RuntimeError(f"飞书缺少字段: {missing}")
    if fields[profile["attachment_field"]].get("type") != "attachment":
        raise RuntimeError(f"{profile['attachment_field']} 必须是附件字段")
    if fields[profile["transcript_field"]].get("type") != "text":
        raise RuntimeError(f"{profile['transcript_field']} 必须是文本字段")


def fetch_rows(
    profile: dict[str, Any],
    view: str,
    start: int,
    count: int,
) -> list[dict[str, Any]]:
    names = [
        profile["attachment_field"],
        profile["transcript_field"],
        *profile.get("identity_fields", []),
    ]
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        args = [
            "base", "+record-list",
            "--base-token", profile["base_token"],
            "--table-id", profile["table_id"],
            "--view-id", view,
            "--offset", str(offset),
            "--limit", "200",
        ]
        for name in names:
            args.extend(["--field-id", name])
        data = lark(*args)
        page = rows_of(data)
        rows.extend(page)
        if not data.get("has_more") or not page:
            break
        offset += len(page)
    sliced = rows[max(0, start - 1) :]
    return sliced[:count] if count > 0 else sliced


def pending_rows(
    rows: list[dict[str, Any]],
    profile: dict[str, Any],
    overwrite: bool,
    record_ids: set[str] | None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    scoped = [
        row for row in rows
        if not record_ids or row.get("_record_id") in record_ids
    ]
    pending: list[dict[str, Any]] = []
    stats = {"records": len(scoped), "no_audio": 0, "already_written": 0, "multiple_audio": 0}
    for row in scoped:
        attachments = row.get(profile["attachment_field"])
        if not isinstance(attachments, list) or not attachments:
            stats["no_audio"] += 1
            continue
        if len(attachments) != 1:
            stats["multiple_audio"] += 1
            continue
        if not overwrite and not is_blank(row.get(profile["transcript_field"])):
            stats["already_written"] += 1
            continue
        pending.append(row)
    stats["pending"] = len(pending)
    return pending, stats


def safe_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return cleaned[:120] or "audio.bin"


def local_audio_name(original_name: str) -> str:
    suffix = Path(original_name).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,8}", suffix):
        suffix = ".bin"
    return "audio" + suffix


def download_attachment(
    profile: dict[str, Any],
    row: dict[str, Any],
    record_dir: Path,
) -> dict[str, Any]:
    item = row[profile["attachment_field"]][0]
    original_name = safe_name(str(item.get("name") or "audio.bin"))
    name = local_audio_name(original_name)
    path = record_dir / name
    record_dir.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        lark(
            "base", "+record-download-attachment",
            "--base-token", profile["base_token"],
            "--table-id", profile["table_id"],
            "--record-id", row["_record_id"],
            "--file-token", str(item["file_token"]),
            "--output", name,
            "--overwrite",
            cwd=record_dir,
        )
    if not path.exists() or path.stat().st_size <= 0:
        raise RuntimeError("附件下载后文件不存在或为空")
    duration = media_duration(path)
    return {
        "record_id": row["_record_id"],
        "status": "prepared",
        "attachment_name": original_name,
        "file_token": item["file_token"],
        "audio_path": str(path.resolve()),
        "audio_format": path.suffix.lower().lstrip("."),
        "audio_bytes": path.stat().st_size,
        "duration_seconds": round(duration, 3),
        "identity": {
            field: row.get(field)
            for field in profile.get("identity_fields", [])
            if not is_blank(row.get(field))
        },
        "error": None,
        "task_id": None,
        "created_at": now(),
    }


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as audio:
        return audio.getnframes() / float(audio.getframerate())


def mp4_duration(path: Path) -> float:
    data = path.read_bytes()
    index = data.find(b"mvhd")
    if index < 0:
        raise ValueError("M4A/MP4 中找不到 mvhd")
    content = index + 4
    if len(data) < content + 32:
        raise ValueError("mvhd 数据不完整")
    version = data[content]
    if version == 0:
        timescale = struct.unpack(">I", data[content + 12 : content + 16])[0]
        duration = struct.unpack(">I", data[content + 16 : content + 20])[0]
    elif version == 1:
        timescale = struct.unpack(">I", data[content + 20 : content + 24])[0]
        duration = struct.unpack(">Q", data[content + 24 : content + 32])[0]
    else:
        raise ValueError(f"不支持的 mvhd 版本: {version}")
    if not timescale:
        raise ValueError("M4A/MP4 timescale 为 0")
    return duration / float(timescale)


def synchsafe(value: bytes) -> int:
    return sum((byte & 0x7F) << shift for byte, shift in zip(value, (21, 14, 7, 0)))


def mp3_header(data: bytes, pos: int) -> tuple[int, int, int] | None:
    if pos + 4 > len(data):
        return None
    value = int.from_bytes(data[pos : pos + 4], "big")
    if (value >> 21) & 0x7FF != 0x7FF:
        return None
    version_id = (value >> 19) & 0x3
    layer_id = (value >> 17) & 0x3
    bitrate_index = (value >> 12) & 0xF
    sample_index = (value >> 10) & 0x3
    padding = (value >> 9) & 0x1
    if version_id == 1 or layer_id != 1 or bitrate_index in (0, 15) or sample_index == 3:
        return None
    version = 1 if version_id == 3 else (2 if version_id == 2 else 2.5)
    rates = {1: (44100, 48000, 32000), 2: (22050, 24000, 16000), 2.5: (11025, 12000, 8000)}
    bitrates = {
        1: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
        2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
    }
    sample_rate = rates[version][sample_index]
    bitrate = bitrates[1 if version == 1 else 2][bitrate_index] * 1000
    samples = 1152 if version == 1 else 576
    frame_size = int((144 if version == 1 else 72) * bitrate / sample_rate + padding)
    return frame_size, samples, sample_rate


def mp3_duration(path: Path) -> float:
    data = path.read_bytes()
    pos = 0
    if data[:3] == b"ID3" and len(data) >= 10:
        pos = 10 + synchsafe(data[6:10])
    duration = 0.0
    frames = 0
    while pos + 4 <= len(data):
        header = mp3_header(data, pos)
        if not header:
            pos += 1
            continue
        size, samples, rate = header
        duration += samples / rate
        frames += 1
        pos += max(1, size)
    if not frames:
        raise ValueError("MP3 中找不到有效音频帧")
    return duration


def media_duration(path: Path) -> float:
    suffix = path.suffix.lower()
    # 优先使用经过验证的媒体元数据解析器，尤其避免 M4A 中多个时间轴造成误判。
    try:
        from mutagen import File as mutagen_file
    except ImportError:
        mutagen_file = None
    if mutagen_file is not None and suffix in {".wav", ".wave", ".m4a", ".mp4", ".mov", ".aac", ".mp3"}:
        media = mutagen_file(path)
        value = float(media.info.length) if media is not None and getattr(media, "info", None) else 0.0
    elif suffix in {".wav", ".wave"}:
        value = wav_duration(path)
    elif suffix in {".m4a", ".mp4", ".mov", ".aac"}:
        value = mp4_duration(path)
    elif suffix == ".mp3":
        value = mp3_duration(path)
    else:
        raise ValueError(f"暂不支持本地估时格式: {suffix}")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"无效音频时长: {value}")
    return value


def money(value: float) -> str:
    return str(Decimal(str(value)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def asr_provider(cfg: dict[str, Any]) -> str:
    value = str(cfg.get("asr_provider") or "volc_las")
    if value not in {"volc_las", "tencent_flash"}:
        raise RuntimeError(f"不支持的 ASR provider: {value}")
    return value


def provider_config(cfg: dict[str, Any], provider: str | None = None) -> dict[str, Any]:
    name = provider or asr_provider(cfg)
    if name == "tencent_flash":
        value = cfg.get("tencent_flash")
        if not isinstance(value, dict):
            raise RuntimeError("配置缺少 tencent_flash 对象")
        return {**value, "hotwords": value.get("hotwords") or cfg.get("hotwords") or []}
    return cfg


def prepare(
    cfg: dict[str, Any],
    profile_name: str,
    view: str,
    start: int,
    count: int,
    workers: int,
    overwrite: bool,
    record_ids: set[str] | None,
    asr_language: str,
    output_language: str,
) -> Path:
    provider = asr_provider(cfg)
    provider_cfg = provider_config(cfg, provider)
    profile = profile_of(cfg, profile_name)
    validate_schema(profile)
    rows = fetch_rows(profile, view, 1, 0) if record_ids else fetch_rows(profile, view, start, count)
    selected, stats = pending_rows(rows, profile, overwrite, record_ids)
    run_dir = RUNS / datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=False)
    state: dict[str, Any] = {
        "schema_version": 1,
        "created_at": now(),
        "updated_at": now(),
        "profile_name": profile_name,
        "profile": profile,
        "view_id": view,
        "overwrite": overwrite,
        "asr_provider": provider,
        "operator_id": cfg["operator_id"],
        "operator_version": cfg["operator_version"],
        "region": cfg.get("region", "cn-beijing"),
        "asr_language": asr_language,
        "output_language": output_language,
        "record_ids": sorted(record_ids or []),
        "rate_yuan_per_hour": provider_cfg["rate_yuan_per_hour"],
        "selection_stats": stats,
        "records": {},
    }
    save_state(run_dir, state)

    def job(row: dict[str, Any]) -> dict[str, Any]:
        rid = row["_record_id"]
        try:
            return download_attachment(profile, row, run_dir / "records" / rid)
        except Exception as exc:
            return {
                "record_id": rid,
                "status": "prepare_error",
                "identity": {field: row.get(field) for field in profile.get("identity_fields", [])},
                "error": str(exc),
                "created_at": now(),
            }

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(job, row) for row in selected]
        for future in as_completed(futures):
            record = future.result()
            state["records"][record["record_id"]] = record
            append_event(run_dir, {"record_id": record["record_id"], "status": record["status"]})
            save_state(run_dir, state)

    prepared = [x for x in state["records"].values() if x["status"] == "prepared"]
    total_seconds = sum(float(x["duration_seconds"]) for x in prepared)
    estimate = total_seconds / 3600 * float(provider_cfg["rate_yuan_per_hour"])
    state["duration_seconds"] = round(total_seconds, 3)
    state["estimated_yuan"] = money(estimate)
    state["prepared_count"] = len(prepared)
    state["prepare_error_count"] = sum(x["status"] == "prepare_error" for x in state["records"].values())
    save_state(run_dir, state)
    return run_dir


def las_bin(cfg: dict[str, Any]) -> str:
    value = os.environ.get("LASUTIL_PATH") or cfg.get("lasutil_path") or shutil.which("lasutil")
    if not value or not Path(value).exists():
        raise RuntimeError("找不到 lasutil；请设置 LASUTIL_PATH 或配置 lasutil_path")
    return str(value)


def las_command(cfg: dict[str, Any], *args: str) -> dict[str, Any]:
    if not os.environ.get("LAS_API_KEY"):
        raise RuntimeError("缺少 LAS_API_KEY 环境变量")
    env = os.environ.copy()
    env["LAS_REGION"] = str(cfg.get("region", "cn-beijing"))
    proc = subprocess.run(
        [las_bin(cfg), *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if proc.returncode:
        raise RuntimeError(f"lasutil 失败({proc.returncode}): {proc.stderr[:500] or proc.stdout[:500]}")
    return json_from_output(proc.stdout, "lasutil")


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


def submit_one(cfg: dict[str, Any], record: dict[str, Any], record_dir: Path) -> dict[str, Any]:
    if record.get("task_id"):
        return {"status": record["status"], "task_id": record["task_id"]}
    upload = las_command(cfg, "file-upload", record["audio_path"])
    url = deep_find(upload, "presigned_url")
    if not isinstance(url, str) or not url.startswith("http"):
        raise RuntimeError("上传成功但没有 presigned_url")
    audio = {
        "url": url,
        "format": record["audio_format"],
    }
    asr_language = str(cfg.get("asr_language", "auto"))
    if asr_language != "auto":
        audio["language"] = asr_language
    payload = {
        "audio": audio,
        "request": {
            "model_name": "bigmodel",
            "enable_itn": True,
            "enable_punc": True,
            "enable_ddc": False,
            "enable_speaker_info": True,
            "show_utterances": True,
            "show_speech_rate": True,
            "show_volume": True,
            "enable_lid": True,
            "enable_emotion_detection": True,
            "enable_gender_detection": True,
            "enable_denoise": True,
        },
    }
    archived_payload = json.loads(json.dumps(payload, ensure_ascii=False))
    archived_payload["audio"]["url"] = "<ephemeral-presigned-url-redacted>"
    atomic_json(record_dir / "request.json", archived_payload)
    try:
        response = las_command(
            cfg,
            "submit",
            "--version", str(cfg["operator_version"]),
            str(cfg["operator_id"]),
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
    except Exception as exc:
        raise SubmitUncertain(str(exc)) from exc
    atomic_json(record_dir / "submit.json", response)
    task_id = deep_find(response, "task_id")
    status = str(deep_find(response, "task_status") or "PENDING").upper()
    if not task_id:
        raise RuntimeError("Submit 未返回 Task ID")
    return {"task_id": str(task_id), "status": status.lower(), "submitted_at": now()}


def submit_run(cfg: dict[str, Any], run_dir: Path, confirm: str, workers: int) -> dict[str, Any]:
    state = load_json(run_dir / "state.json")
    provider = str(state.get("asr_provider") or asr_provider(cfg))
    run_cfg = {
        **cfg,
        "asr_language": state.get("asr_language", "zh"),
        "output_language": state.get("output_language", "source"),
    }
    expected = str(state.get("estimated_yuan"))
    if money(float(confirm)) != expected:
        raise RuntimeError(f"计费确认不匹配：需要 {expected} 元，收到 {money(float(confirm))} 元")
    if provider == "tencent_flash":
        return tencent_submit_run(run_cfg, state, run_dir, workers)
    if not os.environ.get("LAS_API_KEY"):
        raise RuntimeError("缺少 LAS_API_KEY 环境变量；尚未提交任何任务")
    # 进程若在 Submit 返回后、state.json 落盘前中断，优先从逐条产物恢复 Task ID。
    for record in state["records"].values():
        if record.get("task_id"):
            continue
        submit_path = run_dir / "records" / record["record_id"] / "submit.json"
        if not submit_path.exists():
            continue
        response = load_json(submit_path)
        task_id = deep_find(response, "task_id")
        if task_id:
            record.update({
                "task_id": str(task_id),
                "status": str(deep_find(response, "task_status") or "PENDING").lower(),
                "recovered_from_submit_artifact": True,
            })
    save_state(run_dir, state)
    candidates = [
        record for record in state["records"].values()
        if record.get("status") in {"prepared", "submit_error"} and not record.get("task_id")
    ]

    def job(record: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        rid = record["record_id"]
        try:
            result = submit_one(run_cfg, record, run_dir / "records" / rid)
        except SubmitUncertain as exc:
            result = {
                "status": "submit_uncertain",
                "error": str(exc),
                "manual_action": "先从 LAS 控制台核对是否已生成 Task ID，禁止直接重提",
            }
        except Exception as exc:
            result = {"status": "submit_error", "error": str(exc)}
        return rid, result

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(job, record) for record in candidates]
        for future in as_completed(futures):
            rid, result = future.result()
            state["records"][rid].update(result)
            append_event(run_dir, {"record_id": rid, **result})
            save_state(run_dir, state)
    return state


def tencent_submit_run(
    cfg: dict[str, Any],
    state: dict[str, Any],
    run_dir: Path,
    workers: int,
) -> dict[str, Any]:
    flash_cfg = provider_config(cfg, "tencent_flash")
    tencent_flash.credentials(flash_cfg)
    candidates = [
        record
        for record in state["records"].values()
        if record.get("status") in {"prepared", "asr_error"}
    ]
    max_concurrency = max(1, int(flash_cfg.get("max_concurrency", 5)))
    actual_workers = min(max(1, workers), max_concurrency)

    def job(record: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        rid = record["record_id"]
        record_dir = run_dir / "records" / rid
        raw_path = record_dir / "tencent-response.json"
        try:
            if raw_path.exists():
                raw = load_json(raw_path)
                recovered_from_raw = True
            else:
                request_audio = tencent_flash.standardize_audio(
                    Path(record["audio_path"]),
                    flash_cfg,
                )
                raw = tencent_flash.recognize_file(
                    request_audio,
                    flash_cfg,
                    timestamp=int(time.time()),
                )
                atomic_json(raw_path, raw)
                recovered_from_raw = False
        except tencent_flash.RecognitionUncertain as exc:
            return rid, {
                "status": "asr_uncertain",
                "error": str(exc),
                "manual_action": "先在腾讯云账单或调用记录核对 request_id/计费，再决定是否重试",
            }
        except Exception as exc:
            return rid, {"status": "asr_error", "error": str(exc)}
        try:
            response = tencent_flash.normalize_response(raw)
            speakers = collect_speakers(response)
            if flash_cfg.get("require_two_speakers", True) and len(speakers) < 2:
                atomic_json(record_dir / "result.json", response)
                return rid, {
                    "status": "role_error",
                    "error": "腾讯 ASR 仅返回一个 speaker，未通过销售/客户角色写回门禁",
                    "provider_request_id": raw.get("request_id"),
                    "speaker_count": len(speakers),
                }
            result = process_completed(cfg, run_dir, record, response)
            result.update({
                "provider_request_id": raw.get("request_id"),
                "speaker_count": len(speakers),
                "recovered_from_raw": recovered_from_raw,
            })
            return rid, result
        except Exception as exc:
            return rid, {
                "status": "result_error",
                "error": str(exc),
                "provider_request_id": raw.get("request_id"),
                "manual_action": "使用已保存的 tencent-response.json 修复本地转换或角色层，禁止重跑 ASR",
            }

    with ThreadPoolExecutor(max_workers=actual_workers) as pool:
        futures = [pool.submit(job, record) for record in candidates]
        for future in as_completed(futures):
            rid, result = future.result()
            record = state["records"][rid]
            if result.get("status") == "role_labeled":
                try:
                    labeled = validate_transcript(Path(result["transcript_path"]))
                    write_transcript(
                        state["profile"], rid, labeled, bool(state.get("overwrite"))
                    )
                except Exception as exc:
                    result.update({"status": "write_error", "error": str(exc)})
                else:
                    result.update({"status": "written", "error": None, "written_at": now()})
            record.update(result)
            append_event(run_dir, {"record_id": rid, **result})
            save_state(run_dir, state)
    state["effective_asr_workers"] = actual_workers
    save_state(run_dir, state)
    return state


def poll_one(cfg: dict[str, Any], task_id: str) -> dict[str, Any]:
    return las_command(
        cfg,
        "poll",
        "--version", str(cfg["operator_version"]),
        str(cfg["operator_id"]),
        task_id,
    )


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


def extract_json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型输出没有 JSON 对象")
    value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("模型 JSON 顶层不是对象")
    return value


def llm_call(cfg: dict[str, Any], system: str, user: str) -> str:
    linked = Path(str(cfg["role_llm_config_path"]))
    llm = merged_linked_config(linked)
    endpoint = os.environ.get("LLM_API_URL") or llm.get("api_url")
    key = os.environ.get("ARK_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or llm.get("api_key")
    model = llm.get("model") or "deepseek-v4-flash"
    if not endpoint or not key:
        raise RuntimeError("角色模型缺少 api_url 或 API Key")
    endpoint = str(endpoint).rstrip("/")
    if not endpoint.endswith("/chat/completions"):
        endpoint += "/chat/completions"
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    last: Any = None
    for attempt in range(3):
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                value = json.loads(response.read().decode("utf-8"))
            return value["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            if exc.code == 400 and "response_format" in detail:
                body.pop("response_format", None)
                continue
            last = f"HTTP {exc.code}: {detail}"
        except Exception as exc:
            last = exc
        time.sleep((attempt + 1) * 2)
    raise RuntimeError(f"角色模型连续失败: {last}")


def result_container(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("result"), dict):
        return data["result"]
    if isinstance(payload.get("result"), dict):
        return payload["result"]
    return payload


def validate_normalized_items(
    source_items: list[dict[str, str]],
    value: dict[str, Any],
) -> dict[str, str]:
    raw_items = value.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("普通话转换结果缺少 items 数组")
    source = {item["id"]: item["text"] for item in source_items}
    converted: dict[str, str] = {}
    for item in raw_items:
        if not isinstance(item, dict):
            raise ValueError("普通话转换 items 中存在非对象")
        item_id = str(item.get("id") or "")
        text = item.get("text")
        if item_id not in source:
            raise ValueError(f"普通话转换返回未知 ID: {item_id}")
        if item_id in converted:
            raise ValueError(f"普通话转换返回重复 ID: {item_id}")
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"普通话转换结果为空: {item_id}")
        if INLINE_SPEAKER.findall(source[item_id]) != INLINE_SPEAKER.findall(text):
            raise ValueError(f"普通话转换改动了 speaker/时间标签: {item_id}")
        converted[item_id] = text.strip()
    missing = sorted(set(source) - set(converted))
    if missing:
        raise ValueError(f"普通话转换缺少 ID: {missing}")
    return converted


def normalize_dialect_to_mandarin(
    cfg: dict[str, Any],
    payload: dict[str, Any],
    record_dir: Path,
) -> Path:
    normalized = copy.deepcopy(payload)
    utterances = utterances_from(normalized)
    indexed = [
        {"id": f"U{index:04d}", "text": str(item.get("text") or "")}
        for index, item in enumerate(utterances, 1)
    ]
    if any(not item["text"].strip() for item in indexed):
        raise ValueError("方言 ASR 中存在空 utterance，无法转换普通话")

    chunks: list[list[dict[str, str]]] = []
    current: list[dict[str, str]] = []
    current_chars = 0
    for item in indexed:
        item_chars = len(item["text"])
        if current and (len(current) >= 100 or current_chars + item_chars > 12000):
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(item)
        current_chars += item_chars
    if current:
        chunks.append(current)

    converted: dict[str, str] = {}
    prompt = DIALECT_PROMPT.read_text(encoding="utf-8")
    for chunk in chunks:
        raw = llm_call(
            cfg,
            prompt,
            json.dumps({"items": chunk}, ensure_ascii=False),
        )
        converted.update(validate_normalized_items(chunk, extract_json_object(raw)))

    audit: list[dict[str, str]] = []
    for source, utterance in zip(indexed, utterances):
        target = converted[source["id"]]
        utterance["text"] = target
        audit.append({"id": source["id"], "source": source["text"], "mandarin": target})
    container = result_container(normalized)
    container["text"] = "".join(str(item.get("text") or "") for item in utterances)
    output_path = record_dir / "mandarin-result.json"
    atomic_json(output_path, normalized)
    atomic_json(record_dir / "dialect-to-mandarin.json", {"items": audit})
    return output_path


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


def subprocess_checked(command: list[str], label: str) -> str:
    proc = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace")
    if proc.returncode:
        raise RuntimeError(f"{label} 失败: {proc.stderr[:500] or proc.stdout[:500]}")
    return proc.stdout


def run_role_audit(
    result_path: Path,
    role_map_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    subprocess_checked(
        [
            sys.executable,
            str(ROLE_AUDIT),
            str(result_path),
            "--role-map",
            str(role_map_path),
            "--output",
            str(output_path),
        ],
        "审计角色映射",
    )
    return load_json(output_path)


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


def label_roles(
    cfg: dict[str, Any],
    record_dir: Path,
    result_path: Path,
) -> tuple[str, str, dict[str, Any]]:
    profile_path = record_dir / "role-profile.md"
    role_map_path = record_dir / "role-map.json"
    role_audit_path = record_dir / "role-audit.json"
    transcript_path = record_dir / "labeled-transcript.txt"
    subprocess_checked(
        [sys.executable, str(ROLE_BUILD), str(result_path), "--output", str(profile_path)],
        "生成 speaker 画像",
    )
    profile_text = profile_path.read_text(encoding="utf-8")
    speakers = collect_speakers(load_json(result_path))
    source = "llm"
    try:
        raw = llm_call(
            cfg,
            ROLE_PROMPT.read_text(encoding="utf-8"),
            "请根据以下 speaker 画像输出 role-map JSON：\n\n" + profile_text,
        )
        role_map = validate_role_map(extract_json_object(raw), speakers)
    except Exception as exc:
        source = f"deterministic_fallback:{type(exc).__name__}"
        role_map = validate_role_map(fallback_role_map(speakers, profile_text), speakers)
    atomic_json(record_dir / "role-map.initial.json", role_map)

    current_map = role_map
    current_audit: dict[str, Any] | None = None
    for repair_attempt in range(2):
        atomic_json(role_map_path, current_map)
        audit_candidate_path = record_dir / (
            "role-audit.initial.json"
            if repair_attempt == 0
            else f"role-audit.repair-{repair_attempt}.json"
        )
        current_audit = run_role_audit(result_path, role_map_path, audit_candidate_path)
        if not current_audit.get("summary", {}).get("needs_repair"):
            break
        try:
            repair_raw = llm_call(
                cfg,
                ROLE_PROMPT.read_text(encoding="utf-8"),
                (
                    "请修正当前 role-map，只处理审计指出的连续高置信冲突。"
                    "输出完整 role-map JSON。\n\n"
                    "Speaker profile:\n"
                    + profile_text
                    + "\n\nCurrent role-map:\n"
                    + json.dumps(current_map, ensure_ascii=False)
                    + "\n\nRole-map audit:\n"
                    + json.dumps(current_audit, ensure_ascii=False)
                ),
            )
            repaired = validate_role_map(extract_json_object(repair_raw), speakers)
        except Exception:
            break
        repaired_map_path = record_dir / f"role-map.repair-{repair_attempt + 1}.json"
        atomic_json(repaired_map_path, repaired)
        repaired_audit_path = record_dir / f"role-audit.repair-{repair_attempt + 1}.json"
        repaired_audit = run_role_audit(result_path, repaired_map_path, repaired_audit_path)
        before = int(current_audit.get("summary", {}).get("conflict_windows") or 0)
        after = int(repaired_audit.get("summary", {}).get("conflict_windows") or 0)
        if after >= before:
            break
        current_map = repaired
        current_audit = repaired_audit
        source += f"+audit_repair_{repair_attempt + 1}"

    if current_audit is None:
        raise RuntimeError("角色映射审计没有生成结果")
    atomic_json(role_map_path, current_map)
    atomic_json(role_audit_path, current_audit)
    if current_audit.get("summary", {}).get("severe"):
        raise ValueError(
            "角色映射审计仍有严重语义冲突，已停止写回；"
            f"conflict_windows={current_audit.get('summary', {}).get('conflict_windows')}"
        )
    subprocess_checked(
        [
            sys.executable, str(ROLE_APPLY), str(result_path),
            "--role-map", str(role_map_path),
            "--output", str(transcript_path),
        ],
        "应用角色映射",
    )
    return validate_transcript(transcript_path), source, current_audit.get("summary", {})


def read_record_field(profile: dict[str, Any], record_id: str) -> Any:
    data = lark(
        "base", "+record-get",
        "--base-token", profile["base_token"],
        "--table-id", profile["table_id"],
        "--record-id", record_id,
        "--field-id", profile["transcript_field"],
    )
    rows = rows_of(data)
    if len(rows) != 1:
        raise RuntimeError("飞书复读未返回唯一记录")
    return rows[0].get(profile["transcript_field"])


def write_transcript(profile: dict[str, Any], record_id: str, text: str, overwrite: bool) -> None:
    current = read_record_field(profile, record_id)
    if current == text:
        return
    if not overwrite and not is_blank(current):
        raise RuntimeError("目标字段已经有内容，未指定 overwrite")
    data = lark(
        "base", "+record-upsert",
        "--base-token", profile["base_token"],
        "--table-id", profile["table_id"],
        "--record-id", record_id,
        "--json", json.dumps({profile["transcript_field"]: text}, ensure_ascii=False),
    )
    if not data.get("updated"):
        raise RuntimeError("飞书没有确认更新")
    for attempt in range(3):
        if read_record_field(profile, record_id) == text:
            return
        time.sleep(attempt + 1)
    raise RuntimeError("飞书写后复读与本地文本不一致")


def process_completed(
    cfg: dict[str, Any],
    run_dir: Path,
    record: dict[str, Any],
    response: dict[str, Any],
) -> dict[str, Any]:
    rid = record["record_id"]
    record_dir = run_dir / "records" / rid
    result_path = record_dir / "result.json"
    atomic_json(result_path, response)
    code = str(deep_find(response, "business_code") or "")
    text = deep_find(response, "text")
    utterances = utterances_from(response)
    if code not in {"0", ""} or not isinstance(text, str) or not text.strip() or not utterances:
        raise RuntimeError(f"COMPLETED 结果验收失败: business_code={code}, utterances={len(utterances)}")
    role_input_path = result_path
    if (
        cfg.get("asr_language") in {"auto", "cant", "yue-CN"}
        and cfg.get("output_language") == "zh-CN"
    ):
        role_input_path = normalize_dialect_to_mandarin(cfg, response, record_dir)
    labeled, source, role_audit = label_roles(cfg, record_dir, role_input_path)
    result = {
        "status": "role_labeled",
        "completed_at": now(),
        "asr_language": cfg.get("asr_language", "zh"),
        "output_language": cfg.get("output_language", "source"),
        "normalized_result_path": (
            str(role_input_path.resolve()) if role_input_path != result_path else None
        ),
        "role_map_source": source,
        "role_audit": role_audit,
        "utterance_count": len(utterances),
        "transcript_path": str((record_dir / "labeled-transcript.txt").resolve()),
        "error": None,
    }
    return result


def poll_run(cfg: dict[str, Any], run_dir: Path, workers: int, dry_run: bool) -> dict[str, Any]:
    state = load_json(run_dir / "state.json")
    run_cfg = {
        **cfg,
        "asr_language": state.get("asr_language", "zh"),
        "output_language": state.get("output_language", "source"),
    }
    # 已经有本地角色转写时只重试写回，不再查询或重跑 ASR。
    if not dry_run:
        for record in state["records"].values():
            if record.get("status") not in {"role_labeled", "write_error"}:
                continue
            transcript_path = Path(str(record.get("transcript_path") or ""))
            if not transcript_path.exists():
                continue
            try:
                labeled = validate_transcript(transcript_path)
                write_transcript(
                    state["profile"], record["record_id"], labeled, bool(state.get("overwrite"))
                )
            except Exception as exc:
                record.update({"status": "write_error", "error": str(exc)})
            else:
                record.update({"status": "written", "error": None, "written_at": now()})
            append_event(run_dir, {"record_id": record["record_id"], "status": record["status"]})
            save_state(run_dir, state)

    candidates = [
        record for record in state["records"].values()
        if record.get("task_id")
        and record.get("status") not in TERMINAL | {"role_labeled", "write_error"}
    ]

    def job(record: dict[str, Any]) -> tuple[str, dict[str, Any] | None, dict[str, Any]]:
        rid = record["record_id"]
        try:
            response = poll_one(run_cfg, record["task_id"])
            status = str(deep_find(response, "task_status") or "UNKNOWN").upper()
            result = {
                "status": status.lower(),
                "last_polled_at": now(),
                "business_code": str(deep_find(response, "business_code") or ""),
                "error": deep_find(response, "error_msg"),
            }
            return rid, response, result
        except Exception as exc:
            return rid, None, {"status": record.get("status", "submitted"), "poll_error": str(exc)}

    polled: list[tuple[str, dict[str, Any] | None, dict[str, Any]]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(job, record) for record in candidates]
        for future in as_completed(futures):
            polled.append(future.result())

    completed = [(rid, response) for rid, response, result in polled if response is not None and result.get("status") == "completed"]
    completed_results: dict[str, dict[str, Any]] = {}

    def role_job(rid: str, response: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        try:
            return rid, process_completed(run_cfg, run_dir, state["records"][rid], response)
        except Exception as exc:
            return rid, {"status": "role_error", "error": str(exc), "last_polled_at": now()}

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(role_job, rid, response) for rid, response in completed]
        for future in as_completed(futures):
            rid, result = future.result()
            completed_results[rid] = result

    # 写同一张飞书表的动作只在此循环发生，因此始终串行。
    for rid, response, poll_result in polled:
        record = state["records"][rid]
        status = poll_result.get("status")
        result = completed_results.get(rid, poll_result)
        if status in {"failed", "timeout"}:
            result["status"] = "asr_failed"
        elif result.get("status") == "role_labeled" and not dry_run:
            try:
                labeled = validate_transcript(Path(result["transcript_path"]))
                write_transcript(
                    state["profile"], rid, labeled, bool(state.get("overwrite"))
                )
            except Exception as exc:
                result.update({"status": "write_error", "error": str(exc)})
            else:
                result.update({"status": "written", "error": None, "written_at": now()})
        record.update(result)
        append_event(run_dir, {"record_id": rid, **result})
    save_state(run_dir, state)
    return state


def relabel_run(
    cfg: dict[str, Any],
    run_dir: Path,
    workers: int,
    dry_run: bool,
    overwrite: bool,
    record_ids: set[str] | None,
) -> dict[str, Any]:
    if not dry_run and not overwrite:
        raise RuntimeError("--relabel-run 正式回写必须显式提供 --overwrite")
    state = load_json(run_dir / "state.json")
    run_cfg = {
        **cfg,
        "asr_language": state.get("asr_language", "zh"),
        "output_language": state.get("output_language", "source"),
    }
    selected = [
        record
        for record in state["records"].values()
        if not record_ids or record["record_id"] in record_ids
    ]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")

    def role_job(record: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        rid = record["record_id"]
        record_dir = run_dir / "records" / rid
        result_path = record_dir / "mandarin-result.json"
        if not result_path.exists():
            result_path = record_dir / "result.json"
        if not result_path.exists():
            return rid, {"status": "role_error", "error": "缺少本地 ASR result.json"}
        output_dir = record_dir
        if dry_run:
            output_dir = record_dir / "role-preview" / stamp
            output_dir.mkdir(parents=True, exist_ok=True)
        else:
            history_dir = record_dir / "role-history" / stamp
            history_dir.mkdir(parents=True, exist_ok=True)
            for name in (
                "role-profile.md",
                "role-map.initial.json",
                "role-map.json",
                "role-audit.json",
                "labeled-transcript.txt",
            ):
                source_path = record_dir / name
                if source_path.exists():
                    shutil.copy2(source_path, history_dir / name)
        try:
            labeled, source, role_audit = label_roles(run_cfg, output_dir, result_path)
            return rid, {
                "status": "role_labeled",
                "role_map_source": source,
                "role_audit": role_audit,
                "transcript_path": str((output_dir / "labeled-transcript.txt").resolve()),
                "_labeled": labeled,
                "_preview_dir": str(output_dir.resolve()) if dry_run else None,
            }
        except Exception as exc:
            return rid, {"status": "role_error", "error": str(exc)}

    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(role_job, record) for record in selected]
        for future in as_completed(futures):
            rid, result = future.result()
            results[rid] = result

    for record in selected:
        rid = record["record_id"]
        result = results[rid]
        if result.get("status") == "role_labeled" and not dry_run:
            try:
                write_transcript(state["profile"], rid, result["_labeled"], True)
                result.update({"status": "written", "written_at": now(), "error": None})
            except Exception as exc:
                result.update({"status": "write_error", "error": str(exc)})
        event = {
            "record_id": rid,
            "status": result.get("status"),
            "role_map_source": result.get("role_map_source"),
            "role_audit": result.get("role_audit"),
            "preview_dir": result.get("_preview_dir"),
            "error": result.get("error"),
        }
        append_event(run_dir, event)
        if not dry_run:
            record.update({
                key: value
                for key, value in result.items()
                if not key.startswith("_")
            })
            save_state(run_dir, state)

    if dry_run:
        state = {
            **state,
            "relabel_preview": {
                rid: {
                    key: value
                    for key, value in result.items()
                    if not key.startswith("_")
                }
                for rid, result in results.items()
            },
        }
    return state


def print_summary(state: dict[str, Any], run_dir: Path | None = None) -> None:
    counts: dict[str, int] = {}
    for record in state.get("records", {}).values():
        status = record.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    print(json.dumps({
        "run_dir": str(run_dir.resolve()) if run_dir else None,
        "duration_seconds": state.get("duration_seconds"),
        "estimated_yuan": state.get("estimated_yuan"),
        "status_counts": counts,
    }, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--preflight", action="store_true")
    action.add_argument("--prepare", action="store_true")
    action.add_argument("--submit-run", type=Path)
    action.add_argument("--poll-run", type=Path)
    action.add_argument("--relabel-run", type=Path)
    parser.add_argument("--profile", default="default")
    parser.add_argument("--view")
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--count", type=int, default=0)
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--record-id")
    target.add_argument("--record-ids", help="逗号分隔的多个record_id；与start/count不能混用")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--confirm-yuan")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--asr-language",
        default="auto",
        choices=("auto", "zh-CN", "yue-CN", "zh", "cant"),
    )
    parser.add_argument("--output-language", default="source", choices=("source", "zh-CN"))
    args = parser.parse_args()
    record_ids = {
        item.strip() for item in str(args.record_ids or "").split(",") if item.strip()
    }
    if args.record_id:
        record_ids = {args.record_id}
    if record_ids and (args.start != 1 or args.count != 0):
        parser.error("--record-id/--record-ids 与 --start/--count 不能混用")

    cfg = load_config()
    if args.relabel_run:
        run_dir = args.relabel_run.resolve()
        state = relabel_run(
            cfg,
            run_dir,
            args.workers,
            args.dry_run,
            args.overwrite,
            record_ids or None,
        )
        if args.dry_run:
            print(json.dumps({
                "run_dir": str(run_dir),
                "preview": state.get("relabel_preview", {}),
            }, ensure_ascii=False, indent=2))
        else:
            print_summary(state, run_dir)
        return 0
    if args.submit_run:
        if args.confirm_yuan is None:
            parser.error("--submit-run 必须同时提供 --confirm-yuan")
        run_dir = args.submit_run.resolve()
        state = submit_run(cfg, run_dir, args.confirm_yuan, args.workers)
        print_summary(state, run_dir)
        return 0
    if args.poll_run:
        run_dir = args.poll_run.resolve()
        state = poll_run(cfg, run_dir, args.workers, args.dry_run)
        print_summary(state, run_dir)
        return 0

    profile = profile_of(cfg, args.profile)
    view = args.view or profile.get("view_id")
    if not view:
        parser.error("必须通过 profile 或 --view 提供视图")
    validate_schema(profile)
    rows = fetch_rows(profile, view, 1, 0) if record_ids else fetch_rows(
        profile, view, args.start, args.count
    )
    selected, stats = pending_rows(rows, profile, args.overwrite, record_ids or None)
    if args.preflight:
        print(json.dumps({
            **stats,
            "record_ids": [row["_record_id"] for row in selected],
            "paid_asr_submitted": False,
        }, ensure_ascii=False, indent=2))
        return 0
    run_dir = prepare(
        cfg, args.profile, view, args.start, args.count,
        args.workers, args.overwrite, record_ids or None,
        args.asr_language, args.output_language,
    )
    state = load_json(run_dir / "state.json")
    print_summary(state, run_dir)
    print("已完成下载和估价，尚未提交任何付费 ASR 任务。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
