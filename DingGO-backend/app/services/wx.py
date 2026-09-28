import httpx
from fastapi import HTTPException

from ..config import get_settings


def code_to_openid(code: str) -> str:
    """wx.login 的 code 换 openid；未配置 AppSecret 且开启开发登录时，直接用 code 当账号"""
    s = get_settings()
    if not s.wx_secret:
        if s.dev_login:
            return f"dev-{code}"
        raise HTTPException(status_code=500, detail="服务器未配置小程序 AppSecret")
    resp = httpx.get(
        "https://api.weixin.qq.com/sns/jscode2session",
        params={"appid": s.wx_appid, "secret": s.wx_secret, "js_code": code, "grant_type": "authorization_code"},
        timeout=10,
    )
    data = resp.json()
    if not data.get("openid"):
        raise HTTPException(status_code=401, detail=f"微信登录失败：{data.get('errmsg', '未知错误')}")
    return data["openid"]
