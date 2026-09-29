import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from .ai import worker
from .routers import admin, assistant, auth, files, stores, todos, visits


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 后台处理线程：配置了 LAS 和大模型密钥才启动；测试里用 AI_WORKER=false 关闭
    scheduler = None
    if os.environ.get("AI_WORKER", "true").lower() != "false" and worker.configured():
        scheduler = worker.Scheduler()
        scheduler.start()
    yield
    if scheduler:
        scheduler.stop.set()


app = FastAPI(title="DingGo 销售助手后端", version="0.1.0", lifespan=lifespan)


# 小程序 services/request.js 读取 message 字段展示错误
@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"message": exc.detail})


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(x) for x in first.get("loc", [])[1:])
    return JSONResponse(status_code=422, content={"message": f"参数错误：{field} {first.get('msg', '')}".strip()})


@app.get("/health")
def health():
    return {"ok": True}


for r in (auth, stores, visits, todos, assistant, files, admin):
    app.include_router(r.router)
