#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""销售录音无状态分析流水线：结构化模型输出 + 代码硬校验 + 风险复核 + 飞书回填。"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import knowledge as kb

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
PROMPT_DIR = ROOT / "references" / "prompts"
COMMON_PROMPT = (PROMPT_DIR / "common.md").read_text(encoding="utf-8")
PROMPT_FILES = {
    module: PROMPT_DIR / f"{module}.md"
    for module in (
        "explicit-needs", "implicit-needs", "concerns",
        "effectiveness", "quotes", "store-profile", "next-action",
    )
}
# 日常拜访模式专用提示词（拜访阶段 ≠ 首访破冰 时使用）
PROMPT_FILES["store-profile-daily"] = PROMPT_DIR / "store-profile-daily.md"
PROMPT_FILES["next-action-daily"] = PROMPT_DIR / "next-action-daily.md"

MODULE_FIELDS = {
    "explicit-needs": ["显性需求(仅供参考)", "显性需求_原句参考"],
    "implicit-needs": ["隐性需求(仅供参考)", "隐性需求_原句参考"],
    "concerns": ["关心类目打标", "场景化类目归因"],
    "effectiveness": ["合作进展打分评估", "原句参考_合作进展打分评估"],
    "quotes": ["核心金句"],
    "store-profile": ["门店档案", "门店档案-原文证据"],
    "next-action": ["下一步行动策略", "是否达成合作", "上一次行动与这一次行动总结闭环"],
}
ALL_MODULES = list(MODULE_FIELDS)
# 日常拜访模式保留的模块：显性需求、关心类目、门店档案、闭环+下一步行动
DAILY_MODULES = ["explicit-needs", "concerns", "store-profile", "next-action"]
MODULE_ALIASES = {"needs": ["explicit-needs", "implicit-needs"]}
CONCERN_NAMES = [
    "价格敏感", "物流时效", "控价防窜", "培训支持", "售后保障",
    "合作模式", "利润空间", "系统工具", "其他",
]
DIMENSIONS = [
    ("开场目标与议程", 10),
    ("需求挖掘", 20),
    ("问题影响与经济意义", 20),
    ("倾听与异议承接", 15),
    ("方案与证据匹配", 10),
    ("价格与价值建立", 10),
    ("达成合作提问", 5),
    ("下一步承诺", 10),
]
PROFILE_SECTIONS = {
    "basic": "门店基本信息",
    "categories_brands": "主营品类与品牌",
    "business_model": "经营模式",
    "selection_motion": "选品偏好",
    "price_profit": "利润偏好",
    "cooperation_preferences": "合作偏好与排斥项",
}
# 与 store-profile-split 的 SECTION_LABELS 保持一致（self_test 断言）
PROFILE_ONE_LINE_LABEL = "一句话画像"
PROFILE_STATE_TYPES = "稳定档案|当前状态|未确认"
PROFILE_ONE_LINE_RE = re.compile(r"(?m)^一句话画像：(.+)$")
PROFILE_SECTION_RE = re.compile(
    r"(?m)^(" + "|".join(re.escape(label) for label in PROFILE_SECTIONS.values())
    + r")（(?:" + PROFILE_STATE_TYPES + r")）：(.*)$"
)
ROLES = {"销售", "客户", "旁人"}
FORBIDDEN_NEEDS = ("建议验证问题", "待验证点")
INSUFFICIENT_MARKERS = ("不完整", "缺段", "截断", "严重乱码", "无法确认", "无法区分", "ASR损坏")
FORBIDDEN_PROFILE_PHRASES = (
    "极度焦虑", "认真倾听", "情绪激动", "兜底承诺", "保证供货", "免费服务",
)
FUTURE_COMMITMENT_RE = re.compile(
    r"一会儿?|待会儿?|等会儿?|稍后|回头|之后|后续|下次|下周|"
    r"明天|后天|周[一二三四五六日天]|到时候|晚点|随后|过几天|"
    r"完成后|确认后|再联系|再发|发.*给你|给你发|拉你进|约定|安排"
)
WEAK_ASSENT_RE = re.compile(r"^[嗯啊哦好的行可以]+[嗯啊哦呀吧的]*[。！!，,？?]*$")


def load_config() -> dict[str, Any]:
    path = ROOT / "config.local.json"
    cfg = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    linked = cfg.get("llm_config_path")
    if linked and Path(linked).exists():
        shared = json.loads(Path(linked).read_text(encoding="utf-8"))
        for key in ("api_url", "api_key", "model"):
            if not cfg.get(key) and shared.get(key):
                cfg[key] = shared[key]
    return cfg


CFG = load_config()
kb.configure(
    root=CFG.get("knowledge_root"),
    enabled=CFG.get("knowledge_enabled", True),
    mode=CFG.get("knowledge_mode", "manifest"),
)


def api_url() -> str:
    base = os.environ.get("LLM_API_URL") or CFG.get("api_url") or ""
    if not base:
        raise RuntimeError("缺少 api_url")
    base = base.rstrip("/")
    return base if base.endswith("/chat/completions") else base + "/chat/completions"


