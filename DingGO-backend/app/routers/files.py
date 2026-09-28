from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..security import verify_file
from ..services import storage

router = APIRouter(tags=["文件"])


@router.get("/files/{key:path}")
def get_file(key: str, exp: int, sig: str):
    """带签名的录音下载链接（链接本身就是凭证，过期失效）"""
    if not verify_file(key, exp, sig):
        raise HTTPException(status_code=403, detail="链接无效或已过期")
    try:
        path = storage.path_of(key)
    except ValueError:
        raise HTTPException(status_code=400, detail="非法路径") from None
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path)
