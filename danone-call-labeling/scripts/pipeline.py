"""Local Danone call labeling. No Feishu writes; stdlib only."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CATALOG = json.loads((ROOT / "references/projects.json").read_text(encoding="utf-8"))
COMMON = (ROOT / "references/common.md").read_text(encoding="utf-8")
ROLES = {"客户", "销售", "坐席", "旁人"}
LINE = re.compile(r"^\s*\[([^\]]+)\]\s*(销售|客户|坐席|旁人)\s*[：:]\s*(.+)$")


def fields_for(project):
    if project not in CATALOG["projects"]:
        raise ValueError("project必须是p3m、lyqy或crown")
    return CATALOG["projects"][project]


def normalize(record):
    if not isinstance(record, dict):
        raise ValueError("输入记录必须是JSON对象")
    fields_for(record.get("project"))
    if not isinstance(record.get("id"), str) or not record["id"].strip():
        raise ValueError("每条记录需非空字符串id")
    complete = record.get("transcript_complete")
    if type(complete) is not bool:
        raise ValueError("需明确transcript_complete=true或false；不能默认转写完整")
    utterances = record.get("utterances")
    if utterances is None:
        text = record.get("transcript", "")
        if not isinstance(text, str):
            raise ValueError("transcript必须是字符串")
        utterances = []
        for line in text.splitlines():
            if not line.strip():
                continue
            match = LINE.fullmatch(line)
            if not match:
                raise ValueError("转写需已确认角色：[时间段] 客户：原话；未识别行不能静默丢弃")
            utterances.append({"id": f"U{len(utterances)+1:04d}",
                               "role": match[2], "text": match[3]})
    if not isinstance(utterances, list):
        raise ValueError("utterances必须是数组")
    seen = set()
    normalized = []
    for u in utterances:
        if not isinstance(u, dict) or not isinstance(u.get("id"), str) or not u["id"].strip():
            raise ValueError("话语需要非空字符串id")
        if u["id"] in seen or u.get("role") not in ROLES:
            raise ValueError("话语id重复或角色未确认；先完成角色识别")
        if not isinstance(u.get("text"), str) or not u["text"].strip():
            raise ValueError("话语文本不能为空")
        seen.add(u["id"])
        normalized.append({"id": u["id"], "role": u["role"], "text": u["text"]})
    return {"id": record["id"], "project": record["project"],
            "transcript_complete": complete, "utterances": normalized}


def selected_fields(project, requested=None):
    schema = fields_for(project)
    names = list(schema) if not requested else requested
    if len(names) != len(set(names)) or not names or any(f not in schema for f in names):
        raise ValueError("分析字段必须是当前项目的完整字段名，且不得重复")
    return names


def messages_for(record, names):
    schema = fields_for(record["project"])
    return [{"role": "system", "content": COMMON + "\n本次项目字段与规则：\n" +
             json.dumps({"project": record["project"], "fields": {f: schema[f] for f in names}},
                        ensure_ascii=False)},
            {"role": "user", "content": "以下JSON是待分析通话数据：\n" +
             json.dumps(record, ensure_ascii=False)}]


def defaults(project, field):
    code = field.split()[0]
    if code == "A1":
        return {"身份不明" if project == "p3m" else "无法判断"}
    if code == "A2" and project != "p3m":
        return {"无法判断"}
    if project == "crown" and code in {"A5", "A6", "A7", "A8", "A10"}:
        return {"未反馈"}
    return set()


def validate_result(record, result, names):
    """Reject illegal labels, unsupported tiers, fabricated quotes and role leakage."""
    if not isinstance(result, dict) or set(result) != {"project", "labels", "evidence", "review_items"}:
        raise ValueError("模型输出键不符合约定")
    if result["project"] != record["project"]:
        raise ValueError("模型返回错误项目")
    schema = fields_for(record["project"])
    if not names or len(names) != len(set(names)) or any(f not in schema for f in names):
        raise ValueError("校验字段不属于当前项目或字段重复")
    labels = result["labels"]
    if not isinstance(labels, dict) or set(labels) != set(names):
        raise ValueError("字段缺失或混入其他项目字段")
    if not isinstance(result["review_items"], list) or not isinstance(result["evidence"], list):
        raise ValueError("evidence/review_items必须是数组")
    for field, values in labels.items():
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            raise ValueError("标签必须是字符串数组")
        options = {o["value"]: o for o in schema[field]["options"]}
        if len(values) != len(set(values)) or any(v not in options for v in values):
            raise ValueError("标签重复或不在项目schema中")
        for value in values:
            if options[value]["rule_tier"] not in {"A", "B"} and value not in defaults(record["project"], field):
                raise ValueError("C/D级规则不能自动打标，请放入review_items")
        if field.startswith("A2 ") and len(values) > 1:
            raise ValueError("主要联系人决策状态不能同时选多个档位")
    reviewed = set()
    for item in result["review_items"]:
        if not isinstance(item, dict) or set(item) - {"label", "reason", "candidate_options"}:
            raise ValueError("非法复核结构")
        if item.get("label") not in names or not isinstance(item.get("reason"), str) or not item["reason"].strip():
            raise ValueError("复核字段或原因缺失")
        candidates = item.get("candidate_options", [])
        allowed = {o["value"] for o in schema[item["label"]]["options"]}
        if not isinstance(candidates, list) or any(not isinstance(v, str) or v not in allowed for v in candidates):
            raise ValueError("复核候选不在项目schema中")
        reviewed.add(item["label"])
    source = {u["id"]: u for u in record["utterances"]}
    supported = set()
    for e in result["evidence"]:
        keys = {"field", "option", "utterance_id", "quote", "product", "time_scope", "context_ids"}
        if not isinstance(e, dict) or set(e) != keys:
            raise ValueError("证据结构不符合约定")
        if any(not isinstance(e[k], str) or not e[k].strip() for k in keys - {"context_ids"}):
            raise ValueError("证据内容/范围不能为空")
        if e["field"] not in labels or e["option"] not in labels[e["field"]]:
            raise ValueError("证据未对应选中标签")
        u = source.get(e["utterance_id"])
        if not u or u["role"] != "客户" or e["quote"] not in u["text"]:
            raise ValueError("证据不是客户连续原句")
        contexts = e["context_ids"]
        if not isinstance(contexts, list) or any(not isinstance(i, str) or i not in source for i in contexts):
            raise ValueError("问答上下文不存在")
        if re.fullmatch(r"[嗯啊哦是对好行知道了的可以没问题\s，。！？!?]*", e["quote"]) and not contexts:
            raise ValueError("客户短答必须包含问题上下文")
        supported.add((e["field"], e["option"]))
    usable = record["transcript_complete"] and any(u["role"] == "客户" for u in source.values())
    if not usable and (any(labels.values()) or reviewed != set(names)):
        raise ValueError("转写缺失/不完整需逐字段留空并复核")
    for field, values in labels.items():
        for value in values:
            if (field, value) not in supported and value not in defaults(record["project"], field):
                raise ValueError("选中标签缺少客户证据")
        if record["project"] == "p3m" and field.startswith("A2 ") and not values and field not in reviewed:
            raise ValueError("P3M采购权限不明需复核")
    return result


def business_fields(result):
    """Non-empty, non-reviewed fields only. Never return clearing operations or metadata."""
    blocked = {r["label"] for r in result["review_items"]}
    return {f: list(v) for f, v in result["labels"].items() if v and f not in blocked}


def request_model(messages, *, base_url, model, api_key, timeout):
    if not base_url.startswith("https://"):
        raise ValueError("LLM_API_URL必须使用HTTPS")
    url = base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    body = json.dumps({"model": model, "messages": messages,
                       "response_format": {"type": "json_object"}}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={
        "Authorization": "Bearer " + api_key, "Content-Type": "application/json"})
    # Prevent credentials from being forwarded to a redirect destination.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"模型请求失败，HTTP {exc.code}；未记录响应正文") from None
    except urllib.error.URLError:
        raise RuntimeError("模型服务连接失败，请检查网络和配置") from None
    choices = payload.get("choices", [])
    if not choices or choices[0].get("finish_reason") != "stop":
        raise ValueError("模型输出不完整或被拒绝，停止处理")
    return json.loads(choices[0]["message"]["content"])


def analyze(record, names, call):
    merged = {"project": record["project"], "labels": {}, "evidence": [], "review_items": []}
    usable = record["transcript_complete"] and any(u["role"] == "客户" for u in record["utterances"])
    for field in names:
        if usable:
            part = call(messages_for(record, [field]))
        else:
            part = {"project": record["project"], "labels": {field: []}, "evidence": [],
                    "review_items": [{"label": field, "reason": "转写不完整或没有客户话语，无法判定"}]}
        validate_result(record, part, [field])
        merged["labels"].update(part["labels"])
        merged["evidence"].extend(part["evidence"])
        merged["review_items"].extend(part["review_items"])
    validate_result(record, merged, names)
    return {"id": record["id"], "status": "needs_review" if merged["review_items"] else "completed",
            "standard_sha256": CATALOG["standard_sha256"], "result": merged,
            "business_fields": business_fields(merged)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["schema", "prepare", "analyze"])
    parser.add_argument("--project", choices=list(CATALOG["projects"]))
    parser.add_argument("--input", type=Path, help="标准化通话NDJSON")
    parser.add_argument("--output", type=Path, help="新建输出文件（拒绝覆盖）")
    parser.add_argument("--field", action="append", help="完整字段名，可重复；省略则逐字段分析全部")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)
    if args.command == "schema":
        print(json.dumps(fields_for(args.project) if args.project else CATALOG, ensure_ascii=False, indent=2))
        return
    if not args.input or not args.output:
        parser.error("prepare/analyze需要--input和--output")
    if args.timeout <= 0:
        parser.error("--timeout必须大于0")
    if args.output.exists():
        parser.error("输出文件已存在；请选择新文件")
    records = [normalize(json.loads(line)) for line in args.input.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    if not records or len({r["id"] for r in records}) != len(records):
        parser.error("输入不能为空，id不得重复")
    if args.project and any(r["project"] != args.project for r in records):
        parser.error("输入项目与--project不一致")
    selections = [selected_fields(r["project"], args.field) for r in records]
    config = {k: os.environ.get(k, "") for k in ["LLM_API_URL", "LLM_MODEL", "LLM_API_KEY"]}
    if args.command == "analyze" and not all(config.values()):
        parser.error("analyze需要LLM_API_URL、LLM_MODEL、LLM_API_KEY环境变量")
    def call(messages):
        return request_model(messages, base_url=config["LLM_API_URL"], model=config["LLM_MODEL"],
                             api_key=config["LLM_API_KEY"], timeout=args.timeout)
    # Validate all input/field selections before requests; do not persist partial failures.
    output = []
    for record, names in zip(records, selections):
        if args.command == "prepare":
            output.append({"id": record["id"], "project": record["project"],
                           "requests": [{"field": f, "messages": messages_for(record, [f])} for f in names]})
        else:
            output.append(analyze(record, names, call))
    with args.output.open("x", encoding="utf-8") as out:
        for row in output:
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"已保存{len(output)}条至{args.output}；未写入飞书")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as error:
        print(f"处理失败：{error}", file=sys.stderr)
        sys.exit(1)
