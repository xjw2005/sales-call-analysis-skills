#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""按新规则重判历史「是否有效对话」。

用法（在本 Skill 目录）:
    python scripts/rejudge_validity.py --profile visit_prod --view vewIf60qAA --dry-run
    python scripts/rejudge_validity.py --profile visit_prod --view vewIf60qAA --write

行为:
- 拉取视图记录，只处理「是否有效对话 = 有效」且转写非空的记录（当前判有效是误判重灾区）。
- 每条用新 judge_validity 重判（可能得「录音过短」或「内容无效」）；时长优先用本地 runs 里的
  音频总时长（多附件用 state 顶层总时长），查不到用转写末行时间戳兜底。
- 模型判断失败的记录（source=model_error）**不进清单也不写**，保持原值待人工复核。
- --dry-run：只打印「新判为无效」的清单与原因，不写飞书。
- --write：写回前每条复核「当前仍=有效」且「本次重判仍为无效类值」双保险后才写；
  复核不过的跳过并报告。人工改过的记录无法从字段区分，清单在写回前打印供人工过目。
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import pipeline  # noqa: E402


def text_of(value: Any) -> str:
    """把飞书单元格值归一成文本（单选字段常是 ["有效"]）。"""
    if value is None:
        return ""
    if isinstance(value, list):
        return "".join(str(item) for item in value)
    return str(value)


def write_value(profile: dict[str, Any], record_id: str, field: str, value: str) -> None:
    """写回字段并复读一致；写不进去就抛错。"""
    data = pipeline.lark(
        "base", "+record-upsert",
        "--base-token", profile["base_token"],
        "--table-id", profile["table_id"],
        "--record-id", record_id,
        "--json", json.dumps({field: value}, ensure_ascii=False),
    )
    if not data.get("updated"):
        raise RuntimeError("飞书没有确认更新")
    for attempt in range(3):
        if text_of(pipeline.read_record_field(profile, record_id, field)) == value:
            return
        time.sleep(attempt + 1)
    raise RuntimeError("飞书写后复读不一致")


def build_duration_index() -> dict[str, float]:
    """本地时长索引：record_id -> 音频总时长（秒）。

    多附件录音的合并转写要用 state 顶层总时长（各附件时长之和）；
    顶层缺失时用单条附件时长兜底。
    """
    index: dict[str, float] = {}
    for st in glob.glob(str(ROOT / "runs" / "*" / "state.json")):
        try:
            state = json.loads(Path(st).read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  警告：{st} 读取失败跳过（{exc}）", file=sys.stderr)
            continue
        total = state.get("duration_seconds")
        for rid, rec in state.get("records", {}).items():
            d = total or (rec or {}).get("duration_seconds")
            if d and rid not in index:
                index[rid] = float(d)
    return index


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--view", required=True)
    parser.add_argument("--dry-run", action="store_true", help="只出清单不写飞书（默认）")
    parser.add_argument("--write", action="store_true", help="正式写回判为无效的记录（写前逐条复核）")
    args = parser.parse_args()

    cfg = pipeline.load_config()
    profile = cfg["profiles"][args.profile]
    valid_field = profile.get("valid_field")
    if not valid_field:
        raise RuntimeError(f"profile {args.profile} 未配置 valid_field")

    duration_index = build_duration_index()
    rows = pipeline.fetch_rows(profile, args.view, 0, 0)  # count=0 表示全量
    candidates = []
    for row in rows:
        rid = row.get("_record_id")
        if not rid:
            continue
        current = text_of(row.get(valid_field))
        labeled = text_of(row.get(profile["transcript_field"])).strip()
        if current != "有效" or not labeled:
            continue
        duration = duration_index.get(rid)
        candidates.append((rid, row, labeled, duration))

    print(f"当前判「有效」的记录: {len(candidates)} 条（其中本地有时长的 {sum(1 for c in candidates if c[3])} 条）")

    to_invalidate: list[tuple[str, dict, str]] = []
    skipped_model_error = 0
    for rid, row, labeled, duration in candidates:
        verdict = pipeline.judge_validity(cfg, profile, labeled, duration)
        if verdict["source"] == "model_error":
            skipped_model_error += 1
            print(f"  （跳过：{rid} 模型判断失败，保持原值待人工复核）")
            continue
        if verdict["value"] in {"录音过短", "内容无效"}:
            to_invalidate.append((rid, row, verdict))

    print(f"\n重判后应改为「录音过短/内容无效」: {len(to_invalidate)} 条"
          f"{f'（另有 {skipped_model_error} 条模型失败未处理）' if skipped_model_error else ''}\n")
    for rid, row, verdict in to_invalidate:
        identity = " / ".join(
            text_of(row.get(f)) for f in profile.get("identity_fields", []) if row.get(f)
        )
        print(f"- {rid}  {identity}  ->  {verdict['value']}\n    原因: {verdict['reason']}")

    if args.write:
        if not to_invalidate:
            print("\n无记录需要修改")
            return 0
        print(f"\n正式写回 {len(to_invalidate)} 条（录音过短/内容无效）...")
        written, skipped = 0, 0
        for rid, row, verdict in to_invalidate:
            # 写前双保险复核：当前仍=有效，且本次重判仍为无效类值（防模型波动与人工改动）
            now_current = text_of(pipeline.read_record_field(profile, rid, valid_field))
            if now_current != "有效":
                print(f"  ✗ {rid} 当前值已变（{now_current}），跳过")
                skipped += 1
                continue
            verdict = pipeline.judge_validity(cfg, profile, text_of(row.get(profile["transcript_field"])).strip(), duration_index.get(rid))
            if verdict["value"] not in {"录音过短", "内容无效"}:
                print(f"  ✗ {rid} 本次重判为「{verdict['value']}」，跳过（模型波动）")
                skipped += 1
                continue
            write_value(profile, rid, valid_field, verdict["value"])
            print(f"  ✓ {rid} -> {verdict['value']}")
            written += 1
        print(f"完成：写入 {written} 条，跳过 {skipped} 条。")
    else:
        print("\n（未写飞书。确认清单后加 --write 正式回写；写前会逐条复核）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
