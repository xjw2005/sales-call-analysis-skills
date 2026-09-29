FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Shanghai
WORKDIR /app

# 默认用阿里云 pip 镜像（国内服务器快）；其他环境可 --build-arg PIP_INDEX_URL=... 覆盖
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
COPY requirements.txt .
RUN pip install --no-cache-dir -i ${PIP_INDEX_URL} -r requirements.txt

# 火山 LAS 命令行（lasutil，语音转写用）；下载失败不会阻断构建，但转写时会提示找不到 lasutil
ARG LAS_SDK_URL=https://hyy-rd.tos-cn-beijing.volces.com/skills/sdk/las_sdk-0.2.0-py3-none-any.whl
RUN pip install --no-cache-dir -i ${PIP_INDEX_URL} "${LAS_SDK_URL}" || echo "WARNING: las_sdk 安装失败，语音转写不可用，请检查网络后重新构建"

COPY . .

EXPOSE 8000
# 启动前自动建表 / 升级数据库
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
