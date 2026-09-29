#!/usr/bin/env python3
"""Build a compact, deterministic speaker profile from diarized ASR JSON."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


SALES_TERMS = (
    "我们公司", "我们平台", "给您介绍", "给你介绍", "合作", "项目", "政策",
    "方案", "产品", "代理", "渠道", "陈列", "下单", "报价", "优势", "推广",
    "活动", "品牌方", "服务商", "老板您", "老板你", "我给你看", "我给你发",
    "你客户", "你的客户", "消费者", "拿货价", "后台返", "申请提现", "立即注册",
    "搜索", "安装", "操作方式", "利润是", "成本就是",
    "我把空罐给你", "在你这里买的多不多", "审批周期", "我先加你微信",
    "下单以后的赠品", "发朋友圈", "周一周二左右", "你自己考虑",
)
CUSTOMER_TERMS = (
    "我们门店", "我店里", "我们店", "顾客", "进货", "库存", "利润", "卖不动",
    "断货", "房租", "生意", "复购", "售后", "厂家", "成本", "我觉得", "我担心",
    "我不想", "我愿意", "经营", "店员", "我的顾客", "有人问我", "我不敢",
    "被罚", "被钓", "我拿货", "我们家", "加盟", "后悔", "卖得挺多", "熟客",
    "利润怎么来", "也是你", "在你这里买", "买的多不多", "需要再填",
    "都得重新填", "之前问的人", "现在倒是没", "门头照还得",
)
BYSTANDER_TERMS = (
    "正在接通", "请稍候", "喂你好", "抽烟吗", "慢点说", "拜拜", "锅",
    "妈妈", "阿姨", "小孩", "宝宝", "把鞋子脱", "收款", "玩去吧", "好好吃饭",
)
BOUNDARY_RE = re.compile(r"正在接通|请稍候|喂你好|拜拜|挂了|打个电话|接个电话")
INLINE_SPEAKER = re.compile(r"\[spk(\d+)\]\[(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\]")


def utterances_from(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    current = payload
    for key in ("data", "result", "utterances"):
        if not isinstance(current, dict) or key not in current:
            break
        current = current[key]
    if isinstance(current, list):
        return current
    if isinstance(payload, dict) and isinstance(payload.get("utterances"), list):
        return payload["utterances"]
    raise ValueError("No utterance array found in the input JSON")


def speaker_of(item: dict[str, Any]) -> str:
    additions = item.get("additions")
    if isinstance(additions, dict) and additions.get("speaker") is not None:
        return str(additions["speaker"])
    for key in ("speaker_id", "speaker", "spk"):
        if item.get(key) is not None:
            return str(item[key])
    return "unknown"


def time_of(item: dict[str, Any], start: bool) -> int:
    keys = ("start_time", "start_ms") if start else ("end_time", "end_ms")
    for key in keys:
        if item.get(key) is not None:
            return int(item[key])
    return 0


def expand_inline_tags(item: dict[str, Any]) -> list[dict[str, Any]]:
    """把 LAS 偶发的 utterance 内嵌 speaker 片段展开，避免画像漏 speaker。"""
    text = str(item.get("text") or "").strip()
    outer_speaker = speaker_of(item)
    outer_start, outer_end = time_of(item, True), time_of(item, False)
    matches = list(INLINE_SPEAKER.finditer(text))
    if not matches:
        return [{"speaker": outer_speaker, "start": outer_start, "end": outer_end, "text": text}] if text else []

    offset = outer_end - round(float(matches[-1].group(3)) * 1000)
    expanded: list[dict[str, Any]] = []
    leading = text[: matches[0].start()].strip()
    if leading:
        expanded.append({
            "speaker": outer_speaker,
            "start": outer_start,
            "end": offset + round(float(matches[0].group(2)) * 1000),
            "text": leading,
        })
    for index, match in enumerate(matches):
        content_start = match.end()
        content_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        content = text[content_start:content_end].strip()
        if content:
            expanded.append({
                "speaker": match.group(1),
                "start": offset + round(float(match.group(2)) * 1000),
                "end": offset + round(float(match.group(3)) * 1000),
                "text": content,
            })
    return expanded


def term_score(text: str, terms: tuple[str, ...]) -> int:
    return sum(2 for term in terms if term in text)


def role_hint(
    sales: int,
    customer: int,
    bystander: int,
    *,
    allow_bystander: bool = False,
) -> str | None:
    """Return only strong local evidence; weak windows remain undecided."""
    if max(sales, customer) >= 4 and abs(sales - customer) >= 2:
        return "销售" if sales > customer else "客户"
    if allow_bystander and bystander >= 4 and max(sales, customer) == 0:
        return "旁人"
    return None


def temporal_drift_sections(
    grouped: dict[str, list[dict[str, Any]]],
    window_ms: int,
) -> list[str]:
    sections = ["## Temporal role drift candidates", ""]
    found = False
    for speaker in sorted(
        grouped,
        key=lambda value: (not value.isdigit(), int(value) if value.isdigit() else value),
    ):
        windows: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for item in grouped[speaker]:
            windows[item["start"] // window_ms].append(item)
        summaries = []
        for bucket, items in sorted(windows.items()):
            scores = {
                "sales": sum(item["sales"] for item in items),
                "customer": sum(item["customer"] for item in items),
                "bystander": sum(item["bystander"] for item in items),
            }
            hint = role_hint(scores["sales"], scores["customer"], scores["bystander"])
            if hint is None:
                continue
            samples = sorted(
                items,
                key=lambda item: max(item["sales"], item["customer"], item["bystander"]) * 100
                + min(len(item["text"]), 120),
                reverse=True,
            )[:2]
            summaries.append((bucket, hint, scores, samples))
        observed = {hint for _, hint, _, _ in summaries}
        if not observed:
            continue
        found = True
        sections.extend([
            f"### speaker {speaker}",
            "",
            (
                f"- conflicting_local_roles: {', '.join(sorted(observed))}"
                if len(observed) >= 2
                else f"- strong_local_role: {next(iter(observed))}"
            ),
            "- strong windows:",
        ])
        for bucket, hint, scores, samples in summaries:
            lower, upper = bucket * window_ms, (bucket + 1) * window_ms
            sections.append(
                f"  - [{stamp(lower)}–{stamp(upper)}] dominant={hint}; "
                f"sales={scores['sales']}, customer={scores['customer']}, "
                f"bystander={scores['bystander']}"
            )
            for sample in sorted(samples, key=lambda item: item["start"]):
                compact = re.sub(r"\s+", " ", sample["text"])[:140]
                sections.append(f"    - [{stamp(sample['start'])}] {compact}")
        sections.append("")
    if not found:
        sections.append("- none")
        sections.append("")
    return sections


def stamp(ms: int) -> str:
    seconds, millis = divmod(max(0, int(ms)), 1000)
    minutes, sec = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minute:02d}:{sec:02d}.{millis:03d}"
    return f"{minute:02d}:{sec:02d}.{millis:03d}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-samples", type=int, default=8)
    parser.add_argument("--max-chars", type=int, default=10000)
    parser.add_argument("--window-ms", type=int, default=300000)
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    utterances = utterances_from(payload)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    boundaries: list[tuple[int, str]] = []
    previous_end: int | None = None

    for item in utterances:
        for segment in expand_inline_tags(item):
            text = segment["text"]
            start, end = segment["start"], segment["end"]
            speaker = segment["speaker"]
            sales = term_score(text, SALES_TERMS)
            customer = term_score(text, CUSTOMER_TERMS)
            bystander = term_score(text, BYSTANDER_TERMS)
            grouped[speaker].append({
                "text": text,
                "start": start,
                "end": end,
                "sales": sales,
                "customer": customer,
                "bystander": bystander,
                "rank": max(sales, customer, bystander) * 100 + min(len(text), 180),
            })
            if previous_end is not None and start - previous_end >= 15000:
                boundaries.append((start, f"静音/无发言间隔 {start - previous_end} ms"))
            if BOUNDARY_RE.search(text):
                boundaries.append((start, text[:80]))
            previous_end = max(previous_end or 0, end)

    sections: list[str] = [
        "# Speaker role profile",
        "",
        f"- utterances: {sum(len(items) for items in grouped.values())}",
        f"- speakers: {len(grouped)}",
        "",
    ]
    sections.extend(temporal_drift_sections(grouped, max(60000, args.window_ms)))

    for speaker in sorted(grouped, key=lambda value: (not value.isdigit(), int(value) if value.isdigit() else value)):
        items = grouped[speaker]
        duration = sum(max(0, item["end"] - item["start"]) for item in items)
        chars = sum(len(item["text"]) for item in items)
        scores = {
            "sales": sum(item["sales"] for item in items),
            "customer": sum(item["customer"] for item in items),
            "bystander": sum(item["bystander"] for item in items),
        }
        selected = sorted(items, key=lambda item: item["rank"], reverse=True)[: max(1, args.max_samples)]
        if items[0] not in selected:
            selected.append(items[0])
        if items[-1] not in selected:
            selected.append(items[-1])
        selected = sorted(selected, key=lambda item: item["start"])[: args.max_samples]
        sections.extend([
            f"## speaker {speaker}",
            "",
            f"- utterances: {len(items)}",
            f"- speech_ms: {duration}",
            f"- text_chars: {chars}",
            f"- evidence_scores: sales={scores['sales']}, customer={scores['customer']}, bystander={scores['bystander']}",
            "- representative utterances:",
        ])
        for item in selected:
            compact = re.sub(r"\s+", " ", item["text"])[:180]
            sections.append(f"  - [{stamp(item['start'])}] {compact}")
        sections.append("")

    if boundaries:
        sections.extend(["## Scene-boundary candidates", ""])
        seen: set[tuple[int, str]] = set()
        for start, reason in boundaries:
            key = (start, reason)
            if key in seen:
                continue
            seen.add(key)
            sections.append(f"- [{stamp(start)}] {reason}")
        sections.append("")

    output = "\n".join(sections)
    if len(output) > args.max_chars:
        output = output[: args.max_chars].rstrip() + "\n\n[profile truncated by --max-chars]\n"

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
