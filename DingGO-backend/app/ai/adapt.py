"""把校验通过的候选 JSON 转成小程序 / apply_analysis 用的分析结构，并把证据发言 ID 回填成原话和时间戳"""

from typing import Any

from ..services.constants import PROFILE_LABELS
from .core import DIMENSIONS, PROFILE_SECTIONS, Transcript, validate_candidate

CONCERN_TONE_STATES = ("是", "否", "证据不足")


def to_ms(stamp: str) -> int:
    """00:11.450 / 01:00:03.410 → 毫秒"""
    head, _, frac = stamp.partition(".")
    parts = [int(x) for x in head.split(":")]
    sec = 0
    for p in parts:
        sec = sec * 60 + p
    return sec * 1000 + int((frac + "000")[:3])


def utterances_json(tr: Transcript) -> list[dict]:
    """存进 visit_transcripts：uid 与分析证据里的发言 ID 一一对应"""
    return [{"uid": u.uid, "role": u.raw_role, "startMs": to_ms(u.start), "endMs": to_ms(u.end), "text": u.text} for u in tr.utterances]


class Evidence:
    def __init__(self, tr: Transcript, roles: dict[str, str]):
        self.tr, self.roles = tr, roles

    def items(self, ids: list[str]) -> list[dict]:
        by_id = self.tr.by_id
        return [
            {"uid": uid, "role": self.roles.get(uid, by_id[uid].raw_role), "startMs": to_ms(by_id[uid].start), "text": by_id[uid].text}
            for uid in dict.fromkeys(ids) if uid in by_id
        ]

    def text(self, ids: list[str]) -> str:
        return "\n".join(f"[{e['uid']}] {e['role']}：{e['text']}" for e in self.items(ids))


def _clock(ms: int) -> str:
    s = ms // 1000
    return f"{s // 60:02d}:{s % 60:02d}"


def _action_line(a: dict) -> str:
    return f"[{a['owner']}｜{a['timeframe']}] {a['action']}"


def build_analysis(selected: dict[str, dict[str, Any]], tr: Transcript, mode: str, action_history: str = "") -> dict:
    """selected：{模块: {"_scope":…, 模块: 值}}（review_candidates 的返回）。
    返回 {analysis, cooperated}：analysis 与 model/analysis.js 结构一致"""
    out: dict[str, Any] = {}
    cooperated = None
    for module, cand in selected.items():
        errors, ctx = validate_candidate(cand, tr, [module], mode, action_history)
        if errors:
            raise ValueError(f"{module} 转换前校验失败：" + "；".join(errors[:3]))
        ev = Evidence(tr, ctx.roles)
        v = cand[module]
        if module == "explicit-needs":
            out["explicitNeeds"] = [
                {"point": i["need"], "scene": i["scene"], "explanation": i["explanation"], "evidence": ev.items(i["evidence_ids"])}
                for i in v["items"]]
        elif module == "implicit-needs":
            out["implicitNeeds"] = [
                {"hypothesis": i["hypothesis"], "basis": "、".join(e["text"] for e in ev.items(i["evidence_ids"])[:2]),
                 "logic": i["logic"], "confidence": i["confidence"], "evidence": ev.items(i["evidence_ids"])}
                for i in v["items"]]
        elif module == "concerns":
            out["concerns"] = [
                {"name": c["name"], "state": c["status"], "subtype": c["subtype"], "scene": c["scene"], "object": c["object"],
                 "reason": c["reason"], "evidence": ev.items(c["evidence_ids"])}
                for c in v["categories"]]
        elif module == "effectiveness":
            maxes = dict(DIMENSIONS)
            dims = [{"name": s["name"], "max": maxes[s["name"]], "score": s["score"], "judgment": s["judgment"], "evidence": ev.items(s["evidence_ids"])}
                    for s in v["scores"]]
            out["effectiveness"] = {"total": sum(d["score"] for d in dims), "dims": dims, "conclusion": v["conclusion"], "outcome": v["sales_result"]}
        elif module == "quotes":
            out["quotes"] = [
                {"text": q["polished_quote"], "speaker": q["speaker_name"], "scene": q["scene"], "problem": q["problem"], "method": q["method"],
                 "value": q["value"], "reaction": "；".join(e["text"] for e in ev.items(q["reaction_ids"])), "evidence": ev.items(q["evidence_ids"])}
                for q in v["items"]]
        elif module == "store-profile":
            sections = [{"key": "one_line", "label": PROFILE_LABELS.get("one_line", "一句话画像"), "state": "当前状态",
                         "content": v["one_line"], "evidence": ev.text(v["one_line_evidence_ids"])}]
            for key in PROFILE_SECTIONS:
                s = v["sections"][key]
                sections.append({"key": key, "label": PROFILE_LABELS.get(key, PROFILE_SECTIONS[key]), "state": s["state_type"],
                                 "content": s["content"], "evidence": ev.text(s["evidence_ids"])})
            out["profile"] = {"sections": sections}
        elif module == "next-action":
            second = v["second_visit"]
            out["nextAction"] = {
                "judgement": v["action_judgment"], "reason": v["judgment_reason"],
                "confirmed": [_action_line(a) for a in v["confirmed_actions"]],
                "confirmedActions": [{k: a[k] for k in ("owner", "timeframe", "action")} for a in v["confirmed_actions"]],  # 录音里已确认的约定，会生成待办
                "actions": [{k: a[k] for k in ("topic", "owner", "timeframe", "action", "reason", "acceptance")} | {"evidence": ev.items(a["evidence_ids"])}
                            for a in v["recommended_actions"]],
                "revisitValue": "" if second["value"] == "不适用" else f"{second['value']}：{second['reason']}",
            }
            if "closure" in v:
                out["loop"] = {"summary": v["closure"]["summary"],
                               "items": [{"prev": i["action"], "status": i["done"], "now": i.get("reason", ""), "evidence": ev.items(i["evidence_ids"])}
                                         for i in v["closure"]["items"]]}
            else:
                cooperated = "是" if v["cooperation_status"] == "已合作" else "否"
    return {"analysis": out, "cooperated": cooperated}
