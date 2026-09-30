import importlib.util
import sys
from pathlib import Path

from app.db import SessionLocal
from app.models import Store, StoreDirectory

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "backfill_district.py"
spec = importlib.util.spec_from_file_location("backfill_district", SCRIPT)
bf = importlib.util.module_from_spec(spec)
sys.modules["backfill_district"] = bf
spec.loader.exec_module(bf)


def test_parse_district():
    assert bf.parse_district("云南省昆明市官渡区广居路魅力之城一期7栋3号") == "官渡区"
    assert bf.parse_district("玉溪市江川区大街街道星云路锦华苑") == "江川区"
    assert bf.parse_district("贵州省遵义市绥阳县诗乡广场") == "绥阳县"
    assert bf.parse_district("彩云城辰湾5B-S3") == ""
    assert bf.parse_district("云南省昆明市滇池花园小区3栋") == ""
    assert bf.parse_district("") == ""


def test_backfill_prefers_directory_then_address_and_is_idempotent():
    with SessionLocal() as db:
        d = StoreDirectory(platform="智生活", external_id="1", name="甲", district="渝中区")
        db.add(d)
        db.flush()
        db.add_all([
            Store(code="S1", name="甲店", directory_id=d.id, address="随便写"),
            Store(code="S2", name="乙店", address="云南省昆明市官渡区广居路1号"),
            Store(code="S3", name="丙店", address="没有区县的地址"),
            Store(code="S4", name="丁店", district="九龙坡区", address="云南省昆明市官渡区"),
        ])
        db.commit()
        r = bf.backfill(db)
        db.commit()
        assert r["stats"] == {"总门店清单": 1, "地址解析": 1, "补不了": 1} and r["unresolved"] == ["丙店"]
        by = {s.code: s.district for s in db.query(Store)}
        assert by == {"S1": "渝中区", "S2": "官渡区", "S3": "", "S4": "九龙坡区"}  # 已有的不动
        assert bf.backfill(db)["stats"] == {"补不了": 1}
