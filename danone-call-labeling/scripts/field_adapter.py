"""Validate Feishu label fields and export update drafts. Never writes to Feishu."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pipeline

BINDINGS = json.loads((pipeline.ROOT / "references/feishu-fields.json").read_text(encoding="utf-8"))


def binding_for(table_id):
    if table_id not in BINDINGS["tables"]:
        raise ValueError("表未配置；仅支持已核对的三张副本表")
    return BINDINGS["tables"][table_id]


def parse_schema(raw):
    # Some CLI launchers prefix a code-page notice; accept the JSON envelope only.
    start = raw.find("{")
    if start < 0:
        raise ValueError("字段查询未返回JSON")
    response = json.loads(raw[start:])
    if response.get("ok") is not True:
        raise ValueError("字段查询失败；请核对飞书登录和只读权限")
    data = response.get("data", {})
    fields = data.get("fields")
    if not isinstance(fields, list) or data.get("total") != len(fields):
        raise ValueError("字段schema不完整，需完整读取后再适配")
    return fields


def read_schema(table_id, path=None):
    binding_for(table_id)
    if path:
        return parse_schema(path.read_text(encoding="utf-8-sig"))
    executable = shutil.which("lark-cli")
    if not executable:
        raise ValueError("找不到lark-cli；可通过--schema提供完整字段查询JSON")
    proc = subprocess.run([executable, "base", "+field-list", "--base-token", BINDINGS["base_token"],
                           "--table-id", table_id, "--limit", "200", "--as", "user", "--format", "json"],
                          capture_output=True, encoding="utf-8", errors="replace", timeout=60)
    if proc.returncode:
        raise ValueError("飞书字段读取失败；请检查登录和只读权限，未执行写操作")
    return parse_schema(proc.stdout)


def check_schema(table_id, fields):
    binding = binding_for(table_id)
    expected = pipeline.fields_for(binding["project"])
    by_id = {}
    for field in fields:
        if not isinstance(field, dict) or not isinstance(field.get("id"), str) or field["id"] in by_id:
            raise ValueError("schema包含缺失或重复字段id")
        by_id[field["id"]] = field
    issues = []
    if set(binding["fields"]) != set(expected):
        raise ValueError("本地绑定未覆盖该项目的全部标签字段")
    for name, field_id in binding["fields"].items():
        live = by_id.get(field_id)
        if not live:
            issues.append({"field": name, "issue": "missing_field_id"})
            continue
        if live.get("name") != name:
            issues.append({"field": name, "issue": "field_renamed"})
        if live.get("type") != "select" or live.get("multiple") is not True:
            issues.append({"field": name, "issue": "type_or_multiple_changed"})
        options = live.get("options", [])
        if not isinstance(options, list) or any(not isinstance(o, dict) or not isinstance(o.get("name"), str) for o in options):
            raise ValueError("schema选项结构无效")
        actual = [o["name"] for o in options]
        wanted = {o["value"] for o in expected[name]["options"]}
        if set(actual) != wanted or len(actual) != len(set(actual)):
            issues.append({"field": name, "issue": "options_changed",
                           "missing_options": sorted(wanted - set(actual)),
                           "extra_options": sorted(set(actual) - wanted)})
    extra = [f["name"] for f in fields if re.match(r"^[ABC]\d+ ", f.get("name", ""))
             and f["name"] not in expected]
    if extra:
        issues.append({"issue": "unmapped_label_fields", "fields": extra})
    return {"table_id": table_id, "project": binding["project"], "compatible": not issues,
            "field_count": len(expected), "option_count": sum(len(f["options"]) for f in expected.values()),
            "issues": issues}


def update_draft(table_id, rows, fields):
    report = check_schema(table_id, fields)
    if not report["compatible"]:
        raise ValueError("线上字段与配置不一致，停止映射：" + json.dumps(report["issues"], ensure_ascii=False))
    binding = binding_for(table_id)
    schema = pipeline.fields_for(binding["project"])
    updates = {}
    seen = set()
    for row in rows:
        if row.get("standard_sha256") != pipeline.CATALOG["standard_sha256"]:
            raise ValueError("分析结果标准版本不一致，请使用当前版本pipeline输出")
        record_id = row.get("id")
        if not isinstance(record_id, str) or not re.fullmatch(r"rec[A-Za-z0-9]+", record_id) or record_id in seen:
            raise ValueError("分析结果id需使用唯一的原表record_id，不能使用通话编号")
        seen.add(record_id)
        result = row.get("result", {})
        if result.get("project") != binding["project"]:
            raise ValueError("分析项目与目标表不一致，禁止跨项目映射")
        labels = result.get("labels")
        reviews = result.get("review_items")
        if not isinstance(labels, dict) or not isinstance(reviews, list):
            raise ValueError("需提供pipeline输出的labels和review_items")
        for review in reviews:
            if not isinstance(review, dict) or review.get("label") not in labels or not isinstance(review.get("reason"), str) or not review["reason"].strip():
                raise ValueError("复核项字段或原因无效")
        for name, values in labels.items():
            if name not in schema or not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                raise ValueError("分析结果含未知字段或非选项数组")
            options = {o["value"]: o for o in schema[name]["options"]}
            if len(values) != len(set(values)) or any(v not in options for v in values):
                raise ValueError("分析结果含非法或重复选项")
            if any(options[v]["rule_tier"] not in {"A", "B"} and v not in pipeline.defaults(binding["project"], name) for v in values):
                raise ValueError("C/D级选项不得自动导出")
        # Ignore any supplied business_fields; derive from labels/review_items again.
        mapped = {binding["fields"][name]: values for name, values in pipeline.business_fields(result).items()}
        if mapped:
            updates[record_id] = mapped
    return {"base_token": BINDINGS["base_token"], "table_id": table_id,
            "update_records": updates}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "map"])
    parser.add_argument("--table-id", required=True, choices=list(BINDINGS["tables"]))
    parser.add_argument("--schema", type=Path, help="该目标表完整field-list JSON；省略则只读查询飞书")
    parser.add_argument("--input", type=Path, help="pipeline分析输出NDJSON，id必须为原表record_id")
    parser.add_argument("--output", type=Path, help="新建本地更新草稿JSON；不会执行回填")
    args = parser.parse_args(argv)
    if args.command == "map" and (not args.input or not args.output):
        parser.error("map需要--input和--output")
    if args.command == "map" and args.output.exists():
        parser.error("输出已存在，请选择新文件")
    fields = read_schema(args.table_id, args.schema)
    if args.command == "check":
        report = check_schema(args.table_id, fields)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["compatible"] else 1
    rows = [json.loads(line) for line in args.input.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    if not rows:
        raise ValueError("输入不能为空")
    draft = update_draft(args.table_id, rows, fields)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(draft, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(f"已生成{len(draft['update_records'])}条本地字段映射草稿；未写入飞书")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"字段适配失败：{error}", file=sys.stderr)
        sys.exit(1)
