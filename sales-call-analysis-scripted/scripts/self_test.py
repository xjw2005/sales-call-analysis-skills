#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""不调用模型和飞书的确定性自测。"""

import copy
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline as p
import knowledge as k


TEXT = """[00:00.000–00:02.000] 销售：老板您好，我想先了解门店情况，再讨论是否适合做一个小测试。
[00:02.100–00:04.500] 销售：您现在经营上最头疼的是什么？
[00:04.600–00:08.000] 客户：现在价格太透明，旁边大店卖得比我的进货价高不了几块钱。
[00:08.100–00:11.000] 销售：也就是说，您担心的不只是进价，而是最后没有真实利润。
[00:11.100–00:15.000] 客户：对，而且小众产品断货以后，顾客就去别家了。
[00:15.100–00:18.000] 销售：如果先用不压货的方式试两周，您觉得能不能接受？
[00:18.100–00:20.000] 客户：可以先看看，但先不要让我压货。
[00:20.100–00:23.000] 销售：那我明天下午把产品清单发您，周五再确认选哪个。
[00:23.100–00:25.000] 客户：行，周五你再联系我。
[00:25.100–00:27.000] 旁人：老板，有顾客找你。
"""


KB_FILE_TEXT = """---
type: knowledge
domain: industry
id: ind-model
title: 母婴门店经营模式分类
version: 1
status: active
updated: 2026-08-03
tags: [经营模式]
---

## 线下现货模式（ind-model-001）

> [!kb] 条目元数据
> tags: 经营模式, 线下
> version: 1
> updated: 2026-08-03
> status: active

门店直接拿现货、当场陈列销售的模式。识别线索：客户说"现货""直接拿货""现款现结"。
只用于帮助门店档案归类，不据此补写录音未提及的事实，这一段正文已经足够长了。

## 跨境代发模式（ind-model-002）

> [!kb] 条目元数据
> tags: 经营模式, 跨境
> version: 1
> updated: 2026-08-03
> status: active

门店不囤货、客户下单后由品牌方代发。识别线索：客户说"一件代发""不压货"。
只用于帮助门店档案归类，不据此补写录音未提及的事实，这一段正文已经足够长了。
"""


BAD_KB_TEXT = """---
type: knowledge
domain: industry
id: ind-bad
title: 坏条目
version: 1
status: active
updated: 2026-08-03
---

## 重复条目（ind-model-001）

> [!kb] 条目元数据
> version: 1
> status: active
> updated: 2026-08-03

这条与好文件里的条目 id 重复，并且正文里引用了一个不存在的知识#ind-model-999，
两处都应被一致性自检抓出来，这一段正文已经足够长了。
"""


