#!/usr/bin/env python3
"""Deterministic tests for temporal speaker-role drift handling."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from apply_role_map import role_at


ROOT = Path(__file__).resolve().parent


def utterance(speaker: str, start: int, text: str) -> dict:
    return {
        "text": text,
        "start_time": start,
        "end_time": start + 3000,
        "additions": {"speaker": speaker},
    }


def main() -> None:
    payload = {
        "data": {
            "result": {
                "utterances": [
                    utterance("0", 0, "我们平台的合作方案，我给你介绍下单和后台返利。"),
                    utterance("1", 5000, "我们店有人问我，我不敢压货，利润怎么来？"),
                    utterance("0", 610000, "妈妈，把鞋子脱了去玩吧。"),
                    utterance("1", 615000, "我给你看平台下单，你的客户扫码，后台返利。"),
                    utterance("2", 620000, "我店里的顾客有人问我，我不敢卖。"),
                ]
            }
        }
    }
    initial = {
        "default": {"0": "销售", "1": "客户", "2": "客户"},
        "segments": [],
        "fallback": "旁人",
    }
    repaired = {
        "default": {"0": "销售", "1": "客户", "2": "客户"},
        "segments": [{
            "start_ms": 600000,
            "end_ms": None,
            "map": {"0": "旁人", "1": "销售"},
        }],
        "fallback": "旁人",
    }

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        source = tmp_path / "source.json"
        initial_map = tmp_path / "initial.json"
        repaired_map = tmp_path / "repaired.json"
        profile = tmp_path / "profile.md"
        initial_audit = tmp_path / "initial-audit.json"
        repaired_audit = tmp_path / "repaired-audit.json"
        source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        initial_map.write_text(json.dumps(initial, ensure_ascii=False), encoding="utf-8")
        repaired_map.write_text(json.dumps(repaired, ensure_ascii=False), encoding="utf-8")

        subprocess.run(
            [sys.executable, str(ROOT / "build_role_profile.py"), str(source), "--output", str(profile)],
            check=True,
            capture_output=True,
            text=True,
        )
        profile_text = profile.read_text(encoding="utf-8")
        assert "Temporal role drift candidates" in profile_text
        assert "speaker 1" in profile_text
        assert "conflicting_local_roles: 客户, 销售" in profile_text

        for role_map, output in (
            (initial_map, initial_audit),
            (repaired_map, repaired_audit),
        ):
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "audit_role_map.py"),
                    str(source),
                    "--role-map",
                    str(role_map),
                    "--output",
                    str(output),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

        before = json.loads(initial_audit.read_text(encoding="utf-8"))
        after = json.loads(repaired_audit.read_text(encoding="utf-8"))
        assert before["summary"]["needs_repair"]
        assert any(
            item["speaker"] == "1"
            and item["assigned_role"] == "客户"
            and item["proposed_role"] == "销售"
            for item in before["conflicts"]
        )
        assert after["summary"]["conflict_windows"] == 0

    default = repaired["default"]
    segments = repaired["segments"]
    assert role_at("1", 5000, default, segments, "旁人") == "客户"
    assert role_at("1", 615000, default, segments, "旁人") == "销售"
    assert role_at("0", 615000, default, segments, "旁人") == "旁人"
    print("self_test: OK")


if __name__ == "__main__":
    main()
