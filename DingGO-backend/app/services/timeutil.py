from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from ..config import get_settings


def tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def to_ms(dt: datetime | None) -> int | None:
    """数据库里存的是 UTC（不带时区），接口统一返回毫秒时间戳"""
    if dt is None:
        return None
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


def local_date(dt: datetime) -> date:
    return dt.replace(tzinfo=timezone.utc).astimezone(tz()).date()


def today() -> date:
    return datetime.now(tz()).date()


def end_of_day_ms(d: date) -> int:
    return int(datetime.combine(d, time(23, 59, 59), tzinfo=tz()).timestamp() * 1000)


def fmt_date(ms: int) -> str:
    """与小程序 utils/format.js 的 date() 一致：9月28日 14:05"""
    d = datetime.fromtimestamp(ms / 1000, tz())
    return f"{d.month}月{d.day}日 {d.hour:02d}:{d.minute:02d}"


def fmt_duration(sec: int) -> str:
    sec = max(0, int(sec or 0))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
