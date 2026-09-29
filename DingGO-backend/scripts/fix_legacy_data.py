"""按映射文件整改导入后的遗留数据：合并重复门店、补回平台门店 ID、把没对上门店的待办挂到门店。

    python scripts/fix_legacy_data.py --plan fix.json            # 只试运行，逐步列出会改什么
    python scripts/fix_legacy_data.py --plan fix.json --commit   # 确认后真正写库

映射文件（JSON，示例见 README「整改遗留数据」）：
{
  "merge_stores":    [{"keep": "ST-1005", "drop": "ST-1006"}],
  "set_external_id": [{"code": "ST-1005", "platform": "智生活", "externalId": "7378"}],
  "link_todo":       [{"topic": "登康：9月任务拆解", "storeCode": "ST-0123", "assigneeId": null}]
}

- 按 merge_stores → set_external_id → link_todo 的顺序执行；整个文件在一个事务里，任何一步出错都全部回滚。
- 可以重复运行：已经做过的步骤会显示「已完成，跳过」。
- 合并只搬迁 drop 门店名下的拜访、待办、档案、纠正记录；同一档案维度两边都有内容时保留 keep 的。
- 重要：整改之后不要再重新导入 Excel 的门店和待办部分，否则被合并的门店会按门店编号再建一遍。
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select, update  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models import (  # noqa: E402
    CorrectionEvent, Store, StoreCorrection, StoreDirectory, StoreProfileSection, Todo, Visit,
)

FILL_FIELDS = [
    "store_type", "province", "city", "district", "address", "grid", "contact_name", "contact_phone",
    "installed_at", "wecom_added", "photo_key", "lat", "lng", "primary_sales_id", "directory_id",
]
STATUS_RANK = {"未触达": 0, "意向中": 1, "已触达未合作": 2, "已合作": 3}


class PlanError(Exception):
    pass


def store_by_code(db: Session, code: str) -> Store | None:
    return db.scalar(select(Store).where(Store.code == code))


def merge_stores(db: Session, keep_code: str, drop_code: str, log: list[str]) -> None:
    keep, drop = store_by_code(db, keep_code), store_by_code(db, drop_code)
    if drop is None:
        if keep is not None:
            log.append(f"合并 {drop_code} → {keep_code}：已完成，跳过")
            return
        raise PlanError(f"门店 {keep_code} 和 {drop_code} 都不存在")
    if keep is None:
        raise PlanError(f"保留的门店 {keep_code} 不存在")
    if keep.id == drop.id:
        raise PlanError(f"{keep_code} 不能合并给自己")

    moved = {}
    for label, model in (("拜访", Visit), ("待办", Todo), ("纠正", StoreCorrection), ("修改记录", CorrectionEvent)):
        n = db.scalar(select(func.count()).select_from(model).where(model.store_id == drop.id))
        db.execute(update(model).where(model.store_id == drop.id).values(store_id=keep.id))
        moved[label] = n

    keep_secs = {x.key: x for x in db.scalars(select(StoreProfileSection).where(StoreProfileSection.store_id == keep.id))}
    sec_moved = 0
    for sec in list(db.scalars(select(StoreProfileSection).where(StoreProfileSection.store_id == drop.id))):
        mine = keep_secs.get(sec.key)
        if mine is None:
            sec.store_id = keep.id
            sec_moved += 1
        elif (not (mine.content or "").strip() or mine.state == "未确认") and (sec.content or "").strip() and sec.state != "未确认":
            mine.content, mine.state, mine.evidence, mine.source_visit_id = sec.content, sec.state, sec.evidence, sec.source_visit_id
            db.delete(sec)
            sec_moved += 1
        else:
            db.delete(sec)
    moved["档案维度"] = sec_moved

    filled = []
    for f in FILL_FIELDS:
        if getattr(keep, f) in (None, "") and getattr(drop, f) not in (None, ""):
            setattr(keep, f, getattr(drop, f))
            filled.append(f)
    if STATUS_RANK.get(drop.cooperation_status, 0) > STATUS_RANK.get(keep.cooperation_status, 0):
        keep.cooperation_status = drop.cooperation_status
        filled.append("cooperation_status")
    db.flush()
    db.delete(drop)
    db.flush()
    log.append(f"合并 {drop_code} {drop.name} → {keep_code} {keep.name}：迁移 " + "、".join(f"{k} {v}" for k, v in moved.items())
               + (f"；补全字段 {','.join(filled)}" if filled else ""))


def set_external_id(db: Session, code: str, platform: str, external_id: str, log: list[str]) -> None:
    st = store_by_code(db, code)
    if st is None:
        raise PlanError(f"门店 {code} 不存在")
    if st.platform == platform and st.external_id == external_id:
        log.append(f"设置 {code} 的 {platform}/{external_id}：已完成，跳过")
        return
    other = db.scalar(select(Store).where(Store.platform == platform, Store.external_id == external_id, Store.id != st.id))
    if other is not None:
        raise PlanError(f"{platform}/{external_id} 已被 {other.code} {other.name} 占用，无法设置给 {code}")
    st.platform, st.external_id = platform, external_id
    d = db.scalar(select(StoreDirectory).where(StoreDirectory.platform == platform, StoreDirectory.external_id == external_id))
    st.directory_id = d.id if d else st.directory_id
    db.flush()
    log.append(f"设置 {code} {st.name} 的 {platform}/{external_id}" + ("（已关联总门店清单）" if d else "（总门店清单里没有这个 ID）"))


def link_todo(db: Session, topic: str, store_code: str, assignee_id: int | None, log: list[str]) -> None:
    st = store_by_code(db, store_code)
    if st is None:
        raise PlanError(f"门店 {store_code} 不存在")
    todos = list(db.scalars(select(Todo).where(Todo.source == "import", Todo.store_id.is_(None), Todo.topic == topic)))
    if not todos:
        log.append(f"待办「{topic}」→ {store_code}：没有找到未挂门店的同名待办，跳过（可能已完成）")
        return
    new_topic = topic.split("：", 1)[1] if "：" in topic else topic
    for t in todos:
        t.store_id = st.id
        t.assignee_id = assignee_id or st.primary_sales_id
        t.topic = new_topic[:128]
    db.flush()
    log.append(f"待办「{topic}」→ {store_code} {st.name}：{len(todos)} 条（执行人 {todos[0].assignee_id or '无'}）")


def apply_plan(db: Session, plan: dict) -> list[str]:
    log: list[str] = []
    for m in plan.get("merge_stores", []):
        merge_stores(db, m["keep"], m["drop"], log)
    for e in plan.get("set_external_id", []):
        set_external_id(db, e["code"], e["platform"], str(e["externalId"]), log)
    for t in plan.get("link_todo", []):
        link_todo(db, t["topic"], t["storeCode"], t.get("assigneeId"), log)
    return log


def main() -> None:
    parser = argparse.ArgumentParser(description="按映射文件整改导入后的遗留数据")
    parser.add_argument("--plan", required=True, help="映射文件（JSON）")
    parser.add_argument("--commit", action="store_true", help="真正写库（默认只试运行）")
    args = parser.parse_args()
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))

    from app.db import SessionLocal

    with SessionLocal() as db:
        try:
            log = apply_plan(db, plan)
            db.commit() if args.commit else db.rollback()
        except PlanError as e:
            db.rollback()
            print(f"出错，已全部回滚：{e}")
            sys.exit(1)
        except BaseException:
            db.rollback()
            raise
    print("\n".join(log) or "映射文件里没有任何步骤")
    print("\n" + ("已写入数据库。" if args.commit else "这是试运行，没有写库。确认无误后加 --commit。"))


if __name__ == "__main__":
    main()
