"""录音文件存储：先存服务器本地磁盘；以后换阿里云 OSS 只需替换本文件的实现"""

import time
from pathlib import Path
from urllib.parse import quote

from ..config import get_settings
from ..security import sign_file


def _root() -> Path:
    root = Path(get_settings().storage_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def path_of(key: str) -> Path:
    p = (_root() / key).resolve()
    if _root() not in p.parents:
        raise ValueError("非法文件路径")
    return p


def put(key: str, data: bytes) -> int:
    p = path_of(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return len(data)


def url_of(key: str, expires: int | None = None) -> str:
    """带时效的下载链接（小程序播放录音、以后给火山语音识别读取）"""
    s = get_settings()
    exp = int(time.time()) + (expires or s.file_url_expire_seconds)
    return f"{s.public_base_url}/files/{quote(key)}?exp={exp}&sig={sign_file(key, exp)}"
