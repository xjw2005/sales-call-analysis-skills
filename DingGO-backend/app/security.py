import hashlib
import hmac
import time
from datetime import datetime, timedelta, timezone

import jwt

from .config import get_settings


def create_token(user_id: int) -> str:
    s = get_settings()
    exp = datetime.now(timezone.utc) + timedelta(days=s.jwt_expire_days)
    return jwt.encode({"sub": str(user_id), "exp": exp}, s.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> int | None:
    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


# 录音文件下载链接签名：/files/<key>?exp=<秒>&sig=<hmac>
def sign_file(key: str, exp: int) -> str:
    msg = f"{key}:{exp}".encode()
    return hmac.new(get_settings().jwt_secret.encode(), msg, hashlib.sha256).hexdigest()


def verify_file(key: str, exp: int, sig: str) -> bool:
    return exp >= int(time.time()) and hmac.compare_digest(sign_file(key, exp), sig)


# 绑定令牌：微信身份已经验证（拿到 openid），但还没绑定到人员；只能用来提交绑定码，15 分钟有效
def create_bind_token(openid: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=15)
    return jwt.encode({"typ": "bind", "openid": openid, "exp": exp}, get_settings().jwt_secret, algorithm="HS256")


def decode_bind_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
        return payload["openid"] if payload.get("typ") == "bind" else None
    except (jwt.PyJWTError, KeyError):
        return None


def hash_bind_code(code: str) -> str:
    return hashlib.sha256(f"bind:{get_settings().jwt_secret}:{code.strip()}".encode()).hexdigest()
