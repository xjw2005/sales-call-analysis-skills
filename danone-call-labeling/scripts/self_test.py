"""Offline regressions: synthetic calls only, no network or customer data."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pipeline as p


def record(project="p3m", text="我不能决定采购，请直接联系公司的采购负责人。"):
    return p.normalize({"id": "synthetic-1", "project": project, "transcript_complete": True,
                        "utterances": [{"id": "U1", "role": "坐席", "text": "谁负责采购决策？"},
                                       {"id": "U2", "role": "客户", "text": text}]})


def result(field="A2 是否本人决策", option="拿不了主", project="p3m", quote=None):
    return {"project": project, "labels": {field: [option]}, "review_items": [],
            "evidence": [{"field": field, "option": option, "utterance_id": "U2",
                          "quote": quote or "我不能决定采购，请直接联系公司的采购负责人。",
                          "product": "不适用", "time_scope": "本次通话", "context_ids": ["U1"]}]}


class Tests(unittest.TestCase):
    def rejects(self, rec, response):
        with self.assertRaises(ValueError):
            p.validate_result(rec, response, list(response["labels"]))

    def test_schema_counts_and_a6_collision(self):
        expected = {"p3m": (12, 46), "lyqy": (19, 110), "crown": (22, 94)}
        for project, counts in expected.items():
            fields = p.fields_for(project)
            self.assertEqual((len(fields), sum(len(f["options"]) for f in fields.values())), counts)
        self.assertIn("A6 动销状态", p.fields_for("p3m"))
        self.assertNotIn("A6 动销状态", p.fields_for("crown"))
        self.assertNotIn("C6 亲熠-卡点", p.fields_for("crown"))
        self.assertTrue(all("boundary" in f and all(o["rule_tier"] in "ABCD" and o["rule"] for o in f["options"])
                            for fields in p.CATALOG["projects"].values() for f in fields.values()))

    def test_legal_label_and_business_export(self):
        response = result()
        self.assertIs(p.validate_result(record(), response, list(response["labels"])), response)
        self.assertEqual(p.business_fields(response), {"A2 是否本人决策": ["拿不了主"]})

    def test_project_and_field_leakage(self):
        self.rejects(record("crown"), result(project="crown"))
        self.rejects(record(), result(project="lyqy"))
        self.rejects(record(), result(field="A6 皇冠店政策-会员日优惠券", option="清楚细节"))

    def test_fabricated_quote_and_sales_quote(self):
        self.rejects(record(), result(quote="我是老板，我负责采购。"))
        response = result(quote="谁负责采购决策？")
        response["evidence"][0]["utterance_id"] = "U1"
        self.rejects(record(), response)

    def test_noncontiguous_quote(self):
        self.rejects(record(text="我不能决定采购。稍后您联系采购。"), result(quote="我不能决定采购，您联系采购。"))

    def test_missing_and_orphan_evidence(self):
        response = result()
        response["evidence"] = []
        self.rejects(record(), response)
        response = result()
        response["labels"]["A2 是否本人决策"] = []
        self.rejects(record(), response)

    def test_scope_and_context_required(self):
        response = result()
        response["evidence"][0]["product"] = ""
        self.rejects(record(), response)
        response = result(quote="对")
        response["evidence"][0]["context_ids"] = []
        self.rejects(record(text="对"), response)

    def test_c_and_d_rules_fail_closed(self):
        for project, fields in p.CATALOG["projects"].items():
            for field, spec in fields.items():
                for option in spec["options"]:
                    if option["rule_tier"] in {"C", "D"} and option["value"] not in p.defaults(project, field):
                        self.rejects(record(project), result(field, option["value"], project))

    def test_defaults_and_lyqy_unmentioned_remain_distinct(self):
        for project, field, value in [("p3m", "A1 联系人身份", "身份不明"),
                                      ("lyqy", "A2 是否本人决策", "无法判断"),
                                      ("crown", "A6 皇冠店政策-会员日优惠券", "未反馈")]:
            response = {"project": project, "labels": {field: [value]}, "evidence": [], "review_items": []}
            p.validate_result(record(project), response, [field])
        field = "A10 业务服务意向"
        response = {"project": "lyqy", "labels": {field: ["未反馈"]}, "evidence": [], "review_items": []}
        self.rejects(record("lyqy"), response)

    def test_empty_p3m_authority_requires_review(self):
        response = {"project": "p3m", "labels": {"A2 是否本人决策": []}, "evidence": [], "review_items": []}
        self.rejects(record(), response)
        response["review_items"] = [{"label": "A2 是否本人决策", "reason": "权限不明"}]
        p.validate_result(record(), response, ["A2 是否本人决策"])
        self.assertEqual(p.business_fields(response), {})

    def test_reviewed_field_not_exported_and_no_clearing(self):
        response = result()
        response["review_items"] = [{"label": "A2 是否本人决策", "reason": "上下文有冲突"}]
        self.assertEqual(p.business_fields(response), {})

    def test_incomplete_and_missing_customer_skip_model(self):
        incomplete = record()
        incomplete["transcript_complete"] = False
        no_customer = record()
        no_customer["utterances"] = no_customer["utterances"][:1]
        for rec in (incomplete, no_customer):
            def forbidden(_):
                self.fail("不完整通话不能调用模型")
            output = p.analyze(rec, list(p.fields_for(rec["project"])), forbidden)
            self.assertEqual(output["status"], "needs_review")
            self.assertFalse(output["business_fields"])
            self.assertEqual(len(output["result"]["review_items"]), 12)
            self.rejects(rec, result())

    def test_role_parser_rejects_unparsed_lines_and_duplicates(self):
        rec = {"id": "synthetic", "project": "p3m", "transcript_complete": True,
               "transcript": "[00:00.000–00:01.000] 客户：我是店长。"}
        self.assertEqual(p.normalize(rec)["utterances"][0]["role"], "客户")
        rec["transcript"] += "\n[说话人1] 未确认角色文本"
        with self.assertRaises(ValueError):
            p.normalize(rec)
        rec = record()
        rec["utterances"][1]["id"] = "U1"
        with self.assertRaises(ValueError):
            p.normalize(rec)

    def test_isolated_field_requests_and_malformed_response(self):
        rec = record()
        calls = []
        def model(messages):
            calls.append(messages)
            return result()
        output = p.analyze(rec, ["A2 是否本人决策"], model)
        self.assertEqual(output["status"], "completed")
        self.assertEqual(len(calls), 1)
        self.assertNotIn('"A6 动销状态":', calls[0][0]["content"])
        with self.assertRaises(ValueError):
            p.analyze(rec, ["A2 是否本人决策"], lambda _: {"project": "p3m"})

    def test_cli_prepare_and_analyze_mocked_transport(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "in.ndjson"
            source.write_text(json.dumps(record(), ensure_ascii=False) + "\n", encoding="utf-8")
            prepared = Path(temp) / "prepared.ndjson"
            p.main(["prepare", "--input", str(source), "--output", str(prepared), "--field", "A2 是否本人决策"])
            self.assertEqual(len(json.loads(prepared.read_text(encoding="utf-8"))["requests"]), 1)
            with patch.dict(p.os.environ, {"LLM_API_URL": "https://example.invalid/v1", "LLM_MODEL": "mock", "LLM_API_KEY": "synthetic"}), patch.object(p, "request_model", return_value=result()) as request:
                target = Path(temp) / "out.ndjson"
                p.main(["analyze", "--input", str(source), "--output", str(target), "--field", "A2 是否本人决策"])
                self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["status"], "completed")
                self.assertEqual(request.call_count, 1)
                with self.assertRaises(SystemExit):
                    p.main(["analyze", "--input", str(source), "--output", str(target)])
                request.assert_called_once()

    def test_source_import_is_complete_and_contains_no_record_ids(self):
        spec = importlib.util.spec_from_file_location("importer", p.ROOT / "tools/import_standard.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertNotIn("record_id=", json.dumps(p.CATALOG, ensure_ascii=False))
        # Minimal synthetic source verifies the importer doesn't export quoted transcripts.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fields = {"data": {"fields": [{"name": "A1 联系人身份", "multiple": True,
                                             "options": [{"name": "店长"}]}]}}
            for project in ("p3m", "lyqy", "crown"):
                (root / f"f_{project}.json").write_text(json.dumps(fields), encoding="utf-8")
            standard = root / "standard.md"
            standard.write_text("### A1\n适用字段：P3M `A1 联系人身份`；lyqy `A1 联系人身份`；皇冠 `A1 联系人身份`。\n分界与触发：明确职务。\n#### 店长\n操作定义：明确是店长。\n项目证据状态：P3M A：支持；lyqy B：支持；皇冠 D：无依据。\n```text\nPRIVATE_CUSTOMER_QUOTE\n```", encoding="utf-8")
            exported = module.build(standard, root)
            self.assertNotIn("PRIVATE_CUSTOMER_QUOTE", json.dumps(exported))
            self.assertEqual(exported["projects"]["crown"]["A1 联系人身份"]["options"][0]["rule_tier"], "D")

    def test_channel_specialist_role_is_accepted_but_not_customer_evidence(self):
        record = p.normalize({"id": "synthetic-channel", "project": "p3m", "transcript_complete": True,
            "transcript": "[00:00.000–00:02.000] 渠道专员：您是店员吗？\n[00:02.000–00:03.000] 客户：我是店员。"})
        self.assertEqual(record["utterances"][0]["role"], "渠道专员")
        result = {"project": "p3m", "labels": {"A1 联系人身份": ["店员"]},
            "evidence": [{"field": "A1 联系人身份", "option": "店员", "utterance_id": "U0001",
                          "quote": "店员", "product": "不适用", "time_scope": "本次通话", "context_ids": []}],
            "review_items": []}
        with self.assertRaises(ValueError):
            p.validate_result(record, result, ["A1 联系人身份"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
