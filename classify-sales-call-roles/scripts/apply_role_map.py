#!/usr/bin/env python3
"""Apply a speaker-to-business-role map to diarized ASR JSON."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ALLOWED_ROLES = {"销售", "客户", "旁人"}
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


def stamp(ms: int) -> str:
    seconds, millis = divmod(max(0, int(ms)), 1000)
    minutes, sec = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minute:02d}:{sec:02d}.{millis:03d}"
    return f"{minute:02d}:{sec:02d}.{millis:03d}"


def parse_role_map(payload: Any) -> tuple[dict[str, str], list[dict[str, Any]], str]:
    if not isinstance(payload, dict):
        raise ValueError("Role map must be a JSON object")
    if "default" in payload:
        default = {str(key): value for key, value in payload.get("default", {}).items()}
        segments = payload.get("segments", [])
        fallback = payload.get("fallback", "旁人")
    else:
        default = {str(key): value for key, value in payload.items()}
        segments = []
        fallback = "旁人"
    values = list(default.values()) + [fallback]
    for segment in segments:
        values.extend(segment.get("map", {}).values())
    invalid = sorted({value for value in values if value not in ALLOWED_ROLES})
    if invalid:
        raise ValueError(f"Unsupported roles: {invalid}")
    return default, segments, fallback


def role_at(speaker: str, start: int, default: dict[str, str], segments: list[dict[str, Any]], fallback: str) -> str:
    role = default.get(speaker, fallback)
    for segment in segments:
        lower = int(segment.get("start_ms") or 0)
        upper = segment.get("end_ms")
        if start >= lower and (upper is None or start < int(upper)):
            role = {str(key): value for key, value in segment.get("map", {}).items()}.get(speaker, role)
    return role


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--role-map", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--json-output", action="store_true")
    args = parser.parse_args()

    source = json.loads(args.input.read_text(encoding="utf-8"))
    role_payload = json.loads(args.role_map.read_text(encoding="utf-8"))
    default, segments, fallback = parse_role_map(role_payload)
    utterances = []
    for item in utterances_from(source):
        utterances.extend(expand_inline_tags(item))

    labeled = []
    for item in utterances:
        role = role_at(item["speaker"], item["start"], default, segments, fallback)
        labeled.append({**item, "role": role})

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.json_output:
        args.output.write_text(json.dumps(labeled, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        lines = [
            f"[{stamp(item['start'])}–{stamp(item['end'])}] {item['role']}：{item['text']}"
            for item in labeled
        ]
        args.output.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({
        "utterances": len(labeled),
        "roles": {role: sum(1 for item in labeled if item["role"] == role) for role in sorted(ALLOWED_ROLES)},
        "output": str(args.output),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
