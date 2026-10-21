"""Offline regression checks for the project's telephone-sales role boundary."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
ASR = ROOT / "lark-sales-audio-asr-scripted"
ROLES = ROOT / "classify-sales-call-roles"
sys.path[:0] = [str(ASR / "scripts"), str(ROLES / "scripts")]
from build_role_profile import evidence_scores

spec = importlib.util.spec_from_file_location("telesales_pipeline", ASR / "scripts/pipeline.py")
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)


def main():
    assert evidence_scores("喂你好，再见", "telesales") == {"sales": 0, "customer": 0, "bystander": 0}
    for text in ("我是店员，老板不在。", "你们销售没来，你们政策我没听说。", "你们公司你们平台的政策我不知道。"):
        scores = evidence_scores(text, "telesales")
        assert scores["customer"] > scores["sales"] and scores["bystander"] == 0

    def item(speaker, start, text):
        return {"text": text, "start_time": start, "end_time": start + 2000, "additions": {"speaker": speaker}}

    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        source = folder / "result.json"
        source.write_text(json.dumps({"utterances": [
            item("0", 0, "我是爱他美渠道专员，给您回访，您知道咱们门店这个活动吗？"),
            item("1", 2100, "我是店员，老板不在，我负责接电话。"),
            item("1", 4200, "你们销售没来，你们政策我没听说。"),
            item("2", 6500, "我是店长，我们店配额不够。"),
        ]}, ensure_ascii=False))
        mapping = folder / "map.json"
        mapping.write_text(json.dumps({"default": {"0": "销售", "1": "客户", "2": "客户"}, "segments": [], "fallback": "旁人"}, ensure_ascii=False))
        audit = folder / "audit.json"
        subprocess.run([sys.executable, str(ROLES / "scripts/audit_role_map.py"), str(source), "--role-map", str(mapping), "--output", str(audit), "--scenario", "telesales"], check=True, capture_output=True)
        assert json.loads(audit.read_text())["summary"]["conflict_windows"] == 0
        # 错误地把代接店员分为旁人时，应被审计指出。
        wrong = json.loads(mapping.read_text())
        wrong["default"]["1"] = "旁人"
        mapping.write_text(json.dumps(wrong))
        subprocess.run([sys.executable, str(ROLES / "scripts/audit_role_map.py"), str(source), "--role-map", str(mapping), "--output", str(audit), "--scenario", "telesales"], check=True, capture_output=True)
        assert json.loads(audit.read_text())["summary"]["needs_repair"]

        # 模型不可用时不启用强制角色分配，也不生成最终转写。
        with patch.object(pipeline, "llm_call", side_effect=RuntimeError("offline simulated failure")):
            try:
                pipeline.label_roles({"scenario": "telesales"}, folder, source)
            except RuntimeError as exc:
                assert "不按关键词强制分配" in str(exc)
            else:
                raise AssertionError("must stop when role model fails")
        assert not (folder / "labeled-transcript.txt").exists()

    short = "[00:00:00.000–00:00:08.000] 客户：我们店没有配额，需要十听。"
    with patch.object(pipeline, "llm_call", return_value='{"result":"有效","reason":"客户反馈缺配额并提出数量需求"}') as call:
        verdict = pipeline.judge_validity({"scenario": "telesales"}, {"valid_min_seconds": 0}, short, duration_seconds=8)
        assert verdict["value"] == "有效" and call.called
    with patch.object(pipeline, "llm_call", side_effect=RuntimeError("offline simulated failure")):
        try:
            pipeline.judge_validity({"scenario": "telesales"}, {"valid_min_seconds": 0}, short, duration_seconds=8)
        except RuntimeError:
            pass
        else:
            raise AssertionError("model failure must not become business-invalid")
    print("telesales regression: OK (offline; no API calls)")


if __name__ == "__main__":
    main()
