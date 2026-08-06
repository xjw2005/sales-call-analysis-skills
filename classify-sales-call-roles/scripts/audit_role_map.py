#!/usr/bin/env python3
"""Audit a role map for time-local semantic conflicts without calling a model."""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from apply_role_map import parse_role_map, role_at
from build_role_profile import (
    BYSTANDER_TERMS,
    CUSTOMER_TERMS,
    SALES_TERMS,
    expand_inline_tags,
    role_hint,
    term_score,
    utterances_from,
)


def compact(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()[:180]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--role-map", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--window-ms", type=int, default=300000)
    args = parser.parse_args()

    source = json.loads(args.input.read_text(encoding="utf-8"))
    role_payload = json.loads(args.role_map.read_text(encoding="utf-8"))
    default, segments, fallback = parse_role_map(role_payload)
    window_ms = max(60000, args.window_ms)

    expanded: list[dict[str, Any]] = []
    for item in utterances_from(source):
        expanded.extend(expand_inline_tags(item))

    grouped: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
    for item in expanded:
        assigned = role_at(item["speaker"], item["start"], default, segments, fallback)
        enriched = {
            **item,
            "sales": term_score(item["text"], SALES_TERMS),
            "customer": term_score(item["text"], CUSTOMER_TERMS),
            "bystander": term_score(item["text"], BYSTANDER_TERMS),
        }
        grouped[(item["speaker"], item["start"] // window_ms, assigned)].append(enriched)

    conflicts: list[dict[str, Any]] = []
    for (speaker, bucket, assigned), items in sorted(grouped.items()):
        scores = {
            "sales": sum(item["sales"] for item in items),
            "customer": sum(item["customer"] for item in items),
            "bystander": sum(item["bystander"] for item in items),
        }
        proposed = role_hint(scores["sales"], scores["customer"], scores["bystander"])
        if proposed is None or proposed == assigned:
            continue
        samples = sorted(
            items,
            key=lambda item: {
                "销售": item["sales"],
                "客户": item["customer"],
                "旁人": item["bystander"],
            }[proposed] * 100 + min(len(item["text"]), 120),
            reverse=True,
        )[:3]
        conflicts.append({
            "speaker": speaker,
            "window_start_ms": bucket * window_ms,
            "window_end_ms": (bucket + 1) * window_ms,
            "first_evidence_ms": min(item["start"] for item in samples),
            "last_evidence_ms": max(item["end"] for item in samples),
            "assigned_role": assigned,
            "proposed_role": proposed,
            "scores": scores,
            "evidence": [
                {"start_ms": item["start"], "end_ms": item["end"], "text": compact(item["text"])}
                for item in sorted(samples, key=lambda item: item["start"])
            ],
        })

    conflict_counts: dict[str, int] = defaultdict(int)
    for item in conflicts:
        conflict_counts[item["speaker"]] += 1
    payload = {
        "window_ms": window_ms,
        "summary": {
            "utterances": len(expanded),
            "conflict_windows": len(conflicts),
            "conflict_speakers": sorted({item["speaker"] for item in conflicts}),
            "needs_repair": bool(conflicts),
            "severe": len(conflicts) >= 3 or any(count >= 2 for count in conflict_counts.values()),
        },
        "conflicts": conflicts,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
