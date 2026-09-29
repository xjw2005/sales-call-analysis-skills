"""录音处理后台任务：转写 → 角色 → 有效性 → 分析 → 写库。

- 进度存在 visit_pipeline，进程重启后从断点继续；已提交的转写任务（las_tasks）不会重复提交。
- 每个阶段失败都会停在 failed，并记下失败的阶段和原因；重试从失败的阶段继续。
- 提交转写时无法确认是否已建任务（uncertain）：禁止自动重试，避免重复计费，需管理员核对后强制重试。
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import SessionLocal
from ..models import Store, StoreCorrection, StoreProfileSection, Visit, VisitAnalysis, VisitPipeline, VisitSegment, VisitTranscript
from ..services import storage
from ..services.constants import visit_mode
from ..services.ingest import apply_analysis, upsert_transcript
from . import core, transcribe
from .adapt import build_analysis, utterances_json

log = logging.getLogger("dinggo.ai")

ACTIVE_STAGES = ("queued", "asr_submit", "asr_poll", "roles", "validity", "analysis")
STAGE_STATUS = {"queued": "asr_running", "asr_submit": "asr_running", "asr_poll": "asr_running", "roles": "role_classifying",
                "validity": "role_classifying", "analysis": "analyzing"}
PROMPT_VERSION = "sales-call-v1"
_store_locks: dict[int, threading.Lock] = {}
_store_locks_guard = threading.Lock()
_active: set[int] = set()
_active_guard = threading.Lock()


def _store_lock(store_id: int) -> threading.Lock:
    with _store_locks_guard:
        return _store_locks.setdefault(store_id, threading.Lock())


def work_dir(visit_id: int) -> Path:
    return storage.path_of(f"ai/{visit_id}")


# ---------------------------------------------------------------- 入队 / 重试

def enqueue(db: Session, visit: Visit) -> VisitPipeline:
    """确认费用后调用：建立（或重置）处理进度，由后台线程接手"""
    row = db.get(VisitPipeline, visit.id)
    if row is None:
        row = VisitPipeline(visit_id=visit.id)
        db.add(row)
    row.stage, row.error, row.error_stage, row.attempts = "queued", "", "", 0
    visit.status = "asr_running"
    return row


def retry(db: Session, visit: Visit, force: bool = False) -> VisitPipeline:
    row = db.get(VisitPipeline, visit.id)
    if row is None or row.stage != "failed":
        raise ValueError("这条拜访没有失败的处理任务")
    if row.uncertain and not force:
        raise PermissionError("上次提交转写时无法确认是否已成功建立任务，为避免重复计费需管理员核对后处理")
    row.stage = row.error_stage or "queued"
    if row.stage == "asr_submit" and force:
        row.las_tasks = [t for t in (row.las_tasks or []) if t.get("taskId")]
    row.uncertain, row.error, row.attempts = False, "", row.attempts + 1
    visit.status = STAGE_STATUS.get(row.stage, "asr_running")
    return row


def rejudge(db: Session, visit: Visit) -> VisitPipeline:
    """人工把无效录音判为有效：转写已有，从分析阶段开始"""
    row = db.get(VisitPipeline, visit.id)
    if row is None:
        row = VisitPipeline(visit_id=visit.id)
        db.add(row)
    row.validity = {**(row.validity or {}), "value": "有效", "source": "manual", "reason": "销售人工判定为有效"}
    row.stage, row.error, row.error_stage = "analysis", "", ""
    visit.status = "analyzing"
    return row


# ---------------------------------------------------------------- 各阶段

def _fail(db: Session, visit: Visit, row: VisitPipeline, stage: str, exc: Exception, uncertain: bool = False) -> None:
    log.exception("visit %s stage %s failed", visit.id, stage)
    db.rollback()
    row, visit = db.get(VisitPipeline, row.visit_id), db.get(Visit, visit.id)
    row.stage, row.error_stage, row.error, row.uncertain = "failed", stage, str(exc)[:2000], uncertain
    visit.status = "failed"
    db.commit()


def _set_stage(db: Session, visit: Visit, row: VisitPipeline, stage: str) -> None:
    row.stage = stage
    visit.status = STAGE_STATUS.get(stage, visit.status)
    db.commit()


def _submit(db: Session, visit: Visit, row: VisitPipeline, las) -> None:
    segments = list(db.scalars(select(VisitSegment).where(VisitSegment.visit_id == visit.id, VisitSegment.file_missing.is_(False)).order_by(VisitSegment.seq)))
    if not segments:
        raise RuntimeError("没有可识别的录音文件")
    tasks = {t["seq"]: t for t in (row.las_tasks or [])}
    for seg in segments:
        if tasks.get(seg.seq, {}).get("taskId"):
            continue
        path = storage.path_of(seg.object_key)
        if not path.exists():
            raise RuntimeError(f"录音文件不存在：第 {seg.seq} 段")
        url = las.upload(str(path))
        response = las.submit(url, seg.format or path.suffix.lstrip("."))
        tasks[seg.seq] = {"seq": seg.seq, "taskId": transcribe.task_id_of(response), "status": transcribe.task_status(response).lower()}
        row.las_tasks = sorted(tasks.values(), key=lambda t: t["seq"])
        db.commit()  # 每提交一段立刻落库：崩溃后不会重复提交


def _poll(db: Session, visit: Visit, row: VisitPipeline, las) -> None:
    st = get_settings()
    wd = work_dir(visit.id)
    deadline = time.time() + st.las_timeout_seconds
    tasks = list(row.las_tasks or [])
    while True:
        pending = 0
        for t in tasks:
            result_path = wd / f"las-{t['seq']}.json"
            if result_path.exists():
                continue
            response = las.poll(t["taskId"])
            status = transcribe.task_status(response)
            t["status"] = status.lower()
            if status == "COMPLETED":
                transcribe.check_completed(response)
                transcribe.atomic_json(result_path, response)
            elif status in ("FAILED", "TIMEOUT"):
                row.las_tasks = tasks
                raise RuntimeError(f"转写任务失败：第 {t['seq']} 段 {status}")
            else:
                pending += 1
        row.las_tasks = list(tasks)
        db.commit()
        if not pending:
            return
        if time.time() > deadline:
            raise TimeoutError("转写等待超时，任务仍在进行，可稍后重试")
        time.sleep(st.las_poll_seconds)


def _roles(db: Session, visit: Visit, row: VisitPipeline) -> None:
    wd = work_dir(visit.id)
    payloads = [transcribe.load_json(wd / f"las-{t['seq']}.json") for t in sorted(row.las_tasks or [], key=lambda t: t["seq"])]
    labeled, source, audit, usages = transcribe.label_roles(wd, transcribe.merge_payloads(payloads))
    tr = core.parse_transcript(labeled)
    upsert_transcript(db, visit.id, utterances_json(tr), raw_text=labeled, source="asr")
    _add_usage(row, "roles", usages, {"source": source, "audit": audit})
    db.commit()


def _validity(db: Session, visit: Visit, row: VisitPipeline) -> bool:
    """返回是否继续分析"""
    tr = db.get(VisitTranscript, visit.id)
    verdict, usages = transcribe.judge_validity(tr.raw_text or "", visit.duration_sec or None)
    row.validity = verdict
    _add_usage(row, "validity", usages)
    if verdict["value"] == "有效":
        return True
    visit.status = "invalid_short" if verdict["value"] == "录音过短" else "invalid_content"
    row.stage = "done"
    db.commit()
    return False


def _add_usage(row: VisitPipeline, key: str, usages: list, extra: dict | None = None) -> None:
    data = dict(row.usage or {})
    data[key] = {"calls": usages, **(extra or {})}
    row.usage = data


# ---------------------------------------------------------------- 分析输入（从数据库取历史）

def _profile_text(db: Session, store_id: int) -> tuple[str, str]:
    """门店当前档案，写成流水线约定的正文格式；返回 (正文, 最近更新日期)"""
    secs = {s.key: s for s in db.scalars(select(StoreProfileSection).where(StoreProfileSection.store_id == store_id))}
    if not any((s.content or "").strip() and s.state != "未确认" for s in secs.values()):
        return "", ""
    lines = []
    one = secs.get("one_line")
    if one and (one.content or "").strip():
        lines.append(f"一句话画像：{one.content.strip()}")
    for key, label in core.PROFILE_SECTIONS.items():
        s = secs.get(key)
        if s:
            lines.append(f"{label}（{s.state}）：{(s.content or '未确认').strip()}")
    latest = max((s.updated_at for s in secs.values() if s.updated_at), default=None)
    return "\n".join(lines), (latest.strftime("%Y-%m-%d") if latest else "")


def _actions_text(db: Session, visit: Visit) -> str:
    """该门店上一次（早于本次）已分析拜访的「下一步行动策略」，日常拜访用来做闭环判断；没有则返回空串"""
    prev = db.scalars(select(Visit).where(Visit.store_id == visit.store_id, Visit.id != visit.id, Visit.entered_at < visit.entered_at,
                                          Visit.status.in_(["done", "partial_manual"])).order_by(Visit.entered_at.desc())).first()
    if prev is None:
        return ""
    row = db.scalars(select(VisitAnalysis).where(VisitAnalysis.visit_id == prev.id, VisitAnalysis.module == "next-action")).first()
    data = (row.corrected_result if row and row.corrected_result is not None else row.result if row else None) or {}
    if prev.legacy:  # 飞书导入：原文
        text = (data.get("text") or "").strip()
        loop = db.scalars(select(VisitAnalysis).where(VisitAnalysis.visit_id == prev.id, VisitAnalysis.module == "loop")).first()
        closure = ((loop.result or {}).get("text") or "").strip() if loop else ""
    else:
        na = data.get("nextAction") or {}
        lines = [f"行动判断：{na.get('judgement', '')}", f"判断原因：{na.get('reason', '')}", "已确认的后续事项："]
        lines += [f"{i}. {c}" for i, c in enumerate(na.get("confirmed") or [], 1)] or ["无明确约定。"]
        lines.append("\n下一步行动策略：")
        lines += [f"{i}. 【{a.get('topic', '')}｜{a.get('owner', '')}｜{a.get('timeframe', '')}】{a.get('action', '')}\n验收：{a.get('acceptance', '')}"
                  for i, a in enumerate(na.get("actions") or [], 1)] or ["本次无需新增行动。"]
        text, closure = "\n".join(lines), ""
    out = f"上次下一步行动策略：\n{text or '无'}"
    return out + (f"\n\n上次总结闭环：\n{closure}" if closure else "")


def _analyze(db: Session, visit: Visit, row: VisitPipeline) -> None:
    st = get_settings()
    core.init_knowledge()
    store = db.get(Store, visit.store_id)
    mode = visit_mode(visit.stage)
    modules = list(core.ALL_MODULES) if mode == "first" else list(core.DAILY_MODULES)
    tr = core.parse_transcript(db.get(VisitTranscript, visit.id).raw_text or "")
    identity = {"门店编号": store.code or "", "门店名称": store.name}
    corrections = [c.text for c in db.scalars(select(StoreCorrection).where(StoreCorrection.store_id == store.id).order_by(StoreCorrection.id.desc()).limit(5))]
    correction = "\n".join(corrections)
    address = "".join([store.province, store.city, store.district, store.address]).strip()
    history = ""
    profile, profile_date = _profile_text(db, store.id)
    if profile:  # 档案是逐次累计更新的，直接把当前档案作为历史依据
        history = f"进店时间：{profile_date}\n{profile}"
    action_history = _actions_text(db, visit) if mode == "daily" else ""
    if action_history and profile:
        action_history = "该门店历史门店档案（门店背景参考）：\n" + history + "\n\n" + action_history
    notes = (visit.note or "").strip()

    def run(module: str):
        kw = dict(notes=notes, mode=mode, action_history=action_history if module == "next-action" else "")
        cand, usages, errors = core.analyze(tr, module, identity, st.llm_model, st.llm_temperature,
                                            history=history if module == "store-profile" else "",
                                            address=address if module == "store-profile" else "",
                                            retain_stable=bool(history or address) and module == "store-profile",
                                            correction=correction if module == "store-profile" else "", **kw)
        if cand is None:
            return module, None, usages, errors
        selected, issues, more = core.review_candidates(
            cand, tr, [module], identity, st.llm_model, st.llm_temperature, st.review_mode,
            notes=notes, address=address if module == "store-profile" else "",
            retain_stable=bool(history or address) and module == "store-profile",
            correction=correction if module == "store-profile" else "", mode=mode,
            action_history=kw["action_history"], history=history if module == "store-profile" else "")
        if issues and st.review_policy == "strict":
            return module, None, usages + more, ["复核未收敛：" + "；".join(issues)]
        return module, selected[module], usages + more, issues

    results: dict[str, dict] = {}
    failed: dict[str, list[str]] = {}
    usages_all: dict = {}
    with _store_lock(store.id), ThreadPoolExecutor(max_workers=4) as pool:
        for module, selected, usages, notes_ in pool.map(run, modules):
            usages_all[module] = usages
            if selected is None:
                failed[module] = notes_[:5]
            else:
                results[module] = selected
    if not results:
        raise RuntimeError("所有分析模块都没有通过校验：" + "；".join(f"{m}:{e[:1]}" for m, e in failed.items()))

    built = build_analysis(results, tr, mode, action_history)
    analysis = built["analysis"]
    if built["cooperated"] and not visit.cooperated:
        visit.cooperated = built["cooperated"]
    apply_analysis(db, visit, analysis)
    for module in results:
        db.execute(VisitAnalysis.__table__.update().where(VisitAnalysis.visit_id == visit.id, VisitAnalysis.module == module)
                   .values(model=st.llm_model, prompt_version=PROMPT_VERSION))
    visit.status = "partial_manual" if failed else "done"
    _add_usage(row, "analysis", [], {"modules": usages_all, "failed": failed})
    row.stage, row.error, row.error_stage = "done", ("; ".join(f"{m}: {e[0]}" for m, e in failed.items()) if failed else ""), ""
    db.commit()


# ---------------------------------------------------------------- 主流程 / 调度

def run_visit(visit_id: int, las=None) -> None:
    """把一条拜访从当前阶段推进到结束（同步执行，供后台线程和测试调用）"""
    las = las or transcribe.LasutilClient()
    with SessionLocal() as db:
        visit, row = db.get(Visit, visit_id), db.get(VisitPipeline, visit_id)
        if visit is None or row is None:
            return
        stage = row.stage
        try:
            while row.stage in ACTIVE_STAGES:
                stage = row.stage
                if stage in ("queued", "asr_submit"):
                    _set_stage(db, visit, row, "asr_submit")
                    _submit(db, visit, row, las)
                    _set_stage(db, visit, row, "asr_poll")
                elif stage == "asr_poll":
                    _poll(db, visit, row, las)
                    _set_stage(db, visit, row, "roles")
                elif stage == "roles":
                    _roles(db, visit, row)
                    _set_stage(db, visit, row, "validity")
                elif stage == "validity":
                    if not _validity(db, visit, row):
                        return
                    _set_stage(db, visit, row, "analysis")
                elif stage == "analysis":
                    _analyze(db, visit, row)
        except transcribe.SubmitUncertain as exc:
            _fail(db, visit, row, "asr_submit", exc, uncertain=True)
        except Exception as exc:  # noqa: BLE001 任何失败都要落库，不能让线程静默退出
            _fail(db, visit, row, stage, exc)


def _run_guarded(visit_id: int) -> None:
    try:
        run_visit(visit_id)
    finally:
        with _active_guard:
            _active.discard(visit_id)


class Scheduler(threading.Thread):
    """每几秒扫一次待处理的拜访，交给线程池；进程重启后自动接着做"""

    def __init__(self) -> None:
        super().__init__(daemon=True, name="dinggo-ai-scheduler")
        self.stop = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=max(1, get_settings().worker_threads))

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                with SessionLocal() as db:
                    ids = list(db.scalars(select(VisitPipeline.visit_id).where(VisitPipeline.stage.in_(ACTIVE_STAGES))))
                for vid in ids:
                    with _active_guard:
                        if vid in _active:
                            continue
                        _active.add(vid)
                    self.pool.submit(_run_guarded, vid)
            except Exception:  # noqa: BLE001
                log.exception("scheduler loop failed")
            self.stop.wait(5)


def configured() -> bool:
    st = get_settings()
    return bool(st.las_api_key and st.llm_api_key and st.llm_api_url)