def api_key() -> str:
    return (
        os.environ.get("ARK_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
        or CFG.get("api_key")
        or ""
    )


def _resolve_lark_bin() -> str:
    found = shutil.which("lark-cli") or "lark-cli"
    if found.lower().endswith((".cmd", ".bat")):
        try:
            content = Path(found).read_text(encoding="utf-8", errors="replace")
            match = re.search(r'"([^"]*lark-cli\.exe)"', content)
            if match:
                exe = os.path.expandvars(match.group(1))
                if Path(exe).exists():
                    return exe
        except OSError:
            pass
    return found


LARK_BIN = _resolve_lark_bin()


def lark(*args: str, retries: int = 3) -> dict[str, Any]:
    cmd = [LARK_BIN, *args, "--as", "user", "--format", "json"]
    last = ""
    for attempt in range(retries):
        proc = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
        out = proc.stdout.strip()
        try:
            response = json.loads(out)
        except json.JSONDecodeError:
            last = f"lark-cli非JSON输出: {out[:300]} / {proc.stderr[:300]}"
            time.sleep(1 + attempt)
            continue
        if not response.get("ok"):
            raise RuntimeError(json.dumps(response.get("error"), ensure_ascii=False))
        return response["data"]
    raise RuntimeError(last or "lark-cli连续失败")


def rows_of(data: dict[str, Any]) -> list[dict[str, Any]]:
    fields = data.get("fields", [])
    ids = data.get("record_id_list", [])
    rows = []
    for idx, values in enumerate(data.get("data", [])):
        row = dict(zip(fields, values))
        row["_record_id"] = ids[idx] if idx < len(ids) else None
        rows.append(row)
    return rows


def cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "、".join(str(x) for x in value)
    return str(value)


@dataclass(frozen=True)
class Utterance:
    uid: str
    start: str
    end: str
    raw_role: str
    text: str
    index: int


@dataclass
class Transcript:
    utterances: list[Utterance]
    source_text: str

    @property
    def by_id(self) -> dict[str, Utterance]:
        return {u.uid: u for u in self.utterances}

    def model_text(self) -> str:
        return "\n".join(
            f"{u.uid} [{u.start}–{u.end}] {u.raw_role}：{u.text}" for u in self.utterances
        )


LINE_RE = re.compile(
    r"^\s*\[(?P<start>\d{2}(?::\d{2}){1,2}\.\d{3})\s*[–—-]\s*"
    r"(?P<end>\d{2}(?::\d{2}){1,2}\.\d{3})\]\s*"
    r"(?P<role>销售|客户|旁人)\s*[：:]\s*(?P<text>.*)$"
)


def parse_transcript(text: str) -> Transcript:
    utterances: list[Utterance] = []
    for line in text.splitlines():
        match = LINE_RE.match(line)
        if not match:
            continue
        utterances.append(
            Utterance(
                uid=f"U{len(utterances) + 1:04d}",
                start=match.group("start"),
                end=match.group("end"),
                raw_role=match.group("role"),
                text=match.group("text").strip(),
                index=len(utterances),
            )
        )
    if len(utterances) < 3:
        raise ValueError("未解析到足够的“时间戳+销售/客户/旁人”发言")
    return Transcript(utterances, text)


def nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def unsupported_named_tokens(content: str, source: str) -> list[str]:
    tokens = re.findall(r"[\u4e00-\u9fff]{2,8}(?:省|市|区|县|镇|街道|路)", content)
    tokens += re.findall(r"(?:姓[\u4e00-\u9fff]|[\u4e00-\u9fff]总)", content)
    return [token for token in tokens if token not in source]


def require_keys(obj: Any, keys: set[str], label: str, errors: list[str]) -> bool:
    if not isinstance(obj, dict):
        errors.append(f"{label}必须是对象")
        return False
    actual = set(obj)
    if actual != keys:
        errors.append(f"{label}键名不符，缺少/多出: {sorted(actual ^ keys)}")
        return False
    return True


@dataclass
class EvidenceContext:
    roles: dict[str, str]
    excluded: set[str]
    correction_note: str


def validate_scope(scope: Any, transcript: Transcript, errors: list[str]) -> EvidenceContext:
    keys = {"input_quality", "quality_reason", "role_corrections", "exclusions"}
    if not require_keys(scope, keys, "_scope", errors):
        return EvidenceContext({u.uid: u.raw_role for u in transcript.utterances}, set(), "")
    if scope["input_quality"] not in {"可用", "证据不足"}:
        errors.append("_scope.input_quality只能为可用/证据不足")
    if not isinstance(scope["quality_reason"], str):
        errors.append("_scope.quality_reason必须是字符串")
    elif scope["input_quality"] == "证据不足" and not any(
        marker in scope["quality_reason"] for marker in INSUFFICIENT_MARKERS
    ):
        errors.append("输入标为证据不足时必须说明缺段/截断/严重乱码/角色无法确认等具体缺陷")
    by_id = transcript.by_id
    roles = {u.uid: u.raw_role for u in transcript.utterances}
    notes = []
    correction_notes: list[tuple[list[str], str]] = []
    seen_corrections: set[str] = set()
    corrections = scope["role_corrections"]
    if not isinstance(corrections, list):
        errors.append("_scope.role_corrections必须是数组")
        corrections = []
    for idx, item in enumerate(corrections, 1):
        if not isinstance(item, dict):
            errors.append(f"角色纠正{idx}必须是对象")
            continue
        range_keys = {"start_id", "end_id", "corrected_role", "reason"}
        list_keys = {"utterance_ids", "corrected_role", "reason"}
        if set(item) == range_keys:
            start, end = item["start_id"], item["end_id"]
            if start not in by_id or end not in by_id:
                errors.append(f"角色纠正{idx}引用不存在的区间")
                continue
            a, b = by_id[start].index, by_id[end].index
            if a > b:
                errors.append(f"角色纠正{idx}起止顺序无效")
                continue
            ids = [u.uid for u in transcript.utterances[a:b + 1]]
        elif set(item) == list_keys:
            ids = item["utterance_ids"]
        else:
            errors.append(f"角色纠正{idx}键名无效")
            continue
        if not isinstance(ids, list) or not ids:
            errors.append(f"角色纠正{idx}必须引用非空发言区间")
            continue
        if item["corrected_role"] not in ROLES or not nonempty(item["reason"]):
            errors.append(f"角色纠正{idx}缺少合法角色或理由")
        for uid in ids:
            if uid not in by_id:
                errors.append(f"角色纠正{idx}引用不存在的{uid}")
            elif uid in seen_corrections:
                errors.append(f"{uid}被重复纠正")
            else:
                seen_corrections.add(uid)
                roles[uid] = item["corrected_role"]
        id_label = ids[0] if len(ids) == 1 else f"{ids[0]}–{ids[-1]}"
        correction_notes.append(
            (ids, f"{id_label}→{item['corrected_role']}（{item['reason']}）")
        )

    excluded: set[str] = set()
    exclusions = scope["exclusions"]
    if not isinstance(exclusions, list):
        errors.append("_scope.exclusions必须是数组")
        exclusions = []
    for idx, item in enumerate(exclusions, 1):
        if not require_keys(item, {"start_id", "end_id", "reason"}, f"排除场景{idx}", errors):
            continue
        start, end = item["start_id"], item["end_id"]
        if start in by_id and end not in by_id:
            match = re.fullmatch(r"U(\d+)", str(end))
            if match and int(match.group(1)) > len(transcript.utterances):
                end = transcript.utterances[-1].uid
                item["end_id"] = end
        if start not in by_id or end not in by_id:
            errors.append(f"排除场景{idx}引用不存在的发言ID")
            continue
        a, b = by_id[start].index, by_id[end].index
        if a > b or not nonempty(item["reason"]):
            errors.append(f"排除场景{idx}区间或理由无效")
            continue
        excluded.update(u.uid for u in transcript.utterances[a:b + 1])
        notes.append(f"{start}–{end}排除（{item['reason']}）")
    notes = [text for ids, text in correction_notes if not all(uid in excluded for uid in ids)] + notes
    return EvidenceContext(roles, excluded, "；".join(notes))


def validate_ids(
    ids: Any,
    transcript: Transcript,
    ctx: EvidenceContext,
    errors: list[str],
    label: str,
    *,
    role: str | None = None,
    minimum: int = 1,
    contiguous: bool = False,
) -> list[str]:
    if not isinstance(ids, list) or len(ids) < minimum or not all(isinstance(x, str) for x in ids):
        errors.append(f"{label}至少需要{minimum}个发言ID")
        return []
    if len(ids) != len(set(ids)):
        errors.append(f"{label}存在重复发言ID")
    by_id = transcript.by_id
    valid = []
    indexes = []
    for uid in ids:
        if uid not in by_id:
            errors.append(f"{label}引用不存在的{uid}")
            continue
        if uid in ctx.excluded:
            errors.append(f"{label}引用了已排除场景{uid}")
            continue
        if role and ctx.roles.get(uid) != role:
            errors.append(f"{label}要求{role}证据，但{uid}纠正后为{ctx.roles.get(uid)}")
            continue
        valid.append(uid)
        indexes.append(by_id[uid].index)
    if contiguous and valid:
        if indexes != sorted(indexes) or any(b != a + 1 for a, b in zip(indexes, indexes[1:])):
            errors.append(f"{label}必须按原顺序连续引用")
    if len(valid) < minimum:
        errors.append(f"{label}通过角色与场景校验的证据不足{minimum}条")
    return valid


def sanitize_candidate(
    candidate: dict[str, Any],
    tr: Transcript,
    modules: list[str],
    identity: dict[str, str] | None = None,
    known_address: str = "",
    trusted_sources: str = "",
) -> dict[str, Any]:
    """只做保守修正：删无效金句/非未来行动；无客户证据的档案项降为未确认。

    known_address: 注入的主档地址文本，其中的省/市/区 token 视为已知事实，
                   不因证据中未出现而被判为编造。
    trusted_sources: 本次注入的可信来源全文（历史档案 + 主档地址 + 人工纠正）。
                   仅当「稳定档案+空证据」的内容能在可信来源中找到依据时才保留，
                   否则降为未确认（防模型借注入任意放行无证据事实）。
    """
    scope_errors: list[str] = []
    ctx = validate_scope(candidate.get("_scope", {}), tr, scope_errors)
    if scope_errors:
        return candidate
    by_id = tr.by_id

    def valid_ids(ids: Any, role: str) -> list[str]:
        if not isinstance(ids, list):
            return []
        return [
            uid for uid in ids
            if uid in by_id and uid not in ctx.excluded and ctx.roles.get(uid) == role
        ]

    if "quotes" in modules and isinstance(candidate.get("quotes"), dict):
        kept = []
        for item in candidate["quotes"].get("items", []):
            if not isinstance(item, dict):
                continue
            ids = valid_ids(item.get("evidence_ids"), "销售")
            indexes = [by_id[uid].index for uid in ids]
            contiguous = bool(ids) and indexes == sorted(indexes) and all(
                b == a + 1 for a, b in zip(indexes, indexes[1:])
            )
            if not contiguous:
                continue
            item["evidence_ids"] = ids
            item["polished_quote"] = str(item.get("polished_quote", "")).strip()
            item["reaction_ids"] = valid_ids(item.get("reaction_ids"), "客户")
            kept.append(item)
        candidate["quotes"]["items"] = kept

    if "store-profile" in modules and isinstance(candidate.get("store-profile"), dict):
        profile = candidate["store-profile"]
        known_tokens = (
            set(unsupported_named_tokens(known_address, "")) if known_address else set()
        )

        def named_ok(content: str, source: str) -> bool:
            return not [
                token for token in unsupported_named_tokens(content, source)
                if token not in known_tokens
            ]

        def clean_profile_text(text: Any, *, remove_sales_sentences: bool = False) -> str:
            cleaned = re.sub(
                r"[（(][^（）()]*U\d{4}[^（）()]*[）)]", "", str(text)
            )
            if remove_sales_sentences:
                parts = re.split(r"(?<=[。；;])", cleaned)
                cleaned = "".join(
                    part for part in parts
                    if not re.search(r"销售(?:提及|介绍|提出|表示|说明)", part)
                )
            return re.sub(r"\s+", " ", cleaned).strip()

        sections = profile.get("sections")
        if isinstance(sections, dict):
            for item in sections.values():
                if not isinstance(item, dict):
                    continue
                item["content"] = clean_profile_text(
                    item.get("content", ""), remove_sales_sentences=True
                ) or "未确认"
                ids = valid_ids(item.get("evidence_ids"), "客户")
                if item.get("state_type") == "未确认":
                    item["content"] = "未确认"
                    item["evidence_ids"] = []
                elif ids and named_ok(
                    str(item.get("content", "")),
                    "".join(by_id[uid].text for uid in ids),
                ):
                    item["evidence_ids"] = ids
                elif (
                    item.get("state_type") == "稳定档案"
                    and not ids
                    and trusted_sources.strip()
                    and named_ok(str(item.get("content", "")), trusted_sources)
                ):
                    # 「稳定档案+空证据」仅当内容能在可信来源（历史档案/地址/纠正）
                    # 中找到依据时保留；否则不允许无依据放行
                    item["evidence_ids"] = []
                else:
                    item["content"] = "未确认"
                    item["state_type"] = "未确认"
                    item["evidence_ids"] = []
        profile["one_line"] = clean_profile_text(profile.get("one_line", ""))
        one_line_ids = valid_ids(profile.get("one_line_evidence_ids"), "客户")
        one_line_source = "".join(by_id[uid].text for uid in one_line_ids)
        unsupported_identity = any(
            value
            and value in profile["one_line"]
            and value not in one_line_source
            for value in (identity or {}).values()
        )
        if (
            one_line_ids
            and named_ok(profile["one_line"], one_line_source)
            and not unsupported_identity
        ):
            profile["one_line_evidence_ids"] = one_line_ids
        else:
            replacement = None
            for key in (
                "selection_motion", "cooperation_preferences", "price_profit",
                "business_model", "categories_brands",
            ):
                item = sections.get(key) if isinstance(sections, dict) else None
                if isinstance(item, dict) and item.get("state_type") != "未确认":
                    replacement = item
                    break
            if replacement:
                profile["one_line"] = "该门店当前：" + str(replacement["content"]).rstrip("。") + "。"
                profile["one_line_evidence_ids"] = list(replacement["evidence_ids"])
            else:
                profile["one_line"] = "未确认：现有客户原话不足以形成可靠的一句话门店画像。"
                profile["one_line_evidence_ids"] = []

    if "next-action" in modules and isinstance(candidate.get("next-action"), dict):
        confirmed = candidate["next-action"].get("confirmed_actions")
        if isinstance(confirmed, list):
            candidate["next-action"]["confirmed_actions"] = [
                item for item in confirmed
                if isinstance(item, dict)
                and isinstance(item.get("evidence_ids"), list)
                and any(
                    uid in by_id
                    and uid not in ctx.excluded
                    and FUTURE_COMMITMENT_RE.search(by_id[uid].text)
                    for uid in item["evidence_ids"]
                )
            ]
    return candidate


def validate_explicit_needs(value: Any, tr: Transcript, ctx: EvidenceContext, errors: list[str]) -> None:
    if not require_keys(value, {"items"}, "explicit-needs", errors):
        return
    items = value["items"]
    if not isinstance(items, list) or len(items) > 5:
        errors.append("explicit-needs.items必须为0至5条")
    else:
        keys = {"need", "evidence_ids", "scene", "explanation"}
        for i, item in enumerate(items, 1):
            if not require_keys(item, keys, f"显性需求{i}", errors):
                continue
            for key in ("need", "scene", "explanation"):
                if not nonempty(item[key]):
                    errors.append(f"显性需求{i}.{key}不能为空")
            validate_ids(item["evidence_ids"], tr, ctx, errors, f"显性需求{i}", role="客户")
    dump = json.dumps(value, ensure_ascii=False)
    if any(word in dump for word in FORBIDDEN_NEEDS):
        errors.append("显性需求禁止包含建议验证问题或待验证点")


def validate_implicit_needs(value: Any, tr: Transcript, ctx: EvidenceContext, errors: list[str]) -> None:
    if not require_keys(value, {"items"}, "implicit-needs", errors):
        return
    items = value["items"]
    if not isinstance(items, list) or len(items) > 5:
        errors.append("implicit-needs.items必须为0至5条")
    else:
        keys = {"hypothesis", "confidence", "evidence_ids", "logic"}
        for i, item in enumerate(items, 1):
            if not require_keys(item, keys, f"隐性需求{i}", errors):
                continue
            for key in ("hypothesis", "logic"):
                if not nonempty(item[key]):
                    errors.append(f"隐性需求{i}.{key}不能为空")
            if item["confidence"] not in {"高", "中", "低"}:
                errors.append(f"隐性需求{i}.confidence只能为高/中/低")
            minimum = 1 if item["confidence"] == "低" else 2
            validate_ids(item["evidence_ids"], tr, ctx, errors, f"隐性需求{i}", role="客户", minimum=minimum)
    dump = json.dumps(value, ensure_ascii=False)
    if any(word in dump for word in FORBIDDEN_NEEDS):
        errors.append("隐性需求禁止包含建议验证问题或待验证点")


def validate_concerns(value: Any, tr: Transcript, ctx: EvidenceContext, scope: dict[str, Any], errors: list[str]) -> None:
    if not require_keys(value, {"categories"}, "concerns", errors):
        return
    categories = value["categories"]
    if not isinstance(categories, list) or len(categories) != 9:
        errors.append("concerns.categories必须恰好包含九类")
        return
    names = [x.get("name") for x in categories if isinstance(x, dict)]
    if sorted(names) != sorted(CONCERN_NAMES):
        errors.append("关心类目名称必须与九类白名单完全一致")
    status_by_name = {
        x.get("name"): x.get("status") for x in categories if isinstance(x, dict)
    }
    customer_text = "".join(
        u.text for u in tr.utterances
        if u.uid not in ctx.excluded and ctx.roles.get(u.uid) == "客户"
    )
    if re.search(r"价格战|价格打穿|打穿价格|乱价|控价|红线价", customer_text):
        if status_by_name.get("控价防窜") != "是":
            errors.append("客户明确提到价格战/打穿/乱价/控价/红线价，控价防窜必须标是")
    keys = {"name", "status", "subtype", "evidence_ids", "scene", "object", "reason"}
    for i, item in enumerate(categories, 1):
        if not require_keys(item, keys, f"关心类目{i}", errors):
            continue
        status = item["status"]
        if status not in {"是", "否", "证据不足"}:
            errors.append(f"{item['name']}状态无效")
            continue
        if status == "是":
            validate_ids(item["evidence_ids"], tr, ctx, errors, item["name"], role="客户")
            for key in ("scene", "object", "reason"):
                if not nonempty(item[key]):
                    errors.append(f"{item['name']}命中时{key}不能为空")
            if item["name"] == "其他" and not nonempty(item["subtype"]):
                errors.append("其他命中时必须给出具体子类")
        else:
            if item["evidence_ids"]:
                errors.append(f"{item['name']}为{status}时不得伪造命中证据")
            if status == "证据不足" and scope.get("input_quality") != "证据不足":
                errors.append(f"{item['name']}标证据不足时输入质量也必须为证据不足")


def validate_effectiveness(value: Any, tr: Transcript, ctx: EvidenceContext, errors: list[str]) -> None:
    if not require_keys(value, {"scores", "conclusion", "sales_result"}, "effectiveness", errors):
        return
    scores = value["scores"]
    if not isinstance(scores, list) or len(scores) != 8:
        errors.append("effectiveness.scores必须恰好包含八项")
        return
    expected = dict(DIMENSIONS)
    names = [x.get("name") for x in scores if isinstance(x, dict)]
    if sorted(names) != sorted(expected):
        errors.append("销售效能八项维度名称不完整")
    keys = {"name", "score", "judgment", "evidence_ids"}
    for i, item in enumerate(scores, 1):
        if not require_keys(item, keys, f"效能维度{i}", errors):
            continue
        name, score = item["name"], item["score"]
        if name in expected and (not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= expected[name]):
            errors.append(f"{name}分数必须为0至{expected[name]}的整数")
        if not nonempty(item["judgment"]):
            errors.append(f"{name}判断不能为空")
        evidence_ids = item["evidence_ids"]
        if evidence_ids:
            validate_ids(evidence_ids, tr, ctx, errors, name)
        elif not isinstance(evidence_ids, list):
            errors.append(f"{name}.evidence_ids必须是数组")
    length = len(str(value["conclusion"]).strip())
    if not 80 <= length <= 150:
        errors.append(f"销售效能结论必须80至150字，当前{length}字")
    if not nonempty(value["sales_result"]):
        errors.append("销售结果不能为空")


def validate_quotes(value: Any, tr: Transcript, ctx: EvidenceContext, errors: list[str]) -> None:
    if not require_keys(value, {"items"}, "quotes", errors):
        return
    items = value["items"]
    if not isinstance(items, list) or len(items) > 5:
        errors.append("quotes.items必须为0至5条")
        return
    keys = {
        "evidence_ids", "polished_quote", "speaker_name",
        "scene", "problem", "method", "value", "reaction_ids",
    }
    for i, item in enumerate(items, 1):
        if not require_keys(item, keys, f"销售金句{i}", errors):
            continue
        validate_ids(item["evidence_ids"], tr, ctx, errors, f"销售金句{i}", role="销售", contiguous=True)
        for key in ("polished_quote", "speaker_name", "scene", "problem", "method", "value"):
            if not nonempty(item[key]):
                errors.append(f"销售金句{i}.{key}不能为空")
        polished = str(item["polished_quote"]).strip()
        if polished and not 6 <= len(polished) <= 200:
            errors.append(f"销售金句{i}.polished_quote必须为6至200字")
        if re.search(r"U\d{4}", polished):
            errors.append(f"销售金句{i}.polished_quote不得包含发言ID")
        validate_ids(item["reaction_ids"], tr, ctx, errors, f"销售金句{i}客户反应", role="客户", minimum=0)


def validate_store_profile(value: Any, tr: Transcript, ctx: EvidenceContext, errors: list[str]) -> None:
    keys = {"one_line", "one_line_evidence_ids", "sections"}
    if not require_keys(value, keys, "store_profile", errors):
        return
    if not nonempty(value["one_line"]):
        errors.append("门店档案一句话画像不能为空")
    if re.search(r"U\d{4}", value["one_line"]):
        errors.append("门店档案正文不得直接展示内部发言ID")
    dump = json.dumps(value, ensure_ascii=False)
    for phrase in FORBIDDEN_PROFILE_PHRASES:
        if phrase in dump:
            errors.append(f"门店档案不得使用不可验证的心理描述：{phrase}")

    def validate_named_facts(content: str, ids: list[str], label: str) -> None:
        source = "".join(tr.by_id[uid].text for uid in ids if uid in tr.by_id)
        for token in unsupported_named_tokens(content, source):
            errors.append(f"{label}包含客户证据中不存在的专有信息：{token}")
    if str(value["one_line"]).startswith("未确认"):
        if value["one_line_evidence_ids"]:
            errors.append("门店一句话画像未确认时证据必须为空")
    elif value["one_line_evidence_ids"]:
        # 非未确认且有本次证据 → 正常校验；无证据 = 历史档案保留画像（动态更新），放行
        validate_ids(value["one_line_evidence_ids"], tr, ctx, errors, "门店一句话画像", role="客户")
        validate_named_facts(value["one_line"], value["one_line_evidence_ids"], "门店一句话画像")
    sections = value["sections"]
    if not require_keys(sections, set(PROFILE_SECTIONS), "门店档案sections", errors):
        return
    section_keys = {"content", "state_type", "evidence_ids"}
    for key, label in PROFILE_SECTIONS.items():
        item = sections[key]
        if not require_keys(item, section_keys, label, errors):
            continue
        if item["state_type"] not in {"稳定档案", "当前状态", "未确认"}:
            errors.append(f"{label}.state_type无效")
        if not nonempty(item["content"]):
            errors.append(f"{label}.content不能为空")
        if re.search(r"U\d{4}", str(item["content"])):
            errors.append(f"{label}.content不得直接展示内部发言ID")
        if item["state_type"] == "未确认":
            if item["evidence_ids"]:
                errors.append(f"{label}未确认时证据必须为空")
        elif item["evidence_ids"]:
            validate_ids(item["evidence_ids"], tr, ctx, errors, label, role="客户")
            validate_named_facts(item["content"], item["evidence_ids"], label)
        elif item["state_type"] != "稳定档案":
            # 仅「稳定档案」允许无本次证据（历史档案保留项，动态更新）；当前状态必须本次证据
            errors.append(f"{label}需要至少1个本次发言ID作为证据")


def validate_next_action(
    value: Any,
    tr: Transcript,
    ctx: EvidenceContext,
    errors: list[str],
) -> None:
    keys = {
        "cooperation_status", "status_evidence_ids", "action_judgment",
        "judgment_reason", "confirmed_actions", "recommended_actions",
        "second_visit",
    }
    if not require_keys(value, keys, "next-action", errors):
        return

    status = value["cooperation_status"]
    if status not in {"已合作", "未合作"}:
        errors.append("next-action.cooperation_status只能为已合作/未合作")
    if not isinstance(value["status_evidence_ids"], list):
        errors.append("next-action.status_evidence_ids必须为数组")

    judgment = value["action_judgment"]
    if judgment not in {"触发", "不触发", "待验证"}:
        errors.append("next-action.action_judgment只能为触发/不触发/待验证")
    if not nonempty(value["judgment_reason"]):
        errors.append("next-action.judgment_reason不能为空")
    if re.search(r"U\d{4}", str(value["judgment_reason"])):
        errors.append("next-action.judgment_reason不得直接展示发言ID")

    confirmed = value["confirmed_actions"]
    if not isinstance(confirmed, list) or len(confirmed) > 3:
        errors.append("next-action.confirmed_actions必须为0至3条")
    else:
        action_keys = {"owner", "timeframe", "action", "evidence_ids"}
        for i, item in enumerate(confirmed, 1):
            label = f"已确认行动{i}"
            if not require_keys(item, action_keys, label, errors):
                continue
            if item["owner"] not in {"销售", "客户", "双方"}:
                errors.append(f"{label}.owner只能为销售/客户/双方")
            for key in ("timeframe", "action"):
                if not nonempty(item[key]):
                    errors.append(f"{label}.{key}不能为空")
                if re.search(r"U\d{4}", str(item[key])):
                    errors.append(f"{label}.{key}不得直接展示发言ID")
            valid = validate_ids(item["evidence_ids"], tr, ctx, errors, label)
            roles = {ctx.roles.get(uid) for uid in valid}
            if item["owner"] == "销售" and "销售" not in roles:
                errors.append(f"{label}缺少销售明确承诺证据")
            elif item["owner"] == "客户" and "客户" not in roles:
                errors.append(f"{label}缺少客户明确承诺证据")
            elif item["owner"] == "双方" and not {"销售", "客户"}.issubset(roles):
                errors.append(f"{label}标记为双方时必须同时包含销售和客户证据")
            if item["owner"] in {"客户", "双方"}:
                explicit_customer_commitment = any(
                    ctx.roles.get(uid) == "客户"
                    and not WEAK_ASSENT_RE.fullmatch(tr.by_id[uid].text.strip())
                    and FUTURE_COMMITMENT_RE.search(tr.by_id[uid].text)
                    for uid in valid
                )
                if not explicit_customer_commitment:
                    errors.append(
                        f"{label}缺少客户本人明确表达的未来动作；"
                        "客户仅以嗯/好/行/可以附和时不得视为客户承诺"
                    )
            if valid and not any(
                FUTURE_COMMITMENT_RE.search(tr.by_id[uid].text) for uid in valid
            ):
                errors.append(
                    f"{label}缺少对话结束后待执行的明确承诺证据；"
                    "现场已完成或正在执行的动作不得列为下一步"
                )

    recommended = value["recommended_actions"]
    if not isinstance(recommended, list) or len(recommended) > 3:
        errors.append("next-action.recommended_actions必须为0至3条")
    else:
        action_keys = {
            "topic", "owner", "timeframe", "action", "reason",
            "acceptance", "evidence_ids",
        }
        for i, item in enumerate(recommended, 1):
            label = f"建议行动{i}"
            if not require_keys(item, action_keys, label, errors):
                continue
            if item["owner"] not in {"销售", "区域经理", "双方"}:
                errors.append(f"{label}.owner只能为销售/区域经理/双方")
            for key in ("topic", "timeframe", "action", "reason", "acceptance"):
                if not nonempty(item[key]):
                    errors.append(f"{label}.{key}不能为空")
                if re.search(r"U\d{4}", str(item[key])):
                    errors.append(f"{label}.{key}不得直接展示发言ID")
            validate_ids(item["evidence_ids"], tr, ctx, errors, label)

    if isinstance(confirmed, list) and isinstance(recommended, list):
        has_actions = bool(confirmed or recommended)
        if judgment == "触发" and not has_actions:
            errors.append("行动判断为触发时至少需要一条已确认事项或建议行动")
        if judgment in {"不触发", "待验证"} and has_actions:
            errors.append(f"行动判断为{judgment}时不得输出已确认事项或建议行动")

    second = value["second_visit"]
    second_keys = {"value", "reason", "evidence_ids"}
    if not require_keys(second, second_keys, "二次拜访判断", errors):
        return
    if second["value"] not in {"值得", "暂缓", "不建议", "无法判断", "不适用"}:
        errors.append("二次拜访价值只能为值得/暂缓/不建议/无法判断/不适用")
    if not nonempty(second["reason"]):
        errors.append("二次拜访判断.reason不能为空")
    if re.search(r"U\d{4}", str(second["reason"])):
        errors.append("二次拜访判断.reason不得直接展示发言ID")
    if status == "已合作":
        if second["value"] != "不适用" or second["evidence_ids"]:
            errors.append("已合作门店的二次拜访判断必须为不适用且证据为空")
    elif status == "未合作":
        if second["value"] == "不适用":
            errors.append("未合作门店必须判断二次拜访价值")
        validate_ids(
            second["evidence_ids"], tr, ctx, errors, "二次拜访判断",
            minimum=0 if second["value"] == "无法判断" else 1,
        )
        if judgment == "不触发" and second["value"] == "值得":
            errors.append("行动判断为不触发时二次拜访价值不得为值得")


def validate_next_action_daily(
    value: Any,
    tr: Transcript,
    ctx: EvidenceContext,
    errors: list[str],
    action_history: str = "",
) -> None:
    """日常模式 next-action：闭环（上次行动完成情况）+ 行动策略（不判合作状态）。"""
    keys = {
        "closure", "action_judgment", "judgment_reason",
        "confirmed_actions", "recommended_actions", "second_visit",
    }
    if not require_keys(value, keys, "next-action", errors):
        return

    closure = value["closure"]
    if not isinstance(closure, dict):
        errors.append("next-action.closure必须是对象")
        return
    if not require_keys(closure, {"summary", "items"}, "总结闭环", errors):
        return
    if not nonempty(closure["summary"]):
        errors.append("总结闭环.summary不能为空")
    if re.search(r"U\d{4}", str(closure["summary"])):
        errors.append("总结闭环.summary不得直接展示发言ID")
    has_history = bool(action_history.strip())
    items = closure["items"]
    if not isinstance(items, list) or len(items) > 3:
        errors.append("总结闭环.items必须为0至3条")
    elif has_history and not items:
        errors.append("提供了上次行动策略但总结闭环.items为空，必须逐条判断上次行动完成情况")
    elif not has_history and items:
        errors.append("未提供上次行动策略但总结闭环.items非空，不得编造上次行动")
    else:
        for i, item in enumerate(items, 1):
            label = f"闭环{i}"
            if not require_keys(item, {"action", "done", "evidence_ids", "reason"}, label, errors):
                continue
            if not nonempty(item["action"]):
                errors.append(f"{label}.action不能为空")
            if re.search(r"U\d{4}", str(item["action"])):
                errors.append(f"{label}.action不得直接展示发言ID")
            if item["done"] not in {"已完成", "未完成", "部分完成"}:
                errors.append(f"{label}.done只能为已完成/未完成/部分完成")
            elif item["done"] == "已完成":
                validate_ids(item["evidence_ids"], tr, ctx, errors, label)
            else:
                if not nonempty(item.get("reason")):
                    errors.append(f"{label}.reason不能为空（未完成需说明原因）")
                validate_ids(item["evidence_ids"], tr, ctx, errors, label, minimum=0)

    judgment = value["action_judgment"]
    if judgment not in {"触发", "不触发", "待验证"}:
        errors.append("next-action.action_judgment只能为触发/不触发/待验证")
    if not nonempty(value["judgment_reason"]):
        errors.append("next-action.judgment_reason不能为空")
    if re.search(r"U\d{4}", str(value["judgment_reason"])):
        errors.append("next-action.judgment_reason不得直接展示发言ID")

    confirmed = value["confirmed_actions"]
    if not isinstance(confirmed, list) or len(confirmed) > 3:
        errors.append("next-action.confirmed_actions必须为0至3条")
    else:
        action_keys = {"owner", "timeframe", "action", "evidence_ids"}
        for i, item in enumerate(confirmed, 1):
            label = f"已确认行动{i}"
            if not require_keys(item, action_keys, label, errors):
                continue
            if item["owner"] not in {"销售", "客户", "双方"}:
                errors.append(f"{label}.owner只能为销售/客户/双方")
            for key in ("timeframe", "action"):
                if not nonempty(item[key]):
                    errors.append(f"{label}.{key}不能为空")
                if re.search(r"U\d{4}", str(item[key])):
                    errors.append(f"{label}.{key}不得直接展示发言ID")
            validate_ids(item["evidence_ids"], tr, ctx, errors, label)

    recommended = value["recommended_actions"]
    if not isinstance(recommended, list) or len(recommended) > 3:
        errors.append("next-action.recommended_actions必须为0至3条")
    else:
        action_keys = {
            "topic", "owner", "timeframe", "action", "reason",
            "acceptance", "evidence_ids",
        }
        for i, item in enumerate(recommended, 1):
            label = f"建议行动{i}"
            if not require_keys(item, action_keys, label, errors):
                continue
            if item["owner"] not in {"销售", "区域经理", "双方"}:
                errors.append(f"{label}.owner只能为销售/区域经理/双方")
            for key in ("topic", "timeframe", "action", "reason", "acceptance"):
                if not nonempty(item[key]):
                    errors.append(f"{label}.{key}不能为空")
                if re.search(r"U\d{4}", str(item[key])):
                    errors.append(f"{label}.{key}不得直接展示发言ID")
            # 日常模式允许无当前录音证据：历史延续型建议（对应上次未完成行动）没有本次发言可引
            validate_ids(item["evidence_ids"], tr, ctx, errors, label, minimum=0)

    if isinstance(confirmed, list) and isinstance(recommended, list):
        has_actions = bool(confirmed or recommended)
        if judgment == "触发" and not has_actions:
            errors.append("行动判断为触发时至少需要一条已确认事项或建议行动")
        if judgment in {"不触发", "待验证"} and has_actions:
            errors.append(f"行动判断为{judgment}时不得输出已确认事项或建议行动")

    second = value["second_visit"]
    if not require_keys(second, {"value", "reason", "evidence_ids"}, "二次拜访判断", errors):
        return
    if second["value"] not in {"值得", "暂缓", "不建议", "无法判断", "不适用"}:
        errors.append("二次拜访价值只能为值得/暂缓/不建议/无法判断/不适用")
    if not nonempty(second["reason"]):
        errors.append("二次拜访判断.reason不能为空")
    if re.search(r"U\d{4}", str(second["reason"])):
        errors.append("二次拜访判断.reason不得直接展示发言ID")
    validate_ids(second["evidence_ids"], tr, ctx, errors, "二次拜访判断", minimum=0)


def validate_candidate(
    candidate: Any,
    tr: Transcript,
    modules: list[str],
    mode: str = "first",
    action_history: str = "",
) -> tuple[list[str], EvidenceContext]:
    errors: list[str] = []
    if not isinstance(candidate, dict):
        return ["模型输出必须是JSON对象"], EvidenceContext({}, set(), "")
    candidate.pop("_裁决依据", None)
    expected = {"_scope", *modules}
    if set(candidate) != expected:
        errors.append(f"顶层键名不符，缺少/多出: {sorted(set(candidate) ^ expected)}")
    scope = candidate.get("_scope", {})
    ctx = validate_scope(scope, tr, errors)
    for module in modules:
        value = candidate.get(module)
        if module == "explicit-needs":
            validate_explicit_needs(value, tr, ctx, errors)
        elif module == "implicit-needs":
            validate_implicit_needs(value, tr, ctx, errors)
        elif module == "concerns":
            validate_concerns(value, tr, ctx, scope, errors)
        elif module == "effectiveness":
            validate_effectiveness(value, tr, ctx, errors)
        elif module == "quotes":
            validate_quotes(value, tr, ctx, errors)
        elif module == "store-profile":
            validate_store_profile(value, tr, ctx, errors)
        elif module == "next-action":
            if mode == "daily":
                validate_next_action_daily(value, tr, ctx, errors, action_history)
            else:
                validate_next_action(value, tr, ctx, errors)
    return errors, ctx


def evidence_line(uid: str, tr: Transcript, ctx: EvidenceContext) -> str:
    u = tr.by_id[uid]
    role = ctx.roles.get(uid, u.raw_role)
    return f"[{u.start}–{u.end}] {role}：“{u.text}”"


def evidence_block(ids: list[str], tr: Transcript, ctx: EvidenceContext) -> str:
    return "\n".join(evidence_line(uid, tr, ctx) for uid in ids)


def render_explicit_needs(value: dict[str, Any], tr: Transcript, ctx: EvidenceContext) -> dict[str, Any]:
    explicit, explicit_ref = [], []
    for i, item in enumerate(value["items"], 1):
        explicit.append(
            f"{i}. 需求点：{item['need']}"
        )
        explicit_ref.append(
            f"{i}. 需求点：{item['need']}\n客户原话与时间戳：\n"
            f"{evidence_block(item['evidence_ids'], tr, ctx)}\n"
            f"具体场景：{item['scene']}\n事实解释：{item['explanation']}"
        )
    return {
        "显性需求(仅供参考)": "\n\n".join(explicit) or "未识别到证据充分的显性需求。",
        "显性需求_原句参考": "\n\n".join(explicit_ref) or "完整对话中未找到可支持显性需求的客户原话。",
    }


def render_implicit_needs(value: dict[str, Any], tr: Transcript, ctx: EvidenceContext) -> dict[str, Any]:
    implicit, implicit_ref = [], []
    for i, item in enumerate(value["items"], 1):
        implicit.append(
            f"{i}. 隐性需求/顾虑假设：{item['hypothesis']}\n"
            f"可信度：{item['confidence']}"
        )
        implicit_ref.append(
            f"{i}. 隐性需求/顾虑假设：{item['hypothesis']}\n推断依据：\n"
            f"{evidence_block(item['evidence_ids'], tr, ctx)}\n"
            f"推断逻辑：{item['logic']}\n可信度：{item['confidence']}"
        )
    return {
        "隐性需求(仅供参考)": "\n\n".join(implicit) or "未识别到证据充分的隐性需求或顾虑假设。",
        "隐性需求_原句参考": "\n\n".join(implicit_ref) or "现有客户证据不足以形成可靠的隐性需求推断。",
    }


def render_concerns(value: dict[str, Any], tr: Transcript, ctx: EvidenceContext) -> dict[str, Any]:
    hits, no, insufficient = [], [], []
    tags = []
    for item in value["categories"]:
        if item["status"] == "是":
            tags.append(item["name"])
            label = item["name"] + (f"（{item['subtype']}）" if item["subtype"] else "")
            hits.append(
                f"{len(hits) + 1}. {label}｜判断结果：是\n客户原话与时间戳：\n"
                f"{evidence_block(item['evidence_ids'], tr, ctx)}\n具体场景：{item['scene']}\n"
                f"关心对象：{item['object']}\n原因分析：{item['reason']}"
            )
        elif item["status"] == "否":
            no.append(item["name"])
        else:
            insufficient.append(item["name"])
    tail = []
    if no:
        tail.append("未命中类目（判断结果：否）：" + "、".join(no) + "。")
    if insufficient:
        tail.append("证据不足类目：" + "、".join(insufficient) + "。")
    text = "\n\n".join(hits + tail) or "九类均未发现客户有效关心证据。"
    if ctx.correction_note:
        text += "\n\n证据边界：" + ctx.correction_note
    return {"关心类目打标": tags, "场景化类目归因": text}


def effectiveness_level(total: int) -> str:
    if total >= 85:
        return "A｜卓越"
    if total >= 70:
        return "B｜有效"
    if total >= 50:
        return "C｜待改进"
    return "D｜低效"


def render_effectiveness(value: dict[str, Any], tr: Transcript, ctx: EvidenceContext) -> dict[str, Any]:
    by_name = {x["name"]: x for x in value["scores"]}
    total = sum(by_name[name]["score"] for name, _ in DIMENSIONS)
    score_lines, ref_lines, formula = [], [], []
    for idx, (name, maximum) in enumerate(DIMENSIONS, 1):
        item = by_name[name]
        score_lines.append(f"{idx}. {name}：{item['score']}/{maximum}——{item['judgment']}")
        ref_lines.append(
            f"{idx}. {name}｜{item['score']}/{maximum}\n原句证据：\n"
            f"{evidence_block(item['evidence_ids'], tr, ctx)}\n评分依据：{item['judgment']}"
        )
        formula.append(str(item["score"]))
    main = (
        f"评估模型：销售对话效能评估模型\n销售对话效能得分：{total}/100\n"
        f"销售对话效能等级：{effectiveness_level(total)}\n\n八项评分：\n"
        + "\n".join(score_lines)
        + f"\n\n确定性合计：{'+'.join(formula)}={total}\n\n"
        + f"销售对话效能结论：{value['conclusion']}\n\n"
        + f"本次推进结果（不计分）：{value['sales_result']}"
    )
    refs = "\n\n".join(ref_lines)
    if ctx.correction_note:
        refs = "证据边界：" + ctx.correction_note + "\n\n" + refs
    return {
        "合作进展打分评估": main,
        "原句参考_合作进展打分评估": refs,
    }


def render_quotes(value: dict[str, Any], tr: Transcript, ctx: EvidenceContext) -> dict[str, Any]:
    def clean(text: str) -> str:
        return str(text).strip().rstrip("。；;，,")

    blocks = []
    for i, item in enumerate(value["items"], 1):
        quote = item["polished_quote"]
        first, last = tr.by_id[item["evidence_ids"][0]], tr.by_id[item["evidence_ids"][-1]]
        speaker = item["speaker_name"] or "录音未提供姓名"
        block = (
            f"{i}.\n「{quote}」\n—— {speaker}，证据时间戳：[{first.start}–{last.end}]。"
            f"在{clean(item['scene'])}的场景中，针对{clean(item['problem'])}，"
            f"通过{clean(item['method'])}，意在帮助客户理解{clean(item['value'])}。"
        )
        if item["reaction_ids"]:
            block += "\n客户明确反应：" + evidence_block(item["reaction_ids"], tr, ctx)
        blocks.append(block)
    return {"核心金句": "\n\n".join(blocks) or "未识别到证据充分且可复用的销售金句。"}


def visit_mode(row: dict[str, Any], profile: dict[str, Any]) -> str:
    """按「拜访阶段」分流。

    first：未填或等于首访破冰；daily：在白名单的日常阶段；
    unknown：非空但不在任何已知选项（拼写错误/新阶段）——调用方应跳过该记录，
    不静默按 daily 处理（避免意外跳过隐性需求/打分/金句）。
    """
    stage = cell_text(row.get(profile.get("stage_field", ""))).strip()
    if not stage or stage == profile.get("first_stage_choice", "首访破冰"):
        return "first"
    daily_choices = set(profile.get("daily_stage_choices") or [])
    if daily_choices and stage in daily_choices:
        return "daily"
    if not daily_choices:
        # 未配置白名单时保持旧行为（非首访即日常）
        return "daily"
    return "unknown"


def master_record_id_of(row: dict[str, Any], profile: dict[str, Any]) -> str | None:
    """从 02 表行取「关联门店」link 指向的 01 主档记录 ID。

    lark-cli 的 link 字段返回 [{"id": "recxxx", "text": "..."}]，需取 dict 的 id。
    """
    value = row.get(profile.get("master_link_field", ""))
    if isinstance(value, list) and value:
        item = value[0]
        if isinstance(item, dict):
            return str(item.get("id") or "")
        return str(item)
    return None


def read_master_address(profile: dict[str, Any], master_rid: str) -> str:
    """读 01 主档地址字段，拼成已知事实文本；未配置或读取失败返回空串。

    address_fields 按序为 [省, 详细地址]；详细地址已含省时不重复拼。
    """
    fields = profile.get("address_fields", [])
    if not fields or not master_rid:
        return ""
    try:
        args = ["base", "+record-get",
                "--base-token", profile["base"],
                "--table-id", profile["master_table"],
                "--record-id", master_rid]
        for field in fields:
            args.extend(["--field-id", field])
        data = lark(*args)
    except RuntimeError:
        return ""
    rows = rows_of(data)
    if not rows:
        return ""
    parts = [cell_text(rows[0].get(field)).strip() for field in fields]
    parts = [part for part in parts if part]
    if not parts:
        return ""
    province, detail = parts[0], parts[-1]
    if province and (detail.startswith(province) or detail.startswith(province[:-1])):
        # 详细地址已含省名（含「省」字或省名本身）时不重复拼
        return detail
    return " ".join(parts)


def parse_profile_text(text: str) -> dict[str, str]:
    """拆解门店档案正文为 7 维度（与 store-profile-split.parse_profile 口径一致）。"""
    dims: dict[str, str] = {}
    m = PROFILE_ONE_LINE_RE.search(text)
    if m:
        dims["one_line"] = m.group(1).strip()
    label_to_key = {label: key for key, label in PROFILE_SECTIONS.items()}
    seen: set[str] = set()
    for m in PROFILE_SECTION_RE.finditer(text):
        key = label_to_key[m.group(1)]
        if key in seen:
            continue  # 同一维度多行（异常）时取第一行
        seen.add(key)
        dims[key] = m.group(2).strip()
    for key in list(PROFILE_SECTIONS) + ["one_line"]:
        dims.setdefault(key, "")
    return dims


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        ts = value if value < 10**11 else value / 1000  # 秒 vs 毫秒
        try:
            return datetime.fromtimestamp(ts)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()[:19].replace("T", " ")
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def within_history_days(value: Any, days: int) -> bool:
    """进店时间是否在 days 天内（含当天）；解析失败保守视为在范围内。"""
    parsed = _parse_datetime(value)
    if parsed is None:
        return True
    return (datetime.now() - parsed).days <= days


_STORE_LOCKS: dict[str, threading.Lock] = {}
_STORE_LOCKS_GUARD = threading.Lock()


def store_lock_for(master_rid: str) -> threading.Lock:
    """同一门店的记录串行处理（日常模式读历史/写回需确定顺序）。"""
    with _STORE_LOCKS_GUARD:
        lock = _STORE_LOCKS.get(master_rid)
        if lock is None:
            lock = threading.Lock()
            _STORE_LOCKS[master_rid] = lock
        return lock


def _closure_field_of(profile: dict[str, Any]) -> str:
    return profile.get("closure_field") or MODULE_FIELDS["next-action"][-1]


def _earlier_than(row_date: Any, current_date: Any) -> bool:
    """历史记录进店时间必须早于当前记录（防补录/重跑时间穿越）。"""
    current = _parse_datetime(current_date)
    if current is None:
        return True  # 当前时间未知时保守放行（保持原行为）
    parsed = _parse_datetime(row_date)
    if parsed is None:
        return False  # 历史时间无法解析时不读取（避免误用）
    return parsed < current


def find_history_profiles(
    profile: dict[str, Any],
    master_rid: str,
    exclude_rid: str,
    limit: int = 1,
    within_days: int | None = None,
    current_date: Any = None,
) -> list[tuple[str, str]]:
    """02 表查该门店进店时间最近（且早于当前记录）、档案非空的门店档案（最多 limit 份）。

    返回 [(进店时间原文, 档案正文), ...]；within_days 非空时只取该天数内的
    （最近一份超窗即停止）。只读「门店档案」精简正文，不读证据字段（省 token）。
    """
    filter_json = json.dumps(
        {
            "logic": "and",
            "conditions": [[profile["master_link_field"], "intersects", [master_rid]]],
        },
        ensure_ascii=False,
    )
    sort_json = json.dumps(
        [{"field": profile["master_date_field"], "desc": True}], ensure_ascii=False
    )
    profile_field = MODULE_FIELDS["store-profile"][0]
    data = lark(
        "base", "+record-list", "--base-token", profile["base"], "--table-id", profile["table"],
        "--filter-json", filter_json, "--sort-json", sort_json,
        "--field-id", profile["master_date_field"], "--field-id", profile_field,
        "--limit", "200",
    )
    found: list[tuple[str, str]] = []
    for row in rows_of(data):
        if row["_record_id"] == exclude_rid:
            continue
        date_value = row.get(profile["master_date_field"])
        if current_date is not None and not _earlier_than(date_value, current_date):
            continue  # 不早于当前记录 → 时间穿越，跳过
        text = cell_text(row.get(profile_field)).strip()
        if not text:
            continue
        if within_days is not None and date_value and not within_history_days(date_value, within_days):
            break  # 最近一条已超窗口，更早的不再看
        found.append((cell_text(date_value), text))
        if len(found) >= limit:
            break
    return found


def find_history_profile(
    profile: dict[str, Any], master_rid: str, exclude_rid: str,
) -> tuple[str, str] | None:
    """首访模式：前 history_days 天内最近一条非空档案（保持原语义）。"""
    found = find_history_profiles(
        profile, master_rid, exclude_rid,
        limit=1, within_days=int(profile.get("master_history_days", 3)),
    )
    return found[0] if found else None


def find_last_actions(
    profile: dict[str, Any], master_rid: str, exclude_rid: str,
    current_date: Any = None,
) -> tuple[str, str] | None:
    """日常模式：该门店前一次（时间早于当前记录、不跨拜访）的「下一步行动策略」+「总结闭环」。

    返回 (行动策略正文, 闭环正文)；前次行动为空时返回 (空串, 空串)（表示前次无行动，
    不静默跨越多次拜访去找更早的非空行动）；无历史拜访则 None。
    """
    action_field = MODULE_FIELDS["next-action"][0]
    closure_field = _closure_field_of(profile)
    filter_json = json.dumps(
        {
            "logic": "and",
            "conditions": [[profile["master_link_field"], "intersects", [master_rid]]],
        },
        ensure_ascii=False,
    )
    sort_json = json.dumps(
        [{"field": profile["master_date_field"], "desc": True}], ensure_ascii=False
    )
    data = lark(
        "base", "+record-list", "--base-token", profile["base"], "--table-id", profile["table"],
        "--filter-json", filter_json, "--sort-json", sort_json,
        "--field-id", profile["master_date_field"],
        "--field-id", action_field, "--field-id", closure_field,
        "--limit", "200",
    )
    for row in rows_of(data):
        if row["_record_id"] == exclude_rid:
            continue
        date_value = row.get(profile["master_date_field"])
        if current_date is not None and not _earlier_than(date_value, current_date):
            continue  # 时间穿越，跳过
        # 不跳过行动为空的记录：第一条早于当前的就是「前一次拜访」
        return cell_text(row.get(action_field)).strip(), cell_text(row.get(closure_field)).strip()
    return None


def sync_master_profile(
    profile: dict[str, Any], row: dict[str, Any], profile_text: str,
) -> dict[str, Any]:
    """把门店档案正文拆成 7 维度，全量覆盖写 01 门店主档表（用户已授权覆盖）。

    写回沿用 update_record 的逐字段 upsert + 复读模式；失败抛错由调用方记 warning，
    不阻断 02 表分析结果。
    """
    master_rid = master_record_id_of(row, profile)
    if not master_rid:
        raise RuntimeError("02 表未关联 01 门店主档（关联门店为空）")
    dims = parse_profile_text(profile_text)
    patch: dict[str, str] = {}
    for field, dim_key in profile.get("master_fields", {}).items():
        value = dims.get(dim_key, "").strip()
        if value:
            patch[field] = value
    if not patch:
        raise RuntimeError("门店档案正文解析不出任何维度，未写 01 主档")
    update_record(profile["base"], profile["master_table"], master_rid, patch)
    return {"master_record_id": master_rid, "fields": sorted(patch)}


def render_store_profile(
    value: dict[str, Any],
    tr: Transcript,
    ctx: EvidenceContext,
    identity: dict[str, str],
    record_id: str,
    history_date: str = "",
) -> dict[str, Any]:
    lines = [f"一句话画像：{value['one_line']}"]
    all_ids = list(value["one_line_evidence_ids"])
    for key, label in PROFILE_SECTIONS.items():
        item = value["sections"][key]
        lines.append(f"{label}（{item['state_type']}）：{item['content']}")
        all_ids.extend(item["evidence_ids"])
    source = "；".join(
        f"{key}={value}" for key, value in identity.items() if value
    )
    lines.append(
        f"档案口径：动态更新档案；来源：record_id={record_id}"
        + (f"；参考历史进店时间：{history_date}" if history_date else "")
        + (f"；{source}" if source else "")
        + "。"
    )
    # 关键证据拆到「门店档案-原文证据」字段，正文保持精简（读历史省 token）
    evidence_lines: list[str] = []
    unique_ids = [
        uid for uid in dict.fromkeys(all_ids)
        if len(tr.by_id[uid].text.strip()) > 2
        and tr.by_id[uid].text.strip() not in {"好的", "对的", "知道了"}
    ][:12]
    if unique_ids:
        evidence_lines.append("关键证据：\n" + evidence_block(unique_ids, tr, ctx))
    if ctx.correction_note:
        evidence_lines.append("证据边界：" + ctx.correction_note)
    payload = {"门店档案": "\n\n".join(lines)}
    if evidence_lines:
        payload["门店档案-原文证据"] = "\n\n".join(evidence_lines)
    return payload


def _render_action_sections(value: dict[str, Any]) -> str:
    """已确认事项 + 建议行动 + 二次拜访判断 的展示正文（首访/日常共用）。"""
    lines: list[str] = []
    confirmed = value["confirmed_actions"]
    lines.append("已确认的后续事项：")
    if not confirmed:
        lines.append("无明确约定。")
    for i, item in enumerate(confirmed, 1):
        lines.append(
            f"{i}. [{item['owner']}｜{item['timeframe']}] {item['action']}"
        )

    recommended = value["recommended_actions"]
    lines.append("\n下一步行动策略：")
    if not recommended:
        if value["action_judgment"] == "不触发":
            lines.append("本次无需新增行动。")
        elif value["action_judgment"] == "待验证":
            lines.append("待补充关键信息后再生成行动。")
        else:
            lines.append("按已确认的后续事项执行，无需新增建议动作。")
    for i, item in enumerate(recommended, 1):
        lines.append(
            f"{i}. 【{item['topic']}｜{item['owner']}｜{item['timeframe']}】"
            f"{item['action']}\n验收：{item['acceptance']}"
        )

    second = value["second_visit"]
    if second["value"] != "不适用":
        lines.append(f"\n二次拜访价值：{second['value']}\n建议：{second['reason']}")
    return "\n".join(lines)


def render_next_action(
    value: dict[str, Any], tr: Transcript, ctx: EvidenceContext,
) -> dict[str, Any]:
    status = value["cooperation_status"]
    lines = [
        f"合作状态判定：{status}",
        f"行动判断：{value['action_judgment']}",
        f"判断原因：{value['judgment_reason']}",
    ]
    lines.append(_render_action_sections(value))
    return {
        "下一步行动策略": "\n".join(lines),
        "是否达成合作": "是" if status == "已合作" else "否",
    }


def render_next_action_daily(
    value: dict[str, Any], tr: Transcript, ctx: EvidenceContext,
    closure_field: str = "",
) -> dict[str, Any]:
    """日常模式：一次输出「总结闭环」+「下一步行动策略」两个字段。"""
    closure = value["closure"]
    closure_lines = [f"本次行动总结：{closure['summary']}"]
    closure_lines.append("\n上次行动完成情况：")
    items = closure["items"]
    if not items:
        closure_lines.append("无上次行动记录。")
    for i, item in enumerate(items, 1):
        done = item["done"]
        line = f"{i}. 【{done}】{item['action']}"
        if nonempty(item.get("reason")) and done != "已完成":
            line += f"；原因：{item['reason']}"
        closure_lines.append(line)
    action_lines = [
        f"行动判断：{value['action_judgment']}",
        f"判断原因：{value['judgment_reason']}",
    ]
    action_lines.append(_render_action_sections(value))
    return {
        closure_field or "上一次行动与这一次行动总结闭环": "\n".join(closure_lines),
        "下一步行动策略": "\n".join(action_lines),
    }


def render_module(
    module: str,
    candidate: dict[str, Any],
    tr: Transcript,
    identity: dict[str, str],
    record_id: str,
    history_date: str = "",
    mode: str = "first",
    action_history: str = "",
    closure_field: str = "",
) -> dict[str, Any]:
    errors, ctx = validate_candidate(candidate, tr, [module], mode, action_history)
    if errors:
        raise ValueError("渲染前校验失败: " + "；".join(errors))
    if module == "explicit-needs":
        payload = render_explicit_needs(candidate[module], tr, ctx)
    elif module == "implicit-needs":
        payload = render_implicit_needs(candidate[module], tr, ctx)
    elif module == "concerns":
        payload = render_concerns(candidate[module], tr, ctx)
    elif module == "effectiveness":
        payload = render_effectiveness(candidate[module], tr, ctx)
    elif module == "quotes":
        payload = render_quotes(candidate[module], tr, ctx)
    elif module == "store-profile":
        payload = render_store_profile(
            candidate[module], tr, ctx, identity, record_id, history_date
        )
    elif module == "next-action" and mode == "daily":
        payload = render_next_action_daily(candidate[module], tr, ctx, closure_field)
    else:
        payload = render_next_action(candidate[module], tr, ctx)
    # 飞书可见正文剥离知识引用标记；引用全量留在事件/完成日志供审计
    return {key: _strip_knowledge_deep(value) for key, value in payload.items()}


def _strip_knowledge_deep(value: Any) -> Any:
    if isinstance(value, str):
        return kb.strip_knowledge_refs(value)
    if isinstance(value, list):
        return [_strip_knowledge_deep(v) for v in value]
    if isinstance(value, dict):
        return {key: _strip_knowledge_deep(v) for key, v in value.items()}
    return value


_DROP = {"response_format": False, "thinking": False}


def call_llm(system: str, user: str, model: str, temperature: float) -> tuple[str, dict[str, Any]]:
    key = api_key()
    if not key:
        raise RuntimeError("缺少API Key")
    last: Any = None
    for attempt in range(4):
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
        }
        if not _DROP["response_format"]:
            body["response_format"] = {"type": "json_object"}
        if not _DROP["thinking"]:
            body["thinking"] = {"type": "disabled"}
        req = urllib.request.Request(
            api_url(),
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return payload["choices"][0]["message"]["content"], payload.get("usage", {})
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            if exc.code == 400:
                changed = False
                for param in ("response_format", "thinking"):
                    if param in detail:
                        _DROP[param] = True
                        changed = True
                if changed:
                    continue
            last = f"HTTP {exc.code}: {detail}"
            time.sleep((attempt + 1) * (15 if exc.code == 429 else 4))
        except (urllib.error.URLError, TimeoutError, KeyError) as exc:
            last = exc
            time.sleep((attempt + 1) * 4)
    raise RuntimeError(f"模型调用连续失败: {last}")


def extract_json(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("输出中找不到JSON对象")
    value = json.loads(cleaned[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("JSON顶层不是对象")
    return value


def system_prompt(module: str, mode: str = "first") -> str:
    """按模式选提示词：日常拜访用 daily 专用版（store-profile/next-action）。"""
    prompt_key = f"{module}-daily" if mode == "daily" and f"{module}-daily" in PROMPT_FILES else module
    path = PROMPT_FILES.get(prompt_key)
    if path is None:
        raise ValueError(f"模块没有专属提示词: {prompt_key}")
    block = kb.knowledge_block_for(module)
    module_prompt = path.read_text(encoding="utf-8")
    if not block:
        return COMMON_PROMPT + "\n\n" + module_prompt
    return COMMON_PROMPT + "\n\n" + block + "\n\n" + module_prompt


def build_user_message(
    tr: Transcript,
    module: str,
    identity: dict[str, str],
    errors: list[str] | None = None,
    candidates: tuple[dict[str, Any], dict[str, Any]] | None = None,
    notes: str = "",
    history: str = "",
    address: str = "",
    correction: str = "",
    action_history: str = "",
) -> str:
    hard_rules = {
        "concerns": f"九类白名单={json.dumps(CONCERN_NAMES, ensure_ascii=False)}",
        "effectiveness": f"八维白名单={json.dumps([name for name, _ in DIMENSIONS], ensure_ascii=False)}",
        "store-profile": f"六个事实section键={json.dumps(list(PROFILE_SECTIONS), ensure_ascii=False)}",
    }
    history_block = ""
    if module == "store-profile" and history:
        history_block = (
            "该门店历史拜访的门店档案（动态更新依据：前次确认且本次未提及的事实保留；"
            "前次待核验本次有新证据则确认；与本次新证据冲突时以本次为准；"
            "前次结论仍可被本次证据推翻）：\n" + history + "\n\n"
        )
    action_history_block = ""
    if module == "next-action" and action_history:
        action_history_block = (
            "该门店前一次拜访的下一步行动策略与总结闭环（本次需判断上次行动是否完成，"
            "并基于闭环结果输出本次的下一步行动）：\n" + action_history + "\n\n"
        )
    correction_block = ""
    if module == "store-profile" and correction:
        correction_block = (
            "门店档案纠正（DSR人工填写，权威性高于现场速记与转写；用于补充或修正门店事实，"
            "与转写冲突时以纠正为准；行动推进类信息不属于档案维度，不写入档案正文；"
            "纠正非客户原话，原句引用仍只能来自带时间戳的转写）：\n"
            + correction + "\n\n"
        )
    address_block = ""
    if module == "store-profile" and address:
        address_block = (
            "门店地址（来源：门店主档清单，为可信已知事实，无需录音证据支持；"
            "写入门店基本信息时可直接引用，证据留空、state_type 用 稳定档案）：\n"
            + address + "\n\n"
        )
    message = (
        f"本次唯一任务：{module}。\n"
        f"顶层必须且只能包含_scope和{module}。\n"
        + (f"机器硬校验白名单：{hard_rules[module]}\n" if module in hard_rules else "")
        + "飞书行定位元数据不作为客户事实输入，脚本会另行写入档案来源。\n\n"
        + (
            "现场速记（销售本人当场记录，优先级高于转写；与转写冲突时以现场速记事实为准；"
            "速记非客户原话，原句引用仍只能来自带时间戳的转写）：\n" + notes + "\n\n"
            if notes else ""
        )
        + correction_block
        + address_block
        + history_block
        + action_history_block
        + "完整转写（必须完整阅读）：\n" + tr.model_text()
    )
    if errors:
        message += "\n\n上次输出未通过代码校验，请修正后输出完整JSON：\n- " + "\n- ".join(errors[:30])
    if candidates:
        message += (
            "\n\n你是第三裁决者。下面两份候选存在分歧。逐项回查完整转写，"
            "选择或合并有证据的一方，输出一份完整、可通过规则的JSON；不得为了折中保留无证据内容。"
            "\n候选A：\n" + json.dumps(candidates[0], ensure_ascii=False)
            + "\n候选B：\n" + json.dumps(candidates[1], ensure_ascii=False)
            + "\n额外增加_裁决依据字段，简述关键取舍；该字段仅记日志。"
        )
    return message


def analyze(
    tr: Transcript,
    module: str,
    identity: dict[str, str],
    model: str,
    temperature: float,
    candidates: tuple[dict[str, Any], dict[str, Any]] | None = None,
    notes: str = "",
    history: str = "",
    address: str = "",
    retain_stable: bool = False,
    correction: str = "",
    mode: str = "first",
    action_history: str = "",
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[str]]:
    usages: list[dict[str, Any]] = []
    errors: list[str] = []
    last: dict[str, Any] | None = None
    attempts = 1 if candidates else 2
    for attempt in range(attempts):
        output, usage = call_llm(
            system_prompt(module, mode),
            build_user_message(
                tr, module, identity,
                errors if attempt else None, candidates, notes, history, address, correction,
                action_history,
            ),
            model,
            temperature,
        )
        usages.append(usage)
        try:
            last = extract_json(output)
        except (ValueError, json.JSONDecodeError) as exc:
            errors = [f"输出不是合法JSON: {exc}"]
            continue
        trusted_sources = "\n".join(
            part for part in (history, address, correction) if part.strip()
        )
        last = sanitize_candidate(
            last, tr, [module], identity,
            known_address=address, trusted_sources=trusted_sources,
        )
        errors, _ = validate_candidate(last, tr, [module], mode, action_history)
        if not errors and kb.knowledge_block_for(module):
            bad_refs = kb.validate_knowledge_refs(last, kb.injected_ids(module))
            if bad_refs:
                errors = [f"知识引用不在本次注入集: {bad_refs}"]
        if not errors:
            return last, usages, []
    return None, usages, errors + ([f"最后候选={json.dumps(last, ensure_ascii=False)[:1000]}"] if last else [])


def norm_text(value: str) -> str:
    return re.sub(r"[\s，。；：、（）()【】\[\]“”\"']", "", value)


def module_signature(module: str, candidate: dict[str, Any]) -> Any:
    """只比较业务判断骨架，不比较标题、解释和润色文本。"""
    value = candidate[module]
    if module == "explicit-needs":
        return tuple(sorted(tuple(sorted(x["evidence_ids"])) for x in value["items"]))
    if module == "implicit-needs":
        return tuple(sorted(
            (x["confidence"], tuple(sorted(x["evidence_ids"])))
            for x in value["items"]
        ))
    if module == "concerns":
        return tuple(sorted((x["name"], x["status"]) for x in value["categories"]))
    if module == "effectiveness":
        return tuple(sorted((x["name"], x["score"]) for x in value["scores"]))
    if module == "quotes":
        return tuple(sorted(tuple(x["evidence_ids"]) for x in value["items"]))
    if module == "store-profile":
        return (
            tuple(sorted(value["one_line_evidence_ids"])),
            tuple(sorted(
                (key, item["state_type"], tuple(sorted(item["evidence_ids"])))
                for key, item in value["sections"].items()
            )),
        )
    if "closure" in value:
        # 日常模式：覆盖全部写入飞书的可见业务字段（防双跑签名失真）
        def norm_action(item: dict[str, Any]) -> tuple:
            return tuple(str(item.get(k, "")) for k in (
                "topic", "owner", "timeframe", "action", "reason", "acceptance",
            )) + (tuple(sorted(str(x) for x in item.get("evidence_ids", []))),)
        return (
            str(value["closure"].get("summary", "")),
            tuple(sorted(
                (str(item.get("action", "")), str(item.get("done", "")),
                 str(item.get("reason", "")),
                 tuple(sorted(str(x) for x in item.get("evidence_ids", []))))
                for item in value["closure"]["items"]
            )),
            value["action_judgment"],
            tuple(sorted(norm_action(x) for x in value["confirmed_actions"])),
            tuple(sorted(norm_action(x) for x in value["recommended_actions"])),
            (value["second_visit"].get("value", ""),
             str(value["second_visit"].get("reason", ""))),
        )
    return (
        value["cooperation_status"],
        value["action_judgment"],
        tuple(sorted(tuple(sorted(x["evidence_ids"])) for x in value["confirmed_actions"])),
        tuple(sorted(tuple(sorted(x["evidence_ids"])) for x in value["recommended_actions"])),
        value["second_visit"]["value"],
    )


def risky_modules(candidate: dict[str, Any], tr: Transcript, modules: list[str]) -> set[str]:
    risky: set[str] = set()
    scope = candidate["_scope"]
    if scope["role_corrections"] or scope["exclusions"] or len(tr.source_text) > 30000:
        risky.update(modules)
    if "implicit-needs" in modules and any(
        x["confidence"] == "中" for x in candidate["implicit-needs"]["items"]
    ):
        risky.add("implicit-needs")
    if "concerns" in modules:
        other = next(x for x in candidate["concerns"]["categories"] if x["name"] == "其他")
        if other["status"] == "是":
            risky.add("concerns")
    if "effectiveness" in modules:
        total = sum(x["score"] for x in candidate["effectiveness"]["scores"])
        if any(abs(total - boundary) <= 3 for boundary in (50, 70, 85)):
            risky.add("effectiveness")
    if "quotes" in modules and any(len(x["evidence_ids"]) > 1 for x in candidate["quotes"]["items"]):
        risky.add("quotes")
    if "quotes" in modules:
        risky.add("quotes")
    if "store-profile" in modules and any(
        x["state_type"] == "当前状态" for x in candidate["store-profile"]["sections"].values()
    ):
        risky.add("store-profile")
    if "next-action" in modules:
        risky.add("next-action")
    return risky


def split_candidate(candidate: dict[str, Any], module: str) -> dict[str, Any]:
    return {"_scope": candidate["_scope"], module: candidate[module]}


def review_candidates(
    primary: dict[str, Any],
    tr: Transcript,
    modules: list[str],
    identity: dict[str, str],
    model: str,
    temperature: float,
    review_mode: str,
    notes: str = "",
    history: str = "",
    address: str = "",
    retain_stable: bool = False,
    correction: str = "",
    mode: str = "first",
    action_history: str = "",
) -> tuple[dict[str, dict[str, Any]], list[str], list[dict[str, Any]]]:
    """对风险模块做独立双跑；分歧时用第三次独立结果做多数裁决。

    返回的 issues 只表示复核未收敛。调用方可按 review_policy 选择：
    strict 阻止写回，fallback-primary 保留已通过硬校验的首轮结果。
    """
    selected = {module: split_candidate(primary, module) for module in modules}
    issues: list[str] = []
    usages: list[dict[str, Any]] = []
    if review_mode == "off":
        return selected, issues, usages
    to_review = set(modules) if review_mode == "all" else risky_modules(primary, tr, modules)
    for module in modules:
        if module not in to_review:
            continue
        second, usage2, errors2 = analyze(
            tr, module, identity, model, temperature, notes=notes,
            history=history, address=address, retain_stable=retain_stable, correction=correction,
            mode=mode, action_history=action_history,
        )
        usages.extend(usage2)
        if second is None:
            issues.append(f"{module}:第二跑未通过({';'.join(errors2[:3])})")
            continue
        first_module = split_candidate(primary, module)
        first_signature = module_signature(module, first_module)
        second_signature = module_signature(module, second)
        if first_signature == second_signature:
            continue
        third, usage3, errors3 = analyze(
            tr, module, identity, model, temperature, notes=notes,
            history=history, address=address, retain_stable=retain_stable, correction=correction,
            mode=mode, action_history=action_history,
        )
        usages.extend(usage3)
        if third is None:
            issues.append(f"{module}:第三跑未通过({';'.join(errors3[:3])})")
            continue
        third_signature = module_signature(module, third)
        if third_signature == first_signature:
            continue
        if third_signature == second_signature:
            selected[module] = second
            continue
        issues.append(f"{module}:三跑业务判断骨架均不一致")
    return selected, issues, usages


def validate_schema(base: str, table: str, profile: dict[str, Any]) -> dict[str, Any]:
    data = lark("base", "+field-list", "--base-token", base, "--table-id", table)
    fields = {item["name"]: item for item in data.get("fields", [])}
    cooperation_field = profile.get("cooperation_field", "是否达成合作")
    required = {
        profile["source_field"], cooperation_field,
        *profile.get("identity_fields", []),
    }
    for values in MODULE_FIELDS.values():
        required.update(values)
    missing = sorted(required - set(fields))
    if missing:
        raise RuntimeError(f"Base缺少字段: {missing}")
    address_fields = profile.get("address_fields", [])
    if address_fields:
        if not profile.get("master_table"):
            raise RuntimeError("配置了address_fields但缺少master_table")
        master_data = lark(
            "base", "+field-list",
            "--base-token", profile["base"],
            "--table-id", profile["master_table"],
        )
        master_fields = {item["name"] for item in master_data.get("fields", [])}
        missing_addr = [f for f in address_fields if f not in master_fields]
        if missing_addr:
            raise RuntimeError(f"01主档缺少地址字段: {missing_addr}")
    text_required = {profile["source_field"]}
    for values in MODULE_FIELDS.values():
        text_required.update(values)
    text_required.discard("关心类目打标")
    text_required.discard(cooperation_field)  # 单选 select，单独校验
    if profile.get("notes_field"):
        text_required.add(profile["notes_field"])
    if profile.get("correction_field"):
        text_required.add(profile["correction_field"])
    if profile.get("evidence_field"):
        text_required.add(profile["evidence_field"])
    for name in text_required:
        if fields[name].get("type") != "text":
            raise RuntimeError(f"字段{name}必须是text，实际{fields[name].get('type')}")
    cooperation = fields[cooperation_field]
    cooperation_options = [x["name"] for x in cooperation.get("options", [])]
    if (
        cooperation.get("type") != "select"
        or cooperation.get("multiple")
        or not {"是", "否"}.issubset(cooperation_options)
    ):
        raise RuntimeError(f"字段{cooperation_field}必须是包含是/否的单选字段")
    valid_field = profile.get("valid_field")
    if valid_field:
        valid = fields.get(valid_field)
        if valid is None:
            raise RuntimeError(f"Base缺少字段: {valid_field}")
        valid_options = [x["name"] for x in valid.get("options", [])]
        if (
            valid.get("type") != "select"
            or valid.get("multiple")
            or not {"有效", "录音过短", "内容无效"}.issubset(valid_options)
        ):
            raise RuntimeError(f"字段{valid_field}必须是包含有效/录音过短/内容无效的单选字段")
    concern = fields["关心类目打标"]
    options = [x["name"] for x in concern.get("options", [])]
    if concern.get("type") != "select" or not concern.get("multiple"):
        raise RuntimeError("关心类目打标必须是多选字段")
    if sorted(options) != sorted(CONCERN_NAMES):
        raise RuntimeError(f"关心类目选项与白名单不一致: {options}")
    stage_field = profile.get("stage_field")
    if stage_field:
        stage = fields.get(stage_field)
        if stage is None:
            raise RuntimeError(f"Base缺少字段: {stage_field}")
        stage_options = [x["name"] for x in stage.get("options", [])]
        first_choice = profile.get("first_stage_choice", "首访破冰")
        if stage.get("type") != "select" or stage.get("multiple"):
            raise RuntimeError(f"字段{stage_field}必须是单选字段")
        if first_choice not in stage_options:
            raise RuntimeError(f"{stage_field}选项缺少首访阶段「{first_choice}」")
        missing_daily = [
            c for c in (profile.get("daily_stage_choices") or [])
            if c not in stage_options
        ]
        if missing_daily:
            raise RuntimeError(
                f"{stage_field}选项缺少日常阶段配置: {missing_daily}"
            )
    return fields


def fetch_records(
    base: str,
    table: str,
    view: str,
    profile: dict[str, Any],
    start: int,
    count: int,
) -> list[dict[str, Any]]:
    names = [
        profile["source_field"],
        profile.get("cooperation_field", "是否达成合作"),
        *profile.get("identity_fields", []),
        profile.get("notes_field", ""),
        profile.get("correction_field", ""),
        profile.get("valid_field", ""),
        profile.get("evidence_field", ""),
        profile.get("master_link_field", ""),
        profile.get("master_date_field", ""),
        profile.get("stage_field", ""),
        profile.get("closure_field", ""),
    ]
    for values in MODULE_FIELDS.values():
        names.extend(values)
    names = list(dict.fromkeys(names))
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        args = [
            "base", "+record-list", "--base-token", base, "--table-id", table,
            "--view-id", view, "--limit", "200", "--offset", str(offset),
        ]
        for name in names:
            args.extend(["--field-id", name])
        data = lark(*args)
        page = rows_of(data)
        rows.extend(page)
        if not data.get("has_more") or not page:
            break
        offset += 200
    end = start - 1 + count if count > 0 else None
    return rows[start - 1:end]


def field_blank(value: Any) -> bool:
    return value is None or value == "" or value == []


# 无效录音在 7 个分析模块主字段统一写的标记（按无效原因分文案；「无效」是迁移前旧值，按内容无效处理）
INVALID_MARKERS = {
    "录音过短": "录音无实质性内容",
    "内容无效": "录音无效",
    "无效": "录音无效",
}
# 写标记的字段：每个模块的正文主字段；select 字段（关心类目打标、是否达成合作）与原文参考/证据类字段不写
INVALID_MARKER_FIELDS = [
    "显性需求(仅供参考)",
    "隐性需求(仅供参考)",
    "场景化类目归因",
    "合作进展打分评估",
    "核心金句",
    "门店档案",
    "下一步行动策略",
]


def mark_invalid_modules(
    row: dict[str, Any], profile: dict[str, Any], valid_value: str,
    base: str, table: str,
) -> list[str]:
    """无效录音：7 个分析模块主字段统一写标记，只填空字段。返回实际写入的字段。"""
    marker = INVALID_MARKERS.get(valid_value, "录音无效")
    payload = {
        field: marker for field in INVALID_MARKER_FIELDS
        if field_blank(row.get(field))
    }
    if payload:
        update_record(base, table, row["_record_id"], payload)
    return list(payload)


def module_state(row: dict[str, Any], module: str, mode: str = "first") -> str:
    if module == "next-action":
        # 首访/日常都只看「下一步行动策略」是否已生成；互斥字段
        # （首访=是否达成合作、日常=总结闭环）不参与判断，避免模式切换误判
        return (
            "complete" if not field_blank(row.get("下一步行动策略")) else "empty"
        )
    if module == "store-profile":
        # 证据字段由正文派生，判断只看「门店档案」正文
        return (
            "complete" if not field_blank(row.get("门店档案")) else "empty"
        )
    fields = MODULE_FIELDS[module]
    values = [row.get(name) for name in fields]
    if module == "concerns" and not field_blank(row.get("场景化类目归因")):
        return "complete"
    if all(field_blank(v) for v in values):
        return "empty"
    if all(not field_blank(v) for v in values):
        return "complete"
    return "partial"


def field_values_equal(field: str, actual: Any, expected: Any) -> bool:
    if field == "关心类目打标":
        actual_items = [] if actual is None else actual
        expected_items = [] if expected is None else expected
        if isinstance(actual_items, list) and isinstance(expected_items, list):
            return sorted(str(item) for item in actual_items) == sorted(
                str(item) for item in expected_items
            )
    if field == "是否达成合作":
        # select 单选：lark-cli 复读返回选项名数组（如 ["是"]），期望值是选项名
        if isinstance(actual, list) and isinstance(expected, str):
            return [str(item) for item in actual] == [expected]
        return actual == expected
    return actual == expected


def update_record(base: str, table: str, record_id: str, payload: dict[str, Any]) -> None:
    # Windows 命令行长度有限；长录音的多个文本字段合并后可能无法启动 lark-cli。
    for field, value in payload.items():
        data = lark(
            "base", "+record-upsert", "--base-token", base, "--table-id", table,
            "--record-id", record_id,
            "--json", json.dumps({field: value}, ensure_ascii=False),
        )
        if not data.get("updated"):
            raise RuntimeError(
                f"飞书未确认更新字段{field}: {json.dumps(data, ensure_ascii=False)[:300]}"
            )
    args = [
        "base", "+record-get", "--base-token", base, "--table-id", table,
        "--record-id", record_id,
    ]
    for field in payload:
        args.extend(["--field-id", field])
    mismatch = ""
    for attempt in range(3):
        rows = rows_of(lark(*args))
        if len(rows) != 1:
            mismatch = "写后复读未返回唯一记录"
        else:
            mismatch = next(
                (
                    f"写后复读不一致: {field}"
                    for field, expected in payload.items()
                    if not field_values_equal(field, rows[0].get(field), expected)
                ),
                "",
            )
        if not mismatch:
            return
        time.sleep(attempt + 1)
    raise RuntimeError(mismatch)


class RunLogger:
    def __init__(self, run_dir: Path):
        self.lock = threading.Lock()
        self.files = {
            name: open(run_dir / f"{name}.jsonl", "a", encoding="utf-8")
            for name in ("events", "done", "manual", "errors")
        }

    def emit(self, kind: str, value: dict[str, Any]) -> None:
        with self.lock:
            self.files[kind].write(json.dumps(value, ensure_ascii=False) + "\n")
            self.files[kind].flush()

    def close(self) -> None:
        for file in self.files.values():
            file.close()


def parse_modules(value: str) -> list[str]:
    if value.strip() == "all":
        return ALL_MODULES.copy()
    modules = []
    for item in (x.strip() for x in value.split(",") if x.strip()):
        modules.extend(MODULE_ALIASES.get(item, [item]))
    unknown = sorted(set(modules) - set(ALL_MODULES))
    if unknown:
        raise argparse.ArgumentTypeError(f"未知模块: {unknown}")
    return list(dict.fromkeys(modules))


def flatten_module_jobs(
    tasks: list[tuple[dict[str, Any], list[str]]],
) -> list[tuple[dict[str, Any], str]]:
    return [
        (row, module)
        for row, requested in tasks
        for module in requested
    ]


def latest_leftover_run() -> Path:
    runs = ROOT / "runs"
    candidates = []
    for path in runs.iterdir() if runs.exists() else []:
        if any((path / f"{name}.jsonl").exists() and (path / f"{name}.jsonl").stat().st_size for name in ("manual", "errors")):
            candidates.append(path)
    if not candidates:
        raise RuntimeError("没有可rescue的历史运行")
    return max(candidates, key=lambda x: x.name)


def rescue_targets(path: Path) -> dict[str, set[str]]:
    targets: dict[str, set[str]] = {}
    for name in ("manual", "errors"):
        file = path / f"{name}.jsonl"
        if not file.exists():
            continue
        for line in file.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            rid = item.get("record_id")
            if not rid:
                continue
            modules = item.get("modules") or ALL_MODULES
            targets.setdefault(rid, set()).update(modules)
    return targets


def rescue_budget(runs_dir: Path, source_path: Path) -> tuple[str, int]:
    """返回 rescue 根运行和本次轮次；同一根运行的历史轮次跨进程累计。"""
    source_path = source_path.resolve()
    source_manifest_path = source_path / "run_manifest.json"
    source_manifest = (
        json.loads(source_manifest_path.read_text(encoding="utf-8"))
        if source_manifest_path.exists() else {}
    )
    root = str(source_manifest.get("rescue_root") or source_path)
    highest = int(source_manifest.get("rescue_round") or 0)
    for path in runs_dir.iterdir() if runs_dir.exists() else []:
        manifest_path = path / "run_manifest.json"
        if not manifest_path.exists():
            continue
        try:
            item = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(item.get("rescue_root") or "") == root:
            highest = max(highest, int(item.get("rescue_round") or 0))
    return root, highest + 1


def _preflight_knowledge(modules: list[str]) -> None:
    root = CFG.get("knowledge_root")
    if not root or not kb.is_enabled():
        print("[preflight] knowledge: 未启用（缺 knowledge_root 或已关闭）", flush=True)
        return
    errors, warnings = kb.validate_knowledge(Path(root))
    for w in warnings:
        print(f"[preflight] knowledge-warn: {w}", flush=True)
    for e in errors:
        print(f"[preflight] knowledge-ERROR: {e}", flush=True)
    if errors:
        print("[preflight] knowledge: 知识库校验失败，请先修复", flush=True)
    for module in modules:
        block = kb.knowledge_block_for(module)
        if block:
            print(
                f"[preflight] knowledge[{module}] 将注入 {len(block.splitlines())} 行 / "
                f"约 {kb.estimate_tokens(block)} token",
                flush=True,
            )
        else:
            print(f"[preflight] knowledge[{module}] 未注入", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="销售录音批量分析与飞书回填流水线")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--view", help="飞书源视图ID")
    source.add_argument("--input-file", help="本地带角色转写，仅预览")
    parser.add_argument("--profile", default=None)
    parser.add_argument("--record-id", default=None, help="仅处理指定record_id")
    parser.add_argument("--record-ids", default="", help="仅处理逗号分隔的多个record_id")
    parser.add_argument("--modules", default="all")
    parser.add_argument("--overwrite", default="", help="all或逗号分隔模块")
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--count", type=int, default=0)
    parser.add_argument("--workers", type=int, default=16, help="录音×模块任务并发数（默认16）")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--preflight", action="store_true", help="只做schema与任务预筛选，不调用模型")
    parser.add_argument("--no-knowledge", action="store_true", help="本运行禁用知识库注入")
    parser.add_argument(
        "--knowledge-check", action="store_true",
        help="只做知识库一致性检查并退出（不调用模型）",
    )
    parser.add_argument("--review", choices=("off", "flagged", "all"), default="flagged")
    parser.add_argument(
        "--review-policy",
        choices=("fallback-primary", "strict"),
        default="fallback-primary",
        help="复核未收敛时默认保留首轮硬校验合格结果；strict则转人工且不写回",
    )
    parser.add_argument("--rescue", default=None, help="latest或历史运行目录")
    parser.add_argument(
        "--max-rescue-rounds",
        type=int,
        default=1,
        help="同一根运行允许的累计rescue轮数（默认1；提高必须显式指定）",
    )
    parser.add_argument(
        "--model",
        default=CFG.get("model", "deepseek-v4-pro"),
        help="分析模型（默认deepseek-v4-pro）",
    )
    parser.add_argument("--temperature", type=float, default=0.1)
    args = parser.parse_args()

    if not args.knowledge_check and not args.view and not args.input_file:
        parser.error("必须指定 --view 或 --input-file（或使用 --knowledge-check）")

    modules = parse_modules(args.modules)
    overwrite = set(parse_modules(args.overwrite)) if args.overwrite else set()
    record_ids = {item.strip() for item in args.record_ids.split(",") if item.strip()}
    if args.record_id and record_ids:
        parser.error("--record-id 与 --record-ids 不能同时使用")
    if args.record_id:
        record_ids = {args.record_id}
    if args.input_file:
        args.dry_run = True
    if args.start < 1 or args.count < 0 or args.workers < 1 or args.max_rescue_rounds < 1:
        parser.error("start/workers必须为正数，count不能为负数")

    if args.no_knowledge:
        kb.configure(root=CFG.get("knowledge_root"), enabled=False,
                     mode=CFG.get("knowledge_mode", "manifest"))
    if args.knowledge_check:
        import knowledge_check as kc  # 延迟导入避免循环
        root = CFG.get("knowledge_root")
        if not root:
            print("知识库未配置（config.local.json 缺 knowledge_root）", file=sys.stderr)
            return 3
        errors, warnings = kc.run_check(root, None, ALL_MODULES)
        for w in warnings:
            print(f"[kb-warn] {w}")
        for e in errors:
            print(f"[kb-ERROR] {e}")
        print(f"[kb-check] {'失败' if errors else '通过'}（错误 {len(errors)}，警告 {len(warnings)}）")
        return 1 if errors else 0

    run_dir = ROOT / "runs" / (
        datetime.now().strftime("%Y%m%d-%H%M%S-%f") + f"-p{os.getpid()}"
    )
    run_dir.mkdir(parents=True, exist_ok=False)
    logger = RunLogger(run_dir)
    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "model": args.model,
        "workers": args.workers,
        "worker_unit": "record-module",
        "modules": modules,
        "review": args.review,
        "review_policy": args.review_policy,
        "dry_run": args.dry_run,
        "record_ids": sorted(record_ids),
        "rules_internal": "sales-call-v1",
    }
    (run_dir / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    profile_name = args.profile or CFG.get("default_profile", "default")
    profiles = CFG.get("profiles", {})
    if profile_name not in profiles:
        raise RuntimeError(f"未知profile: {profile_name}")
    profile = profiles[profile_name]
    base, table = profile["base"], profile["table"]

    if args.input_file:
        text = Path(args.input_file).read_text(encoding="utf-8")
        rows = [{
            "_record_id": "local",
            profile["source_field"]: text,
            profile.get("cooperation_field", "是否达成合作"): "",
            profile.get("notes_field", ""): "",
            profile.get("valid_field", ""): "",
            **{field: "" for field in profile.get("identity_fields", [])},
        }]
    else:
        validate_schema(base, table, profile)
        view = args.view or profile.get("default_view")
        rows = fetch_records(base, table, view, profile, args.start, args.count)

    rescue_map: dict[str, set[str]] | None = None
    if args.rescue:
        rescue_path = latest_leftover_run() if args.rescue == "latest" else Path(args.rescue)
        rescue_root, rescue_round = rescue_budget(ROOT / "runs", rescue_path)
        if rescue_round > args.max_rescue_rounds:
            raise RuntimeError(
                f"rescue预算已用完：root={rescue_root}，"
                f"下一轮={rescue_round}，上限={args.max_rescue_rounds}；"
                "请转人工处理，或在确认额外消耗后显式提高--max-rescue-rounds"
            )
        manifest.update({
            "rescue_source": str(rescue_path.resolve()),
            "rescue_root": rescue_root,
            "rescue_round": rescue_round,
            "max_rescue_rounds": args.max_rescue_rounds,
        })
        (run_dir / "run_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        rescue_map = rescue_targets(rescue_path)

    tasks = []
    skipped_blank = skipped_complete = skipped_invalid = skipped_unknown_stage = partial = 0
    for row in rows:
        rid = row.get("_record_id")
        if record_ids and rid not in record_ids:
            continue
        transcript_text = cell_text(row.get(profile["source_field"])).strip()
        if not transcript_text:
            skipped_blank += 1
            continue
        valid_field = profile.get("valid_field")
        if valid_field and cell_text(row.get(valid_field)) not in ("", "有效"):
            skipped_invalid += 1
            marked: list[str] = []
            if not args.preflight and not args.dry_run:
                marked = mark_invalid_modules(
                    row, profile, cell_text(row.get(valid_field)), base, table
                )
            logger.emit("events", {
                "at": datetime.now().isoformat(timespec="seconds"),
                "event": "skip_invalid", "record_id": rid, "marked_fields": marked,
            })
            continue
        requested = []
        mode = visit_mode(row, profile)
        if mode == "unknown":
            skipped_unknown_stage += 1
            logger.emit("skip_unknown_stage", {
                "record_id": rid,
                "stage": cell_text(row.get(profile.get("stage_field", ""))).strip(),
            })
            continue
        for module in modules:
            if mode == "daily" and module not in DAILY_MODULES:
                continue  # 日常拜访不做隐性需求/打分/金句
            if rescue_map is not None and (rid not in rescue_map or module not in rescue_map[rid]):
                continue
            state = module_state(row, module, mode)
            if module in overwrite or args.input_file:
                requested.append(module)
            elif state == "empty":
                requested.append(module)
            elif state == "partial":
                partial += 1
                logger.emit("manual", {
                    "record_id": rid, "modules": [module],
                    "reason": "模块部分字段已有内容；默认不混写，请显式overwrite",
                })
            else:
                skipped_complete += 1
        if requested:
            tasks.append((row, requested))

    module_jobs = flatten_module_jobs(tasks)
    manifest["record_tasks"] = len(tasks)
    manifest["module_jobs"] = len(module_jobs)
    (run_dir / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"[init] records={len(rows)} record_tasks={len(tasks)} "
        f"module_jobs={len(module_jobs)} workers={args.workers} blank={skipped_blank} "
        f"invalid={skipped_invalid} unknown_stage={skipped_unknown_stage} "
        f"complete_modules={skipped_complete} partial_modules={partial}",
        flush=True,
    )
    if args.preflight:
        for row, requested in tasks:
            print(f"[preflight] {row.get('_record_id')}: {','.join(requested)}", flush=True)
        _preflight_knowledge(modules)
        logger.close()
        print(f"[runs] {run_dir}", flush=True)
        return 0
    stats = {"done": 0, "manual": partial, "errors": 0}
    stats_lock = threading.Lock()
    write_lock = threading.Lock()

    def event(record_id: str, module: str, status: str, **extra: Any) -> None:
        logger.emit("events", {
            "at": datetime.now().isoformat(timespec="seconds"),
            "record_id": record_id,
            "module": module,
            "status": status,
            **extra,
        })

    def process_module(row: dict[str, Any], module: str) -> None:

        # 同门店记录串行（日常模式历史读取/写回需要确定顺序）
        lock = None
        if visit_mode(row, profile) == "daily" and module in ("store-profile", "next-action"):
            master_rid = master_record_id_of(row, profile)
            if master_rid:
                lock = store_lock_for(master_rid)
        if lock:
            lock.acquire()
        try:
            rid = row["_record_id"]
            mode = visit_mode(row, profile)
            identity = {field: cell_text(row.get(field)) for field in profile.get("identity_fields", [])}
            notes = cell_text(row.get(profile.get("notes_field", ""))).strip()
            history = ""
            history_date = ""
            address = ""
            correction = ""
            action_history = ""
            if module == "store-profile":
                correction = cell_text(row.get(profile.get("correction_field", ""))).strip()
                if correction:
                    event(rid, module, "correction_injected")
                master_rid = master_record_id_of(row, profile)
                if master_rid:
                    address = read_master_address(profile, master_rid)
                    if address:
                        event(rid, module, "address_injected", details={"address": address})
                    if mode == "daily":
                        # 日常：读前 history_profiles 次档案（含日期标注）累计更新
                        found = find_history_profiles(
                            profile, master_rid, rid,
                            limit=int(profile.get("history_profiles", 3)),
                            current_date=row.get(profile.get("master_date_field")),
                        )
                        if found:
                            history = "\n\n".join(
                                f"进店时间：{date}\n{text}" for date, text in found
                            )
                            event(rid, module, "history_found", details={"count": len(found)})
                    else:
                        found = find_history_profile(profile, master_rid, rid)
                        if found:
                            history_date, history_text = found
                            history = f"进店时间：{history_date}\n{history_text}"
                            event(rid, module, "history_found", details={"date": history_date})
            elif module == "next-action" and mode == "daily":
                master_rid = master_record_id_of(row, profile)
                if master_rid:
                    current_date = row.get(profile.get("master_date_field"))
                    # 门店背景：前 history_profiles 次档案（与 store-profile 日常版一致）
                    found_profiles = find_history_profiles(
                        profile, master_rid, rid,
                        limit=int(profile.get("history_profiles", 3)),
                        current_date=current_date,
                    )
                    if found_profiles:
                        action_history += "该门店历史门店档案（门店背景参考）：\n" + "\n\n".join(
                            f"进店时间：{date}\n{text}" for date, text in found_profiles
                        ) + "\n\n"
                        event(rid, module, "profile_history_found", details={"count": len(found_profiles)})
                    found = find_last_actions(profile, master_rid, rid, current_date=current_date)
                    if found:
                        action_text, closure_text = found
                        action_history += f"上次下一步行动策略：\n{action_text}"
                        if closure_text:
                            action_history += f"\n\n上次总结闭环：\n{closure_text}"
                        event(rid, module, "action_history_found")
            retain_stable = bool(history) or bool(address)
            event(rid, module, "started")
            print(f"[{rid}][{module}] started (mode={mode})", flush=True)
            try:
                tr = parse_transcript(cell_text(row[profile["source_field"]]))
                selected, usages, isolated_errors = analyze(
                    tr, module, identity, args.model, args.temperature, notes=notes, history=history,
                    address=address, retain_stable=retain_stable, correction=correction,
                    mode=mode, action_history=action_history,
                )
                if selected is None:
                    logger.emit("errors", {
                        "record_id": rid, "modules": [module],
                        "reason": "独立模块两轮校验未通过",
                        "errors": isolated_errors,
                    })
                    event(rid, module, "validation_failed", errors=isolated_errors[:5])
                    with stats_lock:
                        stats["errors"] += 1
                    print(f"[{rid}][{module}] validation_failed", flush=True)
                    return

                event(rid, module, "primary_validated")
                selected_by_module, review_issues, review_usages = review_candidates(
                    selected, tr, [module], identity, args.model,
                    args.temperature, args.review, notes=notes,
                    address=address, retain_stable=retain_stable, correction=correction,
                    mode=mode, action_history=action_history,
                )
                usages.extend(review_usages)
                if review_issues and args.review_policy == "strict":
                    logger.emit("manual", {
                        "record_id": rid, "modules": [module],
                        "reason": "风险复核未收敛", "details": review_issues,
                    })
                    event(rid, module, "review_disputed", details=review_issues)
                    with stats_lock:
                        stats["manual"] += 1
                    print(f"[{rid}][{module}] review_disputed", flush=True)
                    return
                if review_issues:
                    event(
                        rid, module, "review_fallback_primary",
                        details=review_issues,
                    )
                    print(f"[{rid}][{module}] review_fallback_primary", flush=True)

                payload = render_module(
                    module, selected_by_module[module], tr, identity, rid, history_date, mode,
                    action_history, _closure_field_of(profile),
                )
                event(rid, module, "ready_to_write", fields=list(payload))
                if not args.dry_run:
                    with write_lock:
                        update_record(base, table, rid, payload)
                if module == "store-profile" and not args.dry_run:
                    try:
                        master_result = sync_master_profile(
                            profile, row, payload[MODULE_FIELDS["store-profile"][0]]
                        )
                        event(rid, module, "master_synced", details=master_result)
                        print(
                            f"[{rid}][store-profile] master_synced="
                            f"{len(master_result['fields'])}字段",
                            flush=True,
                        )
                    except Exception as exc:
                        event(rid, module, "master_sync_failed", reason=str(exc))
                        print(
                            f"[{rid}][store-profile] master_sync_failed: {str(exc)[:120]}",
                            flush=True,
                        )
                logger.emit("done", {
                    "record_id": rid,
                    "modules": [module],
                    "dry_run": args.dry_run,
                    "fields": payload,
                    "usage": usages,
                    "review_issues": review_issues,
                    "review_policy": args.review_policy,
                    "knowledge_refs": kb.collect_knowledge_refs(selected_by_module[module]),
                })
                event(rid, module, "dry_run" if args.dry_run else "written")
                with stats_lock:
                    stats["done"] += 1
                print(
                    f"[{rid}][{module}] {'dry-run' if args.dry_run else 'written'}",
                    flush=True,
                )
            except Exception as exc:
                logger.emit("errors", {
                    "record_id": rid, "modules": [module], "reason": str(exc),
                })
                event(rid, module, "exception", reason=str(exc))
                with stats_lock:
                    stats["errors"] += 1
                print(f"[{rid}][{module}] exception: {str(exc)[:160]}", flush=True)

        finally:
            if lock:
                lock.release()



    try:
        for row, module in module_jobs:
            event(row["_record_id"], module, "queued")
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = [
                executor.submit(process_module, row, module)
                for row, module in module_jobs
            ]
            for future in as_completed(futures):
                future.result()
    finally:
        logger.close()

    print(f"[done] {json.dumps(stats, ensure_ascii=False)}", flush=True)
    print(f"[runs] {run_dir}", flush=True)
    return 0 if stats["errors"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
