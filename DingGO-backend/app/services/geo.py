"""位置相关的小工具：两点距离、坐标是否合理、距离怎么念。

坐标一律是国测局坐标（gcj02），和微信地图、腾讯地图一致。"""

import math

EARTH_KM = 6371.0088
# 中国大陆及周边的粗略范围：门店坐标落在范围外，多半是地址解析错了或填反了经纬度
CHINA_LAT = (3.0, 54.0)
CHINA_LNG = (73.0, 136.0)


def valid_point(lat, lng) -> bool:
    """是不是一个能用的坐标：是数字、在合法范围内、不是 (0,0)（没定位上时常见的占位值）"""
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return False
    if not (math.isfinite(lat) and math.isfinite(lng)):
        return False
    return -90 <= lat <= 90 and -180 <= lng <= 180 and not (lat == 0 and lng == 0)


def in_china(lat, lng) -> bool:
    return valid_point(lat, lng) and CHINA_LAT[0] <= float(lat) <= CHINA_LAT[1] and CHINA_LNG[0] <= float(lng) <= CHINA_LNG[1]


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """球面两点距离（公里）。城市内的误差在百米以内，推荐「近的店」足够"""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(min(1.0, math.sqrt(a)))


def distance_text(km: float) -> str:
    """距离怎么念：不到 1 公里说「320 米」，1~100 公里说「1.2 公里」，更远取整"""
    if km < 1:
        return f"{max(10, int(round(km * 1000 / 10)) * 10)} 米"
    return f"{km:.1f} 公里" if km < 100 else f"{int(round(km))} 公里"
