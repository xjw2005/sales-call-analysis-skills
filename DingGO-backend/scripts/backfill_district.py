"""给门店补全区县：优先用总门店清单里的区县，其次从地址里解析。默认只试运行，加 --commit 才写库。

    python scripts/backfill_district.py            # 试运行：统计能补多少、补不了的有哪些
    python scripts/backfill_district.py --commit   # 写库

- 只补「区县为空」的门店，已有区县的不动；可重复运行。
- 「按片区选店」依赖区县；补不了的（地址不含区县）会列出名称，可以以后在门店资料里手动改。
"""

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.models import Store, StoreDirectory  # noqa: E402

DISTRICT_RE = re.compile(r"(?:省|市|州|盟|自治区)([一-龥]{1,4}?(?:新区|区|县|旗))")
NOT_DISTRICT = {"开发区", "经开区", "高新区", "新区", "小区", "园区", "社区", "校区", "景区", "商业区", "居住区"}


def parse_district(address: str) -> str:
    """从「云南省昆明市官渡区广居路…」里取出「官渡区」；取不到返回空串"""
    for m in DISTRICT_RE.finditer(address or ""):
        name = m.group(1)
        if name not in NOT_DISTRICT and not name.endswith(("小区", "社区", "园区")):
            return name
    return ""


def backfill(db) -> dict:
    stats = Counter()
    unresolved: list[str] = []
    directory = {d.id: d for d in db.scalars(select(StoreDirectory))}
    for st in db.scalars(select(Store).where((Store.district == "") | Store.district.is_(None))):
        d = directory.get(st.directory_id)
        district = (d.district or "").strip() if d else ""
        source = "总门店清单"
        if not district:
            district = parse_district(f"{st.city}{st.address}") or parse_district(d.address if d else "")
            source = "地址解析"
        if district:
            st.district = district[:32]
            stats[source] += 1
        else:
            stats["补不了"] += 1
            unresolved.append(st.name)
    db.flush()
    return {"stats": dict(stats), "unresolved": unresolved}


def main() -> None:
    parser = argparse.ArgumentParser(description="给门店补全区县")
    parser.add_argument("--commit", action="store_true", help="真正写库（默认只试运行）")
    args = parser.parse_args()

    from app.db import SessionLocal

    with SessionLocal() as db:
        try:
            result = backfill(db)
            dist = Counter(d for d in db.scalars(select(Store.district).where(Store.district != "")))
            db.commit() if args.commit else db.rollback()
        except BaseException:
            db.rollback()
            raise
    print("补全结果：", result["stats"])
    print("补不了区县的门店：", len(result["unresolved"]), "家；前 15 家：", "、".join(result["unresolved"][:15]))
    print("门店最多的 10 个区县：", dist.most_common(10))
    print("已写入数据库。" if args.commit else "这是试运行，没有写库。确认无误后加 --commit。")


if __name__ == "__main__":
    main()
