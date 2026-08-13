#!/usr/bin/env python3
"""store-profile-split 自检：解析器 fixtures + 与流水线映射一致性断言。

用法：
  python scripts/self_test.py            # 本地自检（不连飞书）
  python scripts/self_test.py --live     # 额外从飞书实读字段 ID 核对 config
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from split import (  # noqa: E402
    LABEL_TO_KEY,
    SECTION_LABELS,
    ONE_LINE_RE,
    SECTION_RE,
    parse_profile,
    load_config,
)

PIPELINE_PATH = (
    Path.home()
    / ".codex"
    / "skills"
    / "sales-call-analysis-scripted"
    / "scripts"
    / "pipeline.py"
)

# ---- fixtures：取自真实档案样例（结构按 pipeline.render_store_profile 输出）----

FULL_PROFILE = """一句话画像：位于嘉兴的医院周边母婴店，主营特配奶粉，曾销售雅培小安素，对德爱价格敏感，了解西港全球购但未装机。
门店基本信息（稳定档案）：门店位于嘉兴，属于医院周边门店。
主营品类与品牌（稳定档案）：主营特配奶粉，目前在售雀巢特配、达能特配；曾销售雅培小安素且销量很好。
经营模式（未确认）：未确认
选品偏好（当前状态）：对德爱价格敏感，了解西港。
利润偏好（当前状态）：客户认为达衣库（全球珍品优选）价格不好，收益不高。
合作偏好与排斥项（稳定档案）：了解西港全球购，曾申请注册但未成功装机。
关键证据：
[00:05.570–00:06.280] 客户：“有啊。”
[04:23.920–04:27.760] 客户：“因为你们那个就是价格并不好，达衣库的价格并不好。”
档案口径：单次录音快照；来源：record_id=recvrd5nruhTEG；门店编号=ST-001；门店名称=河南贝拉沐商贸有限公司。
证据边界：U0004–U0004排除（旁人闲聊）。"""

ALL_UNCONFIRMED = """一句话画像：客户明确表示自己不是老板，且多次拒绝沟通，未透露任何门店信息。
门店基本信息（未确认）：未确认
主营品类与品牌（未确认）：未确认
经营模式（未确认）：未确认
选品偏好（未确认）：未确认
利润偏好（未确认）：未确认
合作偏好与排斥项（未确认）：未确认
档案口径：单次录音快照；来源：record_id=recvqVXg3pCEkJ。"""

NO_BOUNDARY = """一句话画像：该门店当前：总部直采。
门店基本信息（稳定档案）：门店类型为专卖店。
主营品类与品牌（未确认）：未确认
经营模式（稳定档案）：总部直采。
选品偏好（未确认）：未确认
利润偏好（未确认）：未确认
合作偏好与排斥项（未确认）：未确认
关键证据：
[00:22.860–00:23.710] 客户：“对的对的对的”
档案口径：单次录音快照；来源：record_id=recvqVX2CmYucR。"""


def check(name: str, cond: bool, detail: str = "") -> None:
    if not cond:
        raise AssertionError(f"[FAIL] {name} {detail}")
    print(f"[ok] {name}")


def test_parse_full_profile() -> None:
    dims = parse_profile(FULL_PROFILE)
    check("完整档案-一句话画像",
          dims["one_line"] == "位于嘉兴的医院周边母婴店，主营特配奶粉，曾销售雅培小安素，对德爱价格敏感，了解西港全球购但未装机。")
    check("完整档案-basic", dims["basic"].startswith("门店位于嘉兴"))
    check("完整档案-未确认原样保留", dims["business_model"] == "未确认")
    check("完整档案-内容含全角括号不被截断",
          dims["price_profit"] == "客户认为达衣库（全球珍品优选）价格不好，收益不高。")
    check("完整档案-7 维度齐全",
          set(dims) == set(SECTION_LABELS) | {"one_line"} and all(dims.values()))


def test_parse_all_unconfirmed() -> None:
    dims = parse_profile(ALL_UNCONFIRMED)
    check("全未确认档案-每个维度原样写未确认",
          all(dims[k] == "未确认" for k in SECTION_LABELS))
    check("全未确认档案-一句话画像",
          dims["one_line"].startswith("客户明确表示"))


def test_parse_no_boundary() -> None:
    dims = parse_profile(NO_BOUNDARY)
    check("无证据边界档案-维度齐全", all(dims.values()))
    check("无证据边界档案-经营模式", dims["business_model"] == "总部直采。")


def test_parse_missing_section() -> None:
    # 异常数据：缺一个 section（正常不会发生，但要兜底为空字符串而非报错）
    text = "一句话画像：xxx。\n门店基本信息（稳定档案）：abc。\n档案口径：单次录音快照；来源：record_id=x。"
    dims = parse_profile(text)
    check("缺维度兜底为空", dims["selection_motion"] == "")
    check("缺失键补全", set(dims) == set(SECTION_LABELS) | {"one_line"})


def test_mapping_consistent_with_pipeline() -> None:
    """断言本 SKILL 的标签↔键映射与流水线 PROFILE_SECTIONS 一致。"""
    if not PIPELINE_PATH.exists():
        print("[skip] 找不到 pipeline.py，跳过映射一致性断言")
        return
    text = PIPELINE_PATH.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"PROFILE_SECTIONS\s*=\s*\{(.*?)\}", text, re.S)
    check("找到 pipeline.PROFILE_SECTIONS", bool(m))
    pairs = re.findall(r'"([a-z_]+)"\s*:\s*"([^"]+)"', m.group(1))
    pipeline_map = dict(pairs)
    check("映射完全一致", pipeline_map == SECTION_LABELS,
          f"pipeline={pipeline_map} split={SECTION_LABELS}")


def test_regex_anchors() -> None:
    # 只命中 6 个 section 行；关键证据/档案口径/证据边界 行不得被当成 section
    matches = SECTION_RE.findall(FULL_PROFILE)
    labels = [m[0] for m in matches]
    check("只命中 6 个 section", labels == list(SECTION_LABELS.values()), str(labels))
    check("one_line 正则锚定", ONE_LINE_RE.search(FULL_PROFILE).group(1).startswith("位于嘉兴"))


def test_config_shape() -> None:
    cfg = load_config("default")
    check("config.fields 键均为主档字段名", all(isinstance(k, str) for k in cfg["fields"]))
    check("config.fields 值均为档案键",
          set(cfg["fields"].values()) == set(SECTION_LABELS) | {"one_line"},
          f"values={set(cfg['fields'].values())}")


def test_live_field_ids() -> None:
    """--live：从飞书实读 01/02 表字段 ID，核对 config 中的字段名真实存在。"""
    from split import lark
    cfg = load_config("default")
    data = lark("base", "+field-list", "--base-token", cfg["base"],
                "--table-id", cfg["master_table"])
    names = {f["name"] for f in data["fields"]}
    missing = [k for k in cfg["fields"] if k not in names]
    check("主档目标字段全部存在", not missing, f"缺失: {missing}")
    check("主档字段名与档案标签一致",
          set(cfg["fields"]) == set(SECTION_LABELS.values()) | {"一句画像"})
    vdata = lark("base", "+field-list", "--base-token", cfg["base"],
                 "--table-id", cfg["visit_table"])
    vnames = {f["name"] for f in vdata["fields"]}
    for name in (cfg["visit_link_field"], cfg["visit_date_field"], cfg["visit_profile_field"]):
        check(f"02 表字段存在: {name}", name in vnames)


def main() -> None:
    parser = argparse.ArgumentParser(description="store-profile-split 自检")
    parser.add_argument("--live", action="store_true", help="额外从飞书实读核对字段")
    args = parser.parse_args()
    test_parse_full_profile()
    test_parse_all_unconfirmed()
    test_parse_no_boundary()
    test_parse_missing_section()
    test_mapping_consistent_with_pipeline()
    test_regex_anchors()
    test_config_shape()
    if args.live:
        test_live_field_ids()
    print("\nself_test: OK")


if __name__ == "__main__":
    main()