def candidate():
    categories = []
    for name in p.CONCERN_NAMES:
        if name == "价格敏感":
            categories.append({
                "name": name, "status": "是", "subtype": "", "evidence_ids": ["U0003"],
                "scene": "讨论大型门店低价竞争", "object": "终端成交价和进销差价",
                "reason": "客户直接说明售价与进货价差距很小。",
            })
        else:
            categories.append({
                "name": name, "status": "否", "subtype": "", "evidence_ids": [],
                "scene": "", "object": "", "reason": "",
            })
    scores = []
    values = [8, 15, 10, 12, 8, 7, 4, 8]
    for (name, _), score in zip(p.DIMENSIONS, values):
        scores.append({
            "name": name, "score": score, "judgment": f"{name}有明确动作，但仍有改进空间。",
            "evidence_ids": ["U0001"],
        })
    sections = {}
    for key in p.PROFILE_SECTIONS:
        sections[key] = {"content": "未确认", "state_type": "未确认", "evidence_ids": []}
    sections["price_profit"] = {
        "content": "当前对价格透明和真实利润敏感。",
        "state_type": "当前状态", "evidence_ids": ["U0003"],
    }
    sections["cooperation_preferences"] = {
        "content": "当前只接受不压货的小规模测试。",
        "state_type": "当前状态", "evidence_ids": ["U0007"],
    }
    return {
        "_scope": {
            "input_quality": "可用", "quality_reason": "",
            "role_corrections": [], "exclusions": [],
        },
        "explicit-needs": {
            "items": [{
                "need": "需要扣除价格竞争影响后仍有真实利润的产品。",
                "evidence_ids": ["U0003"], "scene": "讨论门店经营难点",
                "explanation": "客户直接描述售价与进货价差距很小。",
            }],
        },
        "implicit-needs": {
            "items": [{
                "hypothesis": "客户可能需要可退出、无需压货的小规模试点。",
                "confidence": "高",
                "evidence_ids": ["U0005", "U0007"],
                "logic": "断货造成客户流失，同时客户明确拒绝压货。",
            }],
        },
        "concerns": {"categories": categories},
        "effectiveness": {
            "scores": scores,
            "conclusion": (
                "本次对话明确了价格透明、断货流失和不愿压货等核心问题，销售能够复述客户顾虑，"
                "并提出两周小测试。价值量化仍不充分，但最终约定了资料发送时间、复联时间和下一步选择，形成了可执行推进。"
            ),
            "sales_result": "条件性推进；销售明天下午发送清单，双方约定周五复联。",
        },
        "quotes": {
            "items": [{
                "evidence_ids": ["U0004"], "speaker_name": "录音未提供姓名",
                "polished_quote": "您担心的不只是进价，而是最终没有真实利润。",
                "scene": "客户说明低价竞争", "problem": "价格透明导致利润不足",
                "method": "复述并区分进价与真实利润", "value": "客户的核心经营顾虑",
                "reaction_ids": ["U0005"],
            }]
        },
        "store-profile": {
            "one_line": "当前对价格透明、断货和压货风险敏感，适合低库存小规模测试。",
            "one_line_evidence_ids": ["U0003", "U0005", "U0007"],
            "sections": sections,
        },
        "next-action": {
            "cooperation_status": "未合作",
            "status_evidence_ids": [],
            "action_judgment": "触发",
            "judgment_reason": "客户同意继续查看方案，且双方已经约定资料发送与复联时间。",
            "confirmed_actions": [{
                "owner": "销售", "timeframe": "明天下午至周五",
                "action": "销售发送产品清单，并在周五联系客户确认选择。",
                "evidence_ids": ["U0008", "U0009"],
            }],
            "recommended_actions": [{
                "topic": "低风险试点",
                "owner": "销售",
                "timeframe": "24小时内",
                "action": "用不压货的小规模测试回应客户风险，再发送对应产品清单和待确认条件。",
                "reason": "此前沟通未把测试方案与客户只接受不压货的顾虑完整对应。",
                "acceptance": "客户收到产品清单，并确认是否接受不压货的小规模测试条件。",
                "evidence_ids": ["U0007", "U0008"],
            }],
            "second_visit": {
                "value": "值得",
                "reason": "客户接受继续查看方案并明确同意周五复联。",
                "evidence_ids": ["U0007", "U0009"],
            },
        },
    }


