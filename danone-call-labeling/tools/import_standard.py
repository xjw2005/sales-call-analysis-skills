"""Export schema and rule definitions, excluding customer quotes and record IDs."""
import argparse
import hashlib
import json
import re
from pathlib import Path

PROJECTS = {"P3M": "p3m", "lyqy": "lyqy", "皇冠": "crown"}


def build(standard, schema_dir):
    text = standard.read_text(encoding="utf-8-sig")
    projects = {}
    for project in PROJECTS.values():
        raw = (schema_dir / f"f_{project}.json").read_text(encoding="utf-8-sig")
        fields = json.loads(raw[raw.index("{"):])["data"]["fields"]
        projects[project] = {f["name"]: {
            "multiple": f["multiple"],
            "options": [{"value": o["name"]} for o in f["options"]],
        } for f in fields if re.match(r"^[ABC]\d+ ", f["name"])}
    seen = set()
    for section in re.split(r"(?m)^### ", text):
        applicable = re.search(r"适用字段：([^\n]+)", section)
        if not applicable:
            continue
        targets = re.findall(r"(P3M|lyqy|皇冠) `([^`]+)`", applicable[1])
        boundary = re.search(r"(?m)^分界与触发：([^\n]+)", section)
        for name, field in targets:
            projects[PROJECTS[name]][field]["boundary"] = boundary[1] if boundary else "按通用规则执行。"
        for block in re.split(r"(?m)^#### ", section)[1:]:
            option = block.splitlines()[0].strip()
            definition = re.search(r"(?m)^操作定义(?:与相邻边界)?：([^\n]+)", block)
            statuses = re.search(r"(?m)^项目证据状态：([^\n]+)", block)
            if not definition or not statuses:
                raise ValueError(f"Missing rule/status: {option}")
            tiers = dict(re.findall(r"(P3M|lyqy|皇冠) ([ABCD])：", statuses[1]))
            for name, field in targets:
                project = PROJECTS[name]
                matches = [o for o in projects[project][field]["options"] if o["value"] == option]
                if not matches:
                    continue
                if name not in tiers:
                    raise ValueError(f"Missing project tier: {project}/{field}/{option}")
                matches[0].update(rule=definition[1], rule_tier=tiers[name])
                seen.add((project, field, option))
    expected = {(p, f, o["value"]) for p, fields in projects.items()
                for f, spec in fields.items() for o in spec["options"]}
    if seen != expected:
        raise ValueError(f"Unmapped schema options: {expected - seen}")
    return {"version": 1, "standard_sha256": hashlib.sha256(standard.read_bytes()).hexdigest(),
            "projects": projects}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--standard", type=Path, required=True)
    parser.add_argument("--schema-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.standard, args.schema_dir)
    with args.output.open("x", encoding="utf-8") as out:
        json.dump(result, out, ensure_ascii=False, indent=2)
        out.write("\n")
