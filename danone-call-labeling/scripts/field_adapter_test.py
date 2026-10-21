"""Field-only regression tests with synthetic schemas/results; no network."""
import copy
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import field_adapter as a


def schema_for(table):
    binding = a.binding_for(table)
    return [{"id": field_id, "name": name, "type": "select", "multiple": True,
             "options": [{"name": o["value"]} for o in a.pipeline.fields_for(binding["project"])[name]["options"]]}
            for name, field_id in binding["fields"].items()]


def row(project="p3m"):
    return {"id": "recSynthetic1", "standard_sha256": a.pipeline.CATALOG["standard_sha256"],
            "result": {"project": project, "labels": {"A2 是否本人决策": ["拿不了主" if project == "p3m" else "否"]},
                       "review_items": []}}


class Tests(unittest.TestCase):
    def setUp(self):
        self.tables = {b["project"]: t for t, b in a.BINDINGS["tables"].items()}
        self.table = self.tables["p3m"]

    def test_all_bindings_cover_53_fields(self):
        counts = []
        for table in self.tables.values():
            report = a.check_schema(table, schema_for(table))
            self.assertTrue(report["compatible"])
            counts.append(report["field_count"])
        self.assertEqual(sum(counts), 53)
        self.assertNotEqual(a.binding_for(self.tables["crown"])["fields"].get("A6 动销状态"),
                            a.binding_for(self.table)["fields"]["A6 动销状态"])

    def test_schema_drift_blocks_mapping(self):
        original = schema_for(self.table)
        for change in ("name", "type", "multiple", "options", "missing", "id"):
            fields = copy.deepcopy(original)
            if change == "missing":
                fields.pop()
            elif change == "options":
                fields[0]["options"].append({"name": "未知选项"})
            else:
                fields[0][change] = False if change == "multiple" else "changed"
            self.assertFalse(a.check_schema(self.table, fields)["compatible"])
            with self.assertRaises(ValueError):
                a.update_draft(self.table, [row()], fields)

    def test_map_all_projects_to_ids_without_touching_input(self):
        for project, table in self.tables.items():
            source = row(project)
            original = copy.deepcopy(source)
            draft = a.update_draft(table, [source], schema_for(table))
            field_id = a.binding_for(table)["fields"]["A2 是否本人决策"]
            self.assertEqual(draft["update_records"], {"recSynthetic1": {field_id: source["result"]["labels"]["A2 是否本人决策"]}})
            self.assertEqual(source, original)

    def test_no_clearing_or_review_metadata(self):
        source = row()
        source["result"]["labels"]["B1 价格与利润"] = []
        source["result"]["review_items"] = [{"label": "A2 是否本人决策", "reason": "权限待确认"}]
        source["business_fields"] = {"录音转写": "不能导出", "其他备注": "不能导出"}
        self.assertEqual(a.update_draft(self.table, [source], schema_for(self.table))["update_records"], {})

    def test_cross_project_invalid_values_and_identity_blocked(self):
        sources = [row("crown"), row(), row(), row(), row()]
        sources[1]["result"]["labels"]["A2 是否本人决策"] = ["待确认"]
        sources[2]["result"]["labels"]["录音转写"] = ["不可修改"]
        sources[3]["id"] = "NO.001"
        sources[4]["standard_sha256"] = "old"
        for source in sources:
            with self.assertRaises(ValueError):
                a.update_draft(self.table, [source], schema_for(self.table))
        with self.assertRaises(ValueError):
            a.update_draft(self.table, [row(), row()], schema_for(self.table))

    def test_incomplete_schema_envelope(self):
        for envelope in [{"ok": False}, {"ok": True, "data": {"fields": [], "total": 1}}]:
            with self.assertRaises(ValueError):
                a.parse_schema(json.dumps(envelope))

    def test_cli_check_and_map_are_local_only(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(temp)
            fields = schema_for(self.table)
            snapshot = root / "schema.json"
            snapshot.write_text(json.dumps({"ok": True, "data": {"fields": fields, "total": len(fields)}}), encoding="utf-8")
            source = root / "analysis.ndjson"
            source.write_text(json.dumps(row()) + "\n", encoding="utf-8")
            target = root / "draft.json"
            args = ["--table-id", self.table, "--schema", str(snapshot)]
            self.assertEqual(a.main(["check", *args]), 0)
            self.assertEqual(a.main(["map", *args, "--input", str(source), "--output", str(target)]), 0)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["table_id"], self.table)


if __name__ == "__main__":
    unittest.main(verbosity=2)