def main():
    assert p.field_values_equal("关心类目打标", None, [])
    assert p.field_values_equal(
        "关心类目打标",
        ["利润空间", "价格敏感"],
        ["价格敏感", "利润空间"],
    )
    assert not p.field_values_equal("关心类目打标", ["价格敏感"], [])

    tr = p.parse_transcript(TEXT)
    value = candidate()
    errors, _ = p.validate_candidate(value, tr, p.ALL_MODULES)
    assert not errors, errors

    cooperating = copy.deepcopy(value)
    cooperating["next-action"]["cooperation_status"] = "已合作"
    cooperating["next-action"]["recommended_actions"] = [{
        "topic": "动销与补货",
        "owner": "销售",
        "timeframe": "3天内",
        "action": "回访门店并确认低库存测试后的动销与补货需求。",
        "reason": "客户关注压货风险，需要持续提供低库存服务支持。",
        "acceptance": "门店反馈当前动销情况，并明确下一次补货安排。",
        "evidence_ids": ["U0007"],
    }]
    cooperating["next-action"]["second_visit"] = {
        "value": "不适用", "reason": "已转入合作后服务分支。", "evidence_ids": [],
    }
    errors, _ = p.validate_candidate(cooperating, tr, p.ALL_MODULES)
    assert not errors, errors
    cooperating_rendered = p.render_module(
        "next-action", p.split_candidate(cooperating, "next-action"), tr,
        {"门店编号": "TEST-001", "门店名称": "测试门店"}, "rec_test",
    )
    assert cooperating_rendered["是否达成合作"] == "是"
    cooperating_text = cooperating_rendered["下一步行动策略"]
    assert "合作状态判定：已合作" in cooperating_text
    assert "是否达成合作" not in cooperating_text
    assert "行动判断：触发" in cooperating_text
    assert "下一步行动策略" in cooperating_text
    assert "【动销与补货｜销售｜3天内】" in cooperating_text
    assert "验收：门店反馈当前动销情况" in cooperating_text

    flexible = copy.deepcopy(value)
    flexible["next-action"]["recommended_actions"][0]["timeframe"] = "客户完成装修后"
    errors, _ = p.validate_candidate(
        p.split_candidate(flexible, "next-action"), tr, ["next-action"],
    )
    assert not errors, errors
    flexible["next-action"]["recommended_actions"] = []
    errors, _ = p.validate_candidate(
        p.split_candidate(flexible, "next-action"), tr, ["next-action"],
    )
    assert not errors, errors

    # 合作状态改为模型判定：status_evidence_ids 允许非空（引用转写证据）
    evidenced = copy.deepcopy(value)
    evidenced["next-action"]["status_evidence_ids"] = ["U0007"]
    errors, _ = p.validate_candidate(
        p.split_candidate(evidenced, "next-action"), tr, ["next-action"],
    )
    assert not errors, errors

    rendered = {}
    for module in p.ALL_MODULES:
        rendered.update(p.render_module(
            module, p.split_candidate(value, module), tr,
            {
                "门店编号": "TEST-001", "门店名称": "测试门店",
                "是否达成合作": "否",
            }, "rec_test",
        ))
    assert set(rendered) == {field for fields in p.MODULE_FIELDS.values() for field in fields}
    assert "建议验证问题" not in rendered["隐性需求(仅供参考)"]
    assert "待验证点" not in rendered["隐性需求_原句参考"]
    assert "推断逻辑" in rendered["隐性需求_原句参考"]
    assert "限制说明" not in rendered["隐性需求_原句参考"]
    assert "规则版本" not in rendered["合作进展打分评估"]
    assert "等级阈值" not in rendered["合作进展打分评估"]
    assert "确定性合计" in rendered["合作进展打分评估"]
    assert "硬性基本功结构化打标" not in rendered
    assert "原句参考_硬性基本功结构化打标" not in rendered
    assert rendered["关心类目打标"] == ["价格敏感"]
    assert "「您担心的不只是进价，而是最终没有真实利润。」" in rendered["核心金句"]
    assert "「也就是说，您担心的不只是进价，而是最后没有真实利润。」" not in rendered["核心金句"]
    assert "单次录音快照" in rendered["门店档案"]
    assert "选品偏好" in rendered["门店档案"]
    assert "利润偏好" in rendered["门店档案"]
    for removed in ("老板/关键联系人", "运营与工具能力", "合规与经营风险", "销售接手建议", "证据边界"):
        assert removed not in rendered["门店档案"]
    assert rendered["是否达成合作"] == "否"
    assert "合作状态判定：未合作" in rendered["下一步行动策略"]
    assert "是否达成合作" not in rendered["下一步行动策略"]
    assert "行动判断：触发" in rendered["下一步行动策略"]
    assert "已确认的后续事项" in rendered["下一步行动策略"]
    assert "【低风险试点｜销售｜24小时内】" in rendered["下一步行动策略"]
    assert "验收：客户收到产品清单" in rendered["下一步行动策略"]
    assert "明天下午至周五" in rendered["下一步行动策略"]
    assert "二次拜访价值：值得" in rendered["下一步行动策略"]
    assert "对话依据" not in rendered["下一步行动策略"]
    assert "[00:18.100–00:20.000]" not in rendered["下一步行动策略"]
    model_message = p.build_user_message(
        tr, "store-profile",
        {"门店编号": "TEST-001", "门店名称": "测试门店", "门店联系人": "陈璇飞"},
    )
    assert "TEST-001" not in model_message
    assert "测试门店" not in model_message
    assert "陈璇飞" not in model_message
    next_action_message = p.build_user_message(tr, "next-action", {})
    assert "现场速记" not in next_action_message
    noted_message = p.build_user_message(
        tr, "next-action", {}, notes="已与老板达成一致，后续直接联系总部下单。",
    )
    assert "现场速记" in noted_message
    assert "已与老板达成一致" in noted_message
    assert noted_message.index("现场速记") < noted_message.index("完整转写")
    prompts = {module: p.system_prompt(module) for module in p.ALL_MODULES}
    assert len(set(prompts.values())) == len(p.ALL_MODULES)
    assert "九类客户关心点" not in prompts["explicit-needs"]
    assert "销售对话效能" not in prompts["quotes"]
    assert p.parse_modules("needs,quotes") == ["explicit-needs", "implicit-needs", "quotes"]
    assert p.parse_modules("next-action") == ["next-action"]
    assert "现场正在执行" in prompts["next-action"]
    assert "对话后待执行" in prompts["next-action"]

    in_call_tr = p.parse_transcript(
        "[00:00.000–00:01.000] 客户：怎么注册？\n"
        "[00:01.100–00:02.000] 销售：点击注册，输入你的电话号码。\n"
        "[00:02.100–00:03.000] 客户：身份证号码是吗？\n"
        "[00:03.100–00:04.000] 销售：对，身份证号码就行。\n"
    )
    in_call_candidate = {
        "_scope": {
            "input_quality": "可用", "quality_reason": "",
            "role_corrections": [], "exclusions": [],
        },
        "next-action": {
            "cooperation_status": "未合作", "status_evidence_ids": [],
            "action_judgment": "触发",
            "judgment_reason": "现场注册可能尚未完成，需要确认最终状态。",
            "confirmed_actions": [{
                "owner": "双方", "timeframe": "未明确时间",
                "action": "销售协助客户完成注册。",
                "evidence_ids": ["U0001", "U0002", "U0003", "U0004"],
            }],
            "recommended_actions": [],
            "second_visit": {
                "value": "无法判断", "reason": "现有对话不足以判断。",
                "evidence_ids": [],
            },
        },
    }
    errors, _ = p.validate_candidate(
        in_call_candidate, in_call_tr, ["next-action"],
    )
    assert any("现场已完成或正在执行" in error for error in errors)
    sanitized_in_call = p.sanitize_candidate(
        copy.deepcopy(in_call_candidate), in_call_tr, ["next-action"],
    )
    assert sanitized_in_call["next-action"]["confirmed_actions"] == []
    errors, _ = p.validate_candidate(
        sanitized_in_call, in_call_tr, ["next-action"],
    )
    assert any("至少需要一条已确认事项或建议行动" in error for error in errors)

    no_trigger = copy.deepcopy(value)
    no_trigger["next-action"]["action_judgment"] = "不触发"
    no_trigger["next-action"]["judgment_reason"] = "客户已明确拒绝，且未留下可继续推进的条件。"
    no_trigger["next-action"]["confirmed_actions"] = []
    no_trigger["next-action"]["recommended_actions"] = []
    no_trigger["next-action"]["second_visit"] = {
        "value": "不建议", "reason": "当前没有二次拜访的有效条件。",
        "evidence_ids": ["U0007"],
    }
    errors, _ = p.validate_candidate(
        p.split_candidate(no_trigger, "next-action"), tr, ["next-action"],
    )
    assert not errors, errors
    no_trigger_rendered = p.render_module(
        "next-action", p.split_candidate(no_trigger, "next-action"), tr,
        {"是否达成合作": "否"}, "rec_test",
    )["下一步行动策略"]
    assert "行动判断：不触发" in no_trigger_rendered
    assert "本次无需新增行动" in no_trigger_rendered

    pending = copy.deepcopy(no_trigger)
    pending["next-action"]["action_judgment"] = "待验证"
    pending["next-action"]["judgment_reason"] = "录音缺少决策人态度，暂时无法判断是否应继续拜访。"
    pending["next-action"]["second_visit"] = {
        "value": "无法判断", "reason": "需要先确认决策人及其态度。", "evidence_ids": [],
    }
    errors, _ = p.validate_candidate(
        p.split_candidate(pending, "next-action"), tr, ["next-action"],
    )
    assert not errors, errors
    pending_rendered = p.render_module(
        "next-action", p.split_candidate(pending, "next-action"), tr,
        {"是否达成合作": "否"}, "rec_test",
    )["下一步行动策略"]
    assert "行动判断：待验证" in pending_rendered
    assert "待补充关键信息后再生成行动" in pending_rendered

    inconsistent = copy.deepcopy(value)
    inconsistent["next-action"]["action_judgment"] = "不触发"
    errors, _ = p.validate_candidate(
        p.split_candidate(inconsistent, "next-action"), tr, ["next-action"],
    )
    assert any("不得输出已确认事项或建议行动" in error for error in errors)

    weak_assent_tr = p.parse_transcript(
        "[00:00.000–00:01.000] 客户：我还有一个手机号。\n"
        "[00:01.100–00:02.000] 销售：那你到时候自己重新注册一个小红书账号。\n"
        "[00:02.100–00:03.000] 客户：嗯。\n"
    )
    weak_assent = {
        "_scope": {
            "input_quality": "可用", "quality_reason": "",
            "role_corrections": [], "exclusions": [],
        },
        "next-action": {
            "cooperation_status": "未合作", "status_evidence_ids": [],
            "action_judgment": "触发",
            "judgment_reason": "需要确认客户是否愿意重新注册账号。",
            "confirmed_actions": [{
                "owner": "客户", "timeframe": "未明确时间",
                "action": "客户重新注册一个小红书账号。",
                "evidence_ids": ["U0002", "U0003"],
            }],
            "recommended_actions": [],
            "second_visit": {
                "value": "无法判断", "reason": "客户尚未形成明确承诺。",
                "evidence_ids": [],
            },
        },
    }
    errors, _ = p.validate_candidate(
        weak_assent, weak_assent_tr, ["next-action"],
    )
    assert any("仅以嗯/好/行/可以附和" in error for error in errors)

    # 复核只比较业务判断骨架，不因标题、解释或金句润色差异触发第三跑。
    explicit_a = p.split_candidate(value, "explicit-needs")
    explicit_b = copy.deepcopy(explicit_a)
    explicit_b["explicit-needs"]["items"][0]["need"] = "需要避免低价竞争侵蚀利润。"
    explicit_b["explicit-needs"]["items"][0]["explanation"] = "换一种合格表述。"
    assert (
        p.module_signature("explicit-needs", explicit_a)
        == p.module_signature("explicit-needs", explicit_b)
    )
    quotes_a = p.split_candidate(value, "quotes")
    quotes_b = copy.deepcopy(quotes_a)
    quotes_b["quotes"]["items"][0]["polished_quote"] = "担心的不只是进价，而是没有真实利润。"
    assert p.module_signature("quotes", quotes_a) == p.module_signature("quotes", quotes_b)
    profile_a = p.split_candidate(value, "store-profile")
    profile_b = copy.deepcopy(profile_a)
    profile_b["store-profile"]["one_line"] = "换一种合格的一句话画像。"
    profile_b["store-profile"]["sections"]["price_profit"]["content"] = "换一种合格表述。"
    assert (
        p.module_signature("store-profile", profile_a)
        == p.module_signature("store-profile", profile_b)
    )
    action_a = p.split_candidate(value, "next-action")
    action_b = copy.deepcopy(action_a)
    action_b["next-action"]["recommended_actions"][0]["action"] = "换一种合格动作表述。"
    action_b["next-action"]["recommended_actions"][0]["reason"] = "换一种合格原因表述。"
    assert p.module_signature("next-action", action_a) == p.module_signature("next-action", action_b)

    # 二跑无效时保留首轮硬校验合格结果并返回复核问题，不丢失结果。
    original_analyze = p.analyze
    try:
        p.analyze = lambda *args, **kwargs: (None, [{"attempt": "review"}], ["模拟校验失败"])
        selected, issues, usages = p.review_candidates(
            explicit_a, tr, ["explicit-needs"], {}, "test-model", 0.0, "all"
        )
        assert selected["explicit-needs"] == explicit_a
        assert issues and "第二跑未通过" in issues[0]
        assert len(usages) == 1

        # 双跑分歧时第三跑与第二跑一致，按多数裁决选择第二跑。
        explicit_c = copy.deepcopy(explicit_a)
        explicit_c["explicit-needs"]["items"][0]["evidence_ids"] = ["U0007"]
        responses = iter([
            (explicit_c, [{"attempt": "second"}], []),
            (copy.deepcopy(explicit_c), [{"attempt": "third"}], []),
        ])
        p.analyze = lambda *args, **kwargs: next(responses)
        selected, issues, usages = p.review_candidates(
            explicit_a, tr, ["explicit-needs"], {}, "test-model", 0.0, "all"
        )
        assert not issues
        assert selected["explicit-needs"] == explicit_c
        assert len(usages) == 2
    finally:
        p.analyze = original_analyze

    row_a = {"_record_id": "rec_a"}
    row_b = {"_record_id": "rec_b"}
    jobs = p.flatten_module_jobs([
        (row_a, ["explicit-needs", "quotes"]),
        (row_b, ["concerns"]),
    ])
    assert [(row["_record_id"], module) for row, module in jobs] == [
        ("rec_a", "explicit-needs"),
        ("rec_a", "quotes"),
        ("rec_b", "concerns"),
    ]
    assert "不得让角色纠正与证据引用自相矛盾" in prompts["concerns"]

    # next-action 只看「下一步行动策略」判断状态（是否达成合作默认值「否」无信息量）
    assert p.module_state({"下一步行动策略": ""}, "next-action") == "empty"
    assert p.module_state({"下一步行动策略": "行动判断：触发"}, "next-action") == "complete"
    assert p.module_state(
        {"显性需求(仅供参考)": "1. 需求点：x"}, "explicit-needs"
    ) == "partial"
    assert p.module_state(
        {
            "显性需求(仅供参考)": "1. 需求点：x",
            "显性需求_原句参考": "1. 客户原话",
        }, "explicit-needs",
    ) == "complete"

    with tempfile.TemporaryDirectory() as tmp:
        logger = p.RunLogger(Path(tmp))
        logger.emit("events", {
            "record_id": "rec_a", "module": "quotes", "status": "started",
        })
        logger.close()
        event = json.loads((Path(tmp) / "events.jsonl").read_text(encoding="utf-8"))
        assert event == {
            "record_id": "rec_a", "module": "quotes", "status": "started",
        }

    with tempfile.TemporaryDirectory() as tmp:
        runs = Path(tmp)
        source = runs / "source"
        source.mkdir()
        (source / "run_manifest.json").write_text("{}", encoding="utf-8")
        root, rescue_round = p.rescue_budget(runs, source)
        assert root == str(source.resolve())
        assert rescue_round == 1
        child = runs / "child"
        child.mkdir()
        (child / "run_manifest.json").write_text(json.dumps({
            "rescue_root": root,
            "rescue_round": 1,
        }), encoding="utf-8")
        same_root, rescue_round = p.rescue_budget(runs, source)
        assert same_root == root
        assert rescue_round == 2

    broken = copy.deepcopy(value)
    broken["quotes"]["items"][0]["evidence_ids"] = ["U9999"]
    errors, _ = p.validate_candidate(broken, tr, p.ALL_MODULES)
    assert any("不存在" in error for error in errors)

    broken = copy.deepcopy(value)
    broken["concerns"]["categories"][0]["name"] = "随意新增类目"
    errors, _ = p.validate_candidate(broken, tr, p.ALL_MODULES)
    assert any("九类白名单" in error for error in errors)

    broken = copy.deepcopy(value)
    broken["implicit-needs"]["items"][0]["evidence_ids"] = ["U0005"]
    errors, _ = p.validate_candidate(broken, tr, p.ALL_MODULES)
    assert any("至少需要2个" in error for error in errors)

    low_confidence = copy.deepcopy(value)
    low_confidence["implicit-needs"]["items"][0]["confidence"] = "低"
    low_confidence["implicit-needs"]["items"][0]["evidence_ids"] = ["U0005"]
    errors, _ = p.validate_candidate(low_confidence, tr, p.ALL_MODULES)
    assert not errors, errors

    absent = copy.deepcopy(value)
    absent["effectiveness"]["scores"][2]["score"] = 0
    absent["effectiveness"]["scores"][2]["judgment"] = "完整对话中未触及问题影响或经济意义。"
    absent["effectiveness"]["scores"][2]["evidence_ids"] = []
    errors, _ = p.validate_candidate(absent, tr, p.ALL_MODULES)
    assert not errors, errors

    dirty = copy.deepcopy(value)
    dirty["store-profile"]["one_line"] = "南京江宁区一家价格敏感的门店"
    dirty["store-profile"]["one_line_evidence_ids"] = ["U0003"]
    dirty["store-profile"]["sections"]["price_profit"]["content"] = (
        "当前价格敏感（U0003）。销售提及免费服务。"
    )
    sanitized = p.sanitize_candidate(
        dirty, tr, ["store-profile"],
        {"门店编号": "TEST-001", "门店名称": "测试门店", "门店联系人": "陈璇飞"},
    )
    assert "江宁区" not in sanitized["store-profile"]["one_line"]
    assert "U0003" not in sanitized["store-profile"]["sections"]["price_profit"]["content"]
    assert "销售提及" not in sanitized["store-profile"]["sections"]["price_profit"]["content"]

    dirty = copy.deepcopy(value)
    dirty["store-profile"]["one_line"] = (
        "测试门店（门店编号TEST-001）的负责人陈璇飞，目前对价格透明敏感。"
    )
    dirty["store-profile"]["one_line_evidence_ids"] = ["U0003"]
    sanitized = p.sanitize_candidate(
        dirty, tr, ["store-profile"],
        {"门店编号": "TEST-001", "门店名称": "测试门店", "门店联系人": "陈璇飞"},
    )
    assert "负责人陈璇飞" not in sanitized["store-profile"]["one_line"]

    # ---- 知识库：注入 / 回退 / 校验 ----
    baseline = p.system_prompt("store-profile")
    with tempfile.TemporaryDirectory() as tmp:
        kb_root = Path(tmp)
        (kb_root / "README.md").write_text("# KB\n", encoding="utf-8")
        (industry := kb_root / "industry").mkdir()
        (industry / "README.md").write_text(
            "---\ntype: domain\nname: 行业\ncode: industry\n---\n", encoding="utf-8"
        )
        (industry / "store-models.md").write_text(KB_FILE_TEXT, encoding="utf-8")
        manifest_path = kb_root / "manifest.json"
        manifest_path.write_text(json.dumps({
            "version": 1,
            "modules": {
                "store-profile": {
                    "instruction": "知识只用于归类。",
                    "max_tokens": 2000,
                    "selection": [{"type": "file", "path": "industry/store-models.md"}],
                },
                "quotes": {"max_tokens": 400, "selection": []},
            },
        }, ensure_ascii=False), encoding="utf-8")

        k.configure(root=kb_root, enabled=True, manifest_path=manifest_path)
        injected = p.system_prompt("store-profile")
        assert injected != baseline
        assert "# 参考知识库（通用分类参考）" in injected
        assert "## 知识#ind-model-001 线下现货模式" in injected
        assert "## 知识#ind-model-002" in injected
        assert "唯一任务：门店档案" in injected
        assert "参考知识库" not in p.system_prompt("quotes")  # 未配置 selection 的模块无注入
        assert k.injected_ids("store-profile") == frozenset({"ind-model-001", "ind-model-002"})
        allowed = k.injected_ids("store-profile")
        assert k.validate_knowledge_refs({"x": "知识#ind-model-001"}, allowed) == []
        assert k.validate_knowledge_refs({"x": "知识#ind-model-999"}, allowed) == ["ind-model-999"]

        # 禁用后与基线逐字节一致
        k.configure(root=kb_root, enabled=False, manifest_path=manifest_path)
        assert p.system_prompt("store-profile") == baseline

        # 好 KB 校验通过
        k.configure(root=kb_root, enabled=True, manifest_path=manifest_path)
        errs, _ = k.validate_knowledge(kb_root)
        assert not errs, errs
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        merrs, _ = k.validate_manifest(manifest, k.load_knowledge_base(kb_root), set(p.ALL_MODULES))
        assert not merrs, merrs

        # 坏 KB：重复条目 id + 孤立引用
        (industry / "bad.md").write_text(BAD_KB_TEXT, encoding="utf-8")
        errs, _ = k.validate_knowledge(kb_root)
        assert any("重复条目 id: ind-model-001" in e for e in errs)
        assert any("引用未知/非 active 知识#ind-model-999" in e for e in errs)

        # manifest 校验抓未知模块
        merrs, _ = k.validate_manifest(
            {"version": 1, "modules": {"bogus-module": {"max_tokens": 1, "selection": []}}},
            k.load_knowledge_base(kb_root), set(p.ALL_MODULES),
        )
        assert any("未知模块" in e for e in merrs)

    # 恢复知识配置（从 config 读取），不污染后续
    k.configure(
        root=p.CFG.get("knowledge_root"),
        enabled=p.CFG.get("knowledge_enabled", True),
        mode=p.CFG.get("knowledge_mode", "manifest"),
    )

    # schema 允许 lookup/auto_number 作为只读 identity 字段。
    schema_profile = {
        "source_field": "完整对话-文字",
        "cooperation_field": "是否达成合作",
        "identity_fields": ["门店编号", "门店名称"],
    }
    schema_names = {
        schema_profile["source_field"], schema_profile["cooperation_field"],
        *schema_profile["identity_fields"],
    }
    for values in p.MODULE_FIELDS.values():
        schema_names.update(values)
    schema_fields = [
        {"name": name, "type": "text"}
        for name in sorted(schema_names)
    ]
    by_name = {field["name"]: field for field in schema_fields}
    by_name["门店编号"]["type"] = "lookup"
    by_name["门店名称"]["type"] = "auto_number"
    by_name["是否达成合作"].update({
        "type": "select", "multiple": False,
        "options": [{"name": "是"}, {"name": "否"}],
    })
    by_name["关心类目打标"].update({
        "type": "select", "multiple": True,
        "options": [{"name": name} for name in p.CONCERN_NAMES],
    })
    original_lark = p.lark
    try:
        p.lark = lambda *args, **kwargs: {"fields": schema_fields}
        validated = p.validate_schema("base_test", "table_test", schema_profile)
        assert validated["门店编号"]["type"] == "lookup"
        assert validated["门店名称"]["type"] == "auto_number"
    finally:
        p.lark = original_lark

    print("self_test: OK")


if __name__ == "__main__":
    main()
