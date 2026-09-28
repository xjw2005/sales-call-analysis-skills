"""把「周五前」「下周二」「本周内」「3天内」「10月8日」等时限文字解析成具体日期"""

import re
from datetime import date, timedelta

WEEKDAYS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
DEFAULT_DAYS = 3


def _num(s: str) -> int | None:
    if s.isdigit():
        return int(s)
    return CN_NUM.get(s)


def parse_due(text: str, base: date) -> date | None:
    """base 为拜访当天；无法识别时返回 None"""
    t = (text or "").strip()
    if not t:
        return None
    if "今天" in t or "当天" in t or "当场" in t:
        return base
    if "明天" in t:
        return base + timedelta(days=1)
    if "后天" in t:
        return base + timedelta(days=2)
    m = re.search(r"(\d{1,2})月(\d{1,2})[日号]", t)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        year = base.year + (1 if month < base.month else 0)
        try:
            return date(year, month, day)
        except ValueError:
            return None
    m = re.search(r"(下)?(?:周|星期|礼拜)([一二三四五六日天])", t)
    if m:
        target = WEEKDAYS[m.group(2)]
        if m.group(1):  # 下周X
            monday_next = base + timedelta(days=7 - base.weekday())
            return monday_next + timedelta(days=target)
        delta = (target - base.weekday()) % 7
        return base + timedelta(days=delta or 7)
    if "本周" in t or "这周" in t:
        return base + timedelta(days=6 - base.weekday())
    if "下周" in t:
        return base + timedelta(days=13 - base.weekday())
    m = re.search(r"([\d一两二三四五六七八九十]+)\s*(天|日)内?", t)
    if m and _num(m.group(1)):
        return base + timedelta(days=_num(m.group(1)))
    m = re.search(r"([\d一两二三四五六七八九十]+)\s*周内?", t)
    if m and _num(m.group(1)):
        return base + timedelta(weeks=_num(m.group(1)))
    if "月底" in t or "本月" in t:
        nxt = date(base.year + (base.month == 12), base.month % 12 + 1, 1)
        return nxt - timedelta(days=1)
    return None


def due_or_default(text: str, base: date, due_days: int | None = None) -> date:
    if due_days is not None:
        return base + timedelta(days=int(due_days))
    return parse_due(text, base) or base + timedelta(days=DEFAULT_DAYS)
