#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""不访问飞书、LAS 或模型的确定性自测。"""

from __future__ import annotations

import importlib.util
import json
import math
import tempfile
import wave
from pathlib import Path

import tencent_flash


PIPELINE = Path(__file__).with_name("pipeline.py")
SPEC = importlib.util.spec_from_file_location("sales_audio_pipeline", PIPELINE)
assert SPEC and SPEC.loader
pipeline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pipeline)


def make_wav(path: Path, seconds: int = 2) -> None:
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\0\0" * 8000 * seconds)


def main() -> int:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        wav = root / "test.wav"
        make_wav(wav)
        assert math.isclose(pipeline.media_duration(wav), 2.0)
        assert pipeline.local_audio_name("新录音.m4a") == "audio.m4a"
        assert pipeline.local_audio_name("标准录音 10.MP3") == "audio.mp3"
        assert pipeline.local_audio_name("没有扩展名") == "audio.bin"
        selected, stats = pipeline.pending_rows(
            [
                {"_record_id": "r1", "audio": [{"file_token": "f1"}], "text": None},
                {"_record_id": "r2", "audio": [{"file_token": "f2"}], "text": None},
                {"_record_id": "r3", "audio": [{"file_token": "f3"}], "text": "已有"},
            ],
            {"attachment_field": "audio", "transcript_field": "text"},
            False,
            {"r2", "r3"},
        )
        assert [row["_record_id"] for row in selected] == ["r2"]
        assert stats["records"] == 2 and stats["already_written"] == 1

        payload = {
            "data": {
                "result": {
                    "text": "老板你好",
                    "utterances": [
                        {
                            "text": "老板你好",
                            "start_time": 0,
                            "end_time": 1000,
                            "additions": {"speaker": "0"},
                        },
                        {
                            "text": "你说",
                            "start_time": 1100,
                            "end_time": 1800,
                            "additions": {"speaker": "1"},
                        },
                    ],
                }
            }
        }
        assert pipeline.collect_speakers(payload) == ["0", "1"]
        inline_payload = {
            "data": {
                "result": {
                    "utterances": [
                        {
                            "text": "[spk2][0.0-0.8]旁边的人[spk1][0.8-1.5]客户回应",
                            "start_time": 0,
                            "end_time": 1500,
                            "additions": {"speaker": "0"},
                        }
                    ]
                }
            }
        }
        assert pipeline.collect_speakers(inline_payload) == ["0", "1", "2"]
        converted = pipeline.validate_normalized_items(
            [
                {"id": "U0001", "text": "你哋系咪做呢个"},
                {"id": "U0002", "text": "[spk2][0.0-0.8]我冇做"},
            ],
            {
                "items": [
                    {"id": "U0001", "text": "你们是不是做这个？"},
                    {"id": "U0002", "text": "[spk2][0.0-0.8]我没做。"},
                ]
            },
        )
        assert converted["U0001"] == "你们是不是做这个？"
        try:
            pipeline.validate_normalized_items(
                [{"id": "U0001", "text": "[spk2][0.0-0.8]我冇做"}],
                {"items": [{"id": "U0001", "text": "我没做。"}]},
            )
        except ValueError:
            pass
        else:
            raise AssertionError("改动 speaker 标签时应拒绝")
        inline_result = root / "inline-result.json"
        inline_result.write_text(json.dumps(inline_payload, ensure_ascii=False), encoding="utf-8")
        inline_profile = root / "inline-profile.md"
        pipeline.subprocess_checked(
            [
                pipeline.sys.executable,
                str(pipeline.ROLE_BUILD),
                str(inline_result),
                "--output", str(inline_profile),
            ],
            "内嵌 speaker 画像自测",
        )
        profile_text = inline_profile.read_text(encoding="utf-8")
        assert "## speaker 1" in profile_text and "## speaker 2" in profile_text
        role_map = pipeline.validate_role_map(
            {"default": {"0": "销售", "1": "客户"}, "segments": [], "fallback": "旁人"},
            ["0", "1"],
        )
        assert role_map["default"]["1"] == "客户"
        try:
            pipeline.validate_role_map(
                {"default": {"0": "旁人"}, "segments": [], "fallback": "旁人"},
                ["0"],
            )
        except ValueError:
            pass
        else:
            raise AssertionError("单 speaker 销售录音不应全部标为旁人")
        fallback_single = pipeline.fallback_role_map(
            ["0"],
            "## speaker 0\n- evidence_scores: sales=3, customer=1, bystander=0",
        )
        assert fallback_single["default"]["0"] == "销售"

        result = root / "result.json"
        result.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        role_file = root / "role-map.json"
        role_file.write_text(json.dumps(role_map, ensure_ascii=False), encoding="utf-8")
        transcript = root / "transcript.txt"
        pipeline.subprocess_checked(
            [
                pipeline.sys.executable,
                str(pipeline.ROLE_APPLY),
                str(result),
                "--role-map", str(role_file),
                "--output", str(transcript),
            ],
            "角色映射自测",
        )
        text = pipeline.validate_transcript(transcript)
        assert "销售：老板你好" in text and "客户：你说" in text

        state_dir = root / "run"
        state = {"records": {"r1": {"status": "prepared"}}}
        pipeline.save_state(state_dir, state)
        assert pipeline.load_json(state_dir / "state.json")["records"]["r1"]["status"] == "prepared"

        assert pipeline.money(1.23456) == "1.2346"
        params = tencent_flash.request_params(
            {
                "engine_type": "16k_zh_en",
                "speaker_diarization": 1,
                "hotwords": ["小安素", "全安素"],
                "hotword_weight": 10,
            },
            "test-secret-id",
            "mp3",
            1234567890,
        )
        query = tencent_flash.encoded_query(params)
        canonical = tencent_flash.canonical_query(params)
        assert query.startswith("convert_num_mode=1&engine_type=16k_zh_en")
        assert "hotword_list=" in query and "%E5%B0%8F%E5%AE%89%E7%B4%A0%7C10" in query
        assert "hotword_list=小安素|10,全安素|10" in canonical
        assert tencent_flash.signature("123", "a=1&b=2", "key") == "Rh4Fc6Tv3/j3G607q2jQSnIJAcM="
        normalized_tencent = tencent_flash.normalize_response({
            "code": 0,
            "request_id": "request-1",
            "audio_duration": 1800,
            "flash_result": [{
                "channel_id": 0,
                "text": "老板你好。你说。",
                "sentence_list": [
                    {
                        "text": "老板你好。",
                        "start_time": 0,
                        "end_time": 900,
                        "speaker_id": 0,
                    },
                    {
                        "text": "你说。",
                        "start_time": 1000,
                        "end_time": 1800,
                        "speaker_id": 1,
                    },
                ],
            }],
        })
        assert pipeline.collect_speakers(normalized_tencent) == ["0", "1"]
        assert pipeline.deep_find(normalized_tencent, "request_id") == "request-1"
        try:
            pipeline.validate_role_map(
                {"default": {"0": "销售", "1": "销售"}},
                ["0", "1"],
            )
        except ValueError:
            pass
        else:
            raise AssertionError("缺少客户角色时应拒绝")

        for invalid_map in (
            {
                "default": {"0": "销售", "1": "客户"},
                "segments": [{"start_ms": 1000, "end_ms": 2000, "map": {"9": "销售"}}],
            },
            {
                "default": {"0": "销售", "1": "客户"},
                "segments": [
                    {"start_ms": 1000, "end_ms": 3000, "map": {"1": "销售"}},
                    {"start_ms": 2000, "end_ms": 4000, "map": {"1": "客户"}},
                ],
            },
        ):
            try:
                pipeline.validate_role_map(invalid_map, ["0", "1"])
            except ValueError:
                pass
            else:
                raise AssertionError("未知 speaker 或重叠 segment 应拒绝")

        drift_payload = {
            "data": {
                "result": {
                    "utterances": [
                        {
                            "text": "我们平台的合作方案，我给你介绍下单和后台返利。",
                            "start_time": 0,
                            "end_time": 3000,
                            "additions": {"speaker": "0"},
                        },
                        {
                            "text": "我们店有人问我，我不敢压货，利润怎么来？",
                            "start_time": 5000,
                            "end_time": 8000,
                            "additions": {"speaker": "1"},
                        },
                        {
                            "text": "我给你看平台下单，你的客户扫码，后台返利。",
                            "start_time": 615000,
                            "end_time": 618000,
                            "additions": {"speaker": "1"},
                        },
                        {
                            "text": "我店里的顾客有人问我，我不敢卖。",
                            "start_time": 620000,
                            "end_time": 623000,
                            "additions": {"speaker": "2"},
                        },
                    ]
                }
            }
        }
        wrong = {
            "default": {"0": "销售", "1": "客户", "2": "客户"},
            "segments": [],
            "fallback": "旁人",
        }
        repaired = {
            "default": {"0": "销售", "1": "客户", "2": "客户"},
            "segments": [{
                "start_ms": 600000,
                "end_ms": None,
                "map": {"1": "销售"},
            }],
            "fallback": "旁人",
        }
        drift_result = root / "drift-result.json"
        drift_record = root / "drift-record"
        drift_result.write_text(json.dumps(drift_payload, ensure_ascii=False), encoding="utf-8")
        calls = []
        original_llm_call = pipeline.llm_call

        def fake_llm_call(*_args, **_kwargs):
            calls.append(1)
            return json.dumps(wrong if len(calls) == 1 else repaired, ensure_ascii=False)

        pipeline.llm_call = fake_llm_call
        try:
            labeled, source, audit = pipeline.label_roles({}, drift_record, drift_result)
        finally:
            pipeline.llm_call = original_llm_call
        assert len(calls) == 2
        assert source == "llm+audit_repair_1"
        assert audit["conflict_windows"] == 0
        assert "客户：我们店有人问我" in labeled
        assert "销售：我给你看平台下单" in labeled

        relabel_dir = root / "relabel-run"
        relabel_record = relabel_dir / "records" / "r1"
        relabel_record.mkdir(parents=True)
        (relabel_record / "result.json").write_text(
            json.dumps(drift_payload, ensure_ascii=False),
            encoding="utf-8",
        )
        pipeline.save_state(relabel_dir, {
            "profile": {},
            "asr_language": "zh-CN",
            "output_language": "source",
            "records": {"r1": {"record_id": "r1", "status": "written"}},
        })
        calls.clear()
        pipeline.llm_call = fake_llm_call
        try:
            preview = pipeline.relabel_run(
                {}, relabel_dir, workers=1, dry_run=True,
                overwrite=False, record_ids={"r1"},
            )
        finally:
            pipeline.llm_call = original_llm_call
        assert preview["relabel_preview"]["r1"]["status"] == "role_labeled"
        preview_dir = Path(preview["relabel_preview"]["r1"]["transcript_path"]).parent
        assert "role-preview" in str(preview_dir)
        assert (preview_dir / "role-audit.json").exists()

    # ---- 多附件合并 ----
    assert pipeline.renumber_speaker(2, "1") == "201"
    assert pipeline.renumber_speaker(1, "abc") == "1_abc"
    item = {"additions": {"speaker": "1"}, "start_ms": 500, "end_ms": 2000}
    pipeline.shift_time(item, 30000)
    assert item["start_ms"] == 30500 and item["end_ms"] == 32000
    pipeline.set_speaker(item, "101")
    assert item["additions"]["speaker"] == "101"
    assert pipeline.time_of({"start_time": 1000, "start_ms": 999}, True) == 1000
    assert pipeline.time_of({"end_ms": 42}, False) == 42

    seg1 = root / "result-1.json"
    seg2 = root / "result-2.json"
    pipeline.atomic_json(seg1, {
        "data": {"result": {"text": "你好", "utterances": [
            {"additions": {"speaker": "1"}, "start_ms": 0, "end_ms": 1000, "text": "你好"},
            {"additions": {"speaker": "2"}, "start_ms": 1000, "end_ms": 3000, "text": "您好"},
        ]}},
    })
    pipeline.atomic_json(seg2, {
        "data": {"result": {"text": "再见", "utterances": [
            {"additions": {"speaker": "1"}, "start_ms": 0, "end_ms": 2000, "text": "再见"},
        ]}},
    })
    merged_path = pipeline.merge_result_files(root, [seg1, seg2])
    merged_items = pipeline.utterances_from(pipeline.load_json(merged_path))
    assert [str(item["additions"]["speaker"]) for item in merged_items] == ["101", "102", "201"]
    assert [item["start_ms"] for item in merged_items] == [0, 1000, 3000]
    assert [item["end_ms"] for item in merged_items] == [1000, 3000, 5000]
    assert pipeline.result_container(pipeline.load_json(merged_path))["text"] == "你好您好再见"

    # ---- 是否有效对话自动初判（两层：硬门禁 + 模型内容判断）----
    # 硬门禁：时长不足 60 秒直接无效（不调模型）
    short = (
        "[00:00:00.000–00:00:00.500] 客户：嗯\n"
        "[00:00:00.500–00:00:01.000] 销售：好\n"
    )
    verdict = pipeline.judge_validity({}, {}, short)
    assert verdict == {"value": "录音过短", "reason": "录音时长 1 秒，不足 60 秒", "source": "rule"}, verdict
    # 59 秒（含）以下「录音过短」，60 秒整放行到模型层
    under = "[00:00:00.000–00:00:59.000] 客户：嗯\n"
    assert pipeline.judge_validity({}, {}, under, duration_seconds=59.0)["value"] == "录音过短"
    exactly = "[00:00:00.000–00:01:00.000] 客户：嗯\n"
    # 60 秒整：硬门禁放行（不再由 rule 判），进入模型层（cfg 缺模型配置时是 model_error）
    assert pipeline.judge_validity({}, {}, exactly, duration_seconds=60.0)["source"] != "rule"
    # 时长不足但显式传入更长 duration 时，规则层放行，转写为空则规则判「内容无效」
    assert pipeline.judge_validity({}, {}, "", duration_seconds=200.0)["source"] == "rule"
    # 转写无法解析任何行 -> 规则判「内容无效」
    assert pipeline.judge_validity({}, {}, "没有时间戳的乱七八糟内容", duration_seconds=300.0)["value"] == "内容无效"
    # transcript_duration 兜底：无时间戳/空文本返回 None
    assert pipeline.transcript_duration("") is None
    assert pipeline.transcript_duration(
        "[00:01:00.000–00:02:00.000] 销售：好\n[00:02:00.000–00:04:00.000] 客户：好\n"
    ) == 240.0

    print("self_test: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
