#!/usr/bin/env python3
"""把最新拜访档案拆解写回门店主档具体字段。

承接 sales-call-analysis-scripted 的 store-profile 模块：分析写完 02 门店拜访记录
「门店档案」后，本脚本把每条主档记录对应的最新一次档案（进店时间最新、且档案
非空）解析为 7 个维度，填入 01 门店主档对应字段，让主档可读、可筛选。

设计约束（用户 2026-08-06 确认）：
- 源 = 02 表进店时间最新一条「门店档案」单份文本（不解析「门店档案（总）」拼接）
- 只填空字段：目标字段已有内容则跳过（铁律 10），提供 --overwrite 强制覆盖
- 未确认项：按档案原文口径，档案该维度写「未确认」就原样写「未确认」
- 不合并多次档案（单次快照铁律）；不改飞书字段结构；关键证据/档案口径/证据边界不进主档
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.local.json"

# 档案维度标签 ↔ 键（与 sales-call-analysis-scripted/scripts/pipeline.py PROFILE_SECTIONS 一致，
# 由 self_test.py 断言保持一致）
SECTION_LABELS = {
    "basic": "门店基本信息",
    "categories_brands": "主营品类与品牌",
    "business_model": "经营模式",
    "selection_motion": "选品偏好",
    "price_profit": "利润偏好",
    "cooperation_preferences": "合作偏好与排斥项",
}
LABEL_TO_KEY = {label: key for key, label in SECTION_LABELS.items()}
ONE_LINE_LABEL = "一句话画像"
STATE_TYPES = "稳定档案|当前状态|未确认"

ONE_LINE_RE = re.compile(r"(?m)^一句话画像：(.+)$")
SECTION_RE = re.compile(
    r"(?m)^(" + "|".join(re.escape(label) for label in SECTION_LABELS.values())
    + r")（(?:" + STATE_TYPES + r")）：(.*)$"
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


def lark(*args: str, retries: int = 3) -> dict:
    """调用 lark-cli，返回 data 部分；失败重试。"""
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


def rows_of(data: dict) -> list[dict]:
    """record-list/record-get 返回的 data 转行列表。"""
    fields = data.get("fields", [])
    ids = data.get("record_id_list", [])
    rows = []
    for idx, values in enumerate(data.get("data", [])):
        row = dict(zip(fields, values))
        row["_record_id"] = ids[idx] if idx < len(ids) else None
        rows.append(row)
    return rows


def cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "、".join(str(x) for x in value)
    return str(value)


def field_blank(value) -> bool:
    return value is None or value == "" or value == []


def parse_profile(text: str) -> dict[str, str]:
    """解析单份门店档案文本 → {one_line, basic, ..., cooperation_preferences}。

    档案格式（pipeline.render_store_profile 输出）：
      一句话画像：xxx
      门店基本信息（稳定档案）：xxx
      ...
      关键证据：\n[时间戳] 角色：“原话”…
      档案口径：单次录音快照；来源：record_id=xxx；…
      证据边界：…（可选）
    """
    dims: dict[str, str] = {}
    m = ONE_LINE_RE.search(text)
    if m:
        dims["one_line"] = m.group(1).strip()
    seen = set()
    for m in SECTION_RE.finditer(text):
        key = LABEL_TO_KEY[m.group(1)]
        if key in seen:
            continue  # 同一维度多行（异常）时取第一行
        seen.add(key)
        dims[key] = m.group(2).strip()
    for key in list(SECTION_LABELS) + ["one_line"]:
        dims.setdefault(key, "")
    return dims


def load_config(profile: str) -> dict:
    if not CONFIG_PATH.exists():
        raise SystemExit(f"缺少配置: {CONFIG_PATH}（从 config.example.json 复制并填写）")
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    profiles = cfg.get("profiles") or {}
    if profile not in profiles:
        raise SystemExit(f"profile '{profile}' 不存在，可用: {', '.join(profiles)}")
    return profiles[profile]


def find_latest_profile(cfg: dict, master_record_id: str) -> dict | None:
    """查 02 拜访记录中该门店关联的、进店时间最新且档案非空的一条。"""
    base = cfg["base"]
    table = cfg["visit_table"]
    link_field = cfg["visit_link_field"]
    date_field = cfg["visit_date_field"]
    profile_field = cfg["visit_profile_field"]
    filter_json = json.dumps(
        {"logic": "and", "conditions": [[link_field, "intersects", [master_record_id]]]},
        ensure_ascii=False,
    )
    sort_json = json.dumps([{"field": date_field, "desc": True}], ensure_ascii=False)
    offset = 0
    while True:
        data = lark(
            "base", "+record-list", "--base-token", base, "--table-id", table,
            "--filter-json", filter_json, "--sort-json", sort_json,
            "--field-id", date_field, "--field-id", profile_field,
            "--limit", "200", "--offset", str(offset),
        )
        page = rows_of(data)
        for row in page:
            text = cell_text(row.get(profile_field))
            if text.strip():
                return {
                    "visit_record_id": row["_record_id"],
                    "profile_text": text.strip(),
                }
        if not data.get("has_more") or not page:
            break
        offset += 200
    return None


def collect_master(cfg: dict, record_ids: list[str]) -> list[dict]:
    """读 01 主档记录；只投影 7 个目标字段。"""
    base = cfg["base"]
    table = cfg["master_table"]
    fields = list(cfg["fields"].keys())
    rows: list[dict] = []
    if record_ids:
        args = [
            "base", "+record-get", "--base-token", base, "--table-id", table,
        ]
        for rid in record_ids:
            args.extend(["--record-id", rid])
        for f in fields:
            args.extend(["--field-id", f])
        data = lark(*args)
        rows = rows_of(data)
    else:
        offset = 0
        while True:
            args = [
                "base", "+record-list", "--base-token", base, "--table-id", table,
                "--limit", "200", "--offset", str(offset),
            ]
            for f in fields:
                args.extend(["--field-id", f])
            data = lark(*args)
            page = rows_of(data)
            rows.extend(page)
            if not data.get("has_more") or not page:
                break
            offset += 200
    return rows


def build_patch(
    cfg: dict, master_row: dict, dims: dict[str, str], overwrite: bool,
) -> tuple[dict, dict]:
    """对照主档当前值构造写回 patch；返回 (patch, stats)。"""
    patch: dict[str, str] = {}
    stats = {"filled": 0, "skipped_existing": 0, "blank_value": 0}
    for field, dim_key in cfg["fields"].items():
        value = dims.get(dim_key, "").strip()
        if not field_blank(master_row.get(field)) and not overwrite:
            stats["skipped_existing"] += 1
            continue
        if not value:
            stats["blank_value"] += 1
            continue
        patch[field] = value
        stats["filled"] += 1
    return patch, stats


def update_record(cfg: dict, record_id: str, field: str, value: str) -> None:
    data = lark(
        "base", "+record-upsert", "--base-token", cfg["base"],
        "--table-id", cfg["master_table"], "--record-id", record_id,
        "--json", json.dumps({field: value}, ensure_ascii=False),
    )
    if not data.get("updated"):
        raise RuntimeError(
            f"飞书未确认更新字段{field}: {json.dumps(data, ensure_ascii=False)[:300]}"
        )


def read_back(cfg: dict, record_id: str, field: str) -> str:
    data = lark(
        "base", "+record-get", "--base-token", cfg["base"],
        "--table-id", cfg["master_table"], "--record-id", record_id,
        "--field-id", field,
    )
    rows = rows_of(data)
    if not rows:
        raise RuntimeError(f"复读失败，无记录 {record_id}")
    return cell_text(rows[0].get(field))


def main() -> None:
    parser = argparse.ArgumentParser(description="把最新拜访档案拆解写回门店主档具体字段")
    parser.add_argument("--profile", default="default", help="config.local.json 中的 profile")
    parser.add_argument("--dry-run", action="store_true", help="只预览将写入的值，不写飞书")
    parser.add_argument("--record-ids", default="", help="指定主档记录（逗号分隔）；默认全表")
    parser.add_argument("--overwrite", action="store_true", help="目标字段已有内容时也覆盖（默认跳过）")
    parser.add_argument("--verbose", action="store_true", help="逐条打印详情")
    args = parser.parse_args()

    cfg = load_config(args.profile)
    record_ids = [r.strip() for r in args.record_ids.split(",") if r.strip()]
    master_rows = collect_master(cfg, record_ids)
    print(f"主档记录 {len(master_rows)} 条"
          + ("" if not record_ids else f"（指定 {len(record_ids)} 条）"))

    total = {"records": 0, "no_profile": 0, "filled_fields": 0,
             "skipped_existing": 0, "recheck_fail": 0, "parse_missing": 0}
    for row in master_rows:
        record_id = row["_record_id"]
        if not record_id:
            continue
        found = find_latest_profile(cfg, record_id)
        if not found:
            total["no_profile"] += 1
            if args.verbose:
                print(f"[{record_id}] 无最新档案，跳过")
            continue
        dims = parse_profile(found["profile_text"])
        missing = [k for k in dims if not dims[k]]
        if missing:
            total["parse_missing"] += 1
            if args.verbose:
                print(f"[{record_id}] 档案缺少维度: {missing}")
        patch, stats = build_patch(cfg, row, dims, args.overwrite)
        total["records"] += 1
        total["filled_fields"] += stats["filled"]
        total["skipped_existing"] += stats["skipped_existing"]
        if not patch:
            if args.verbose:
                print(f"[{record_id}] 无需更新"
                      + (f"（已有内容 {stats['skipped_existing']} 项）" if stats["skipped_existing"] else ""))
            continue
        print(f"[{record_id}] 来源拜访 {found['visit_record_id']}")
        for field, value in patch.items():
            preview = value if len(value) <= 60 else value[:60] + "…"
            print(f"  → {field}: {preview}")
        if args.dry_run:
            continue
        for field, value in patch.items():
            update_record(cfg, record_id, field, value)
        # 写后复读（沿用流水线惯例：逐字段复读一致才记 done）
        for field, value in patch.items():
            actual = read_back(cfg, record_id, field)
            if actual != value:
                total["recheck_fail"] += 1
                print(f"  ✗ 复读不一致 {field}: 期望 {value[:50]}… 实际 {actual[:50]}…")
            elif args.verbose:
                print(f"  ✓ {field} 复读一致")

    print("\n汇总: 处理主档 {records} 条 / 无档案跳过 {no_profile} 条"
          .format(**total)
          + f" / 填入字段 {total['filled_fields']} 个 / 已有内容跳过 {total['skipped_existing']} 个"
          + f" / 档案缺维度 {total['parse_missing']} 条 / 复读不一致 {total['recheck_fail']} 个")
    if args.dry_run:
        print("（dry-run 模式，未写飞书）")


if __name__ == "__main__":
    main()
