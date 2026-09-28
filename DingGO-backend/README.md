# DingGo 销售助手 · 后端（第一期）

Python + FastAPI + MySQL，给小程序 `DingGO-AI-Saler` 提供真实数据，替代飞书多维表（01 门店主档、02 门店拜访记录）。
第一期只做基础功能：登录、门店、拜访录音上传、费用确认、待办、今日待办、进店前简报。
**AI 功能（语音识别、销售分析、问答、陪练）尚未接入**：确认费用后拜访停在「语音识别中」；分析结果可先用导入工具写入。

## 在阿里云服务器（Ubuntu）上部署

1. **装 Docker**（用阿里云镜像源）：
   ```bash
   curl -fsSL https://get.docker.com | sudo bash -s docker --mirror Aliyun
   sudo systemctl enable --now docker
   ```
   国内拉 Docker 镜像可能很慢：在阿里云控制台「容器镜像服务 → 镜像工具 → 镜像加速器」拿到你的专属加速地址，按页面说明写进 `/etc/docker/daemon.json` 后 `sudo systemctl restart docker`。
2. **拿代码**：
   ```bash
   git clone -b claude/awesome-pasteur-2fhgln https://github.com/xjw2005/sales-call-analysis-skills.git
   cd sales-call-analysis-skills/DingGO-backend
   ```
3. **填配置**：`cp .env.example .env`，然后 `nano .env` 把里面所有「请改成…」换掉；`PUBLIC_BASE_URL` 填 `http://服务器公网IP:8000`。
4. **启动**：`sudo docker compose up -d --build`（第一次会下载镜像、自动建表，几分钟）。
5. **放行端口**：阿里云控制台 → 该服务器的「安全组」→ 入方向添加 TCP 8000。
6. **检查**：浏览器打开 `http://服务器公网IP:8000/health` 显示 `{"ok":true}` 即成功；`http://服务器公网IP:8000/docs` 是全部接口的说明页。

更新代码：`git pull && sudo docker compose up -d --build`。看日志：`sudo docker compose logs -f api`。

## 小程序连接后端

`DingGO-AI-Saler/config/index.js`：`useMock` 改为 `false`，`baseUrl` 填 `http://服务器公网IP:8000`。
- 开发者工具：右上角「详情 → 本地设置」勾选「不校验合法域名…」。
- 手机：用「真机调试」，或预览后在右上角菜单里「开发调试」打开。
- 正式上线：需要备案的 HTTPS 域名并在小程序后台登记「服务器域名」（request 和 uploadFile 都要填），或改用微信云托管（`config/index.js` 的 `transport: 'cloud'`）。

## 登录说明

- `.env` 里 `WX_SECRET` 为空、`DEV_LOGIN=true` 时是**开发登录**：不校验微信身份，方便调试。
- 拿到 AppSecret 后填入 `WX_SECRET`，并把 `DEV_LOGIN` 改为 `false`，重启：`sudo docker compose up -d`。

## 导入分析结果（AI 接入前的过渡）

在 `.env` 设置 `ADMIN_TOKEN` 后，两种方式任选：
```bash
# 1) 命令行（result.json 结构同小程序 model/analysis.js）
sudo docker compose exec api python scripts/import_analysis.py --visit-id 12 --file result.json
# 2) 接口
curl -X POST http://服务器IP:8000/admin/visits/12/analysis -H "X-Admin-Token: 你的令牌" \
     -H "Content-Type: application/json" -d @result.json
```
导入后：拜访变为「已完成」，门店档案七维度更新，「下一步行动」自动拆成待办（“周五前”“下周二”等时限自动换算成日期）。

## 接口一览

| 接口 | 说明 |
|---|---|
| `POST /auth/wx-login` | `{code, name}` → `{token, user}`；之后请求头带 `Authorization: Bearer <token>` |
| `GET/POST /stores`、`GET/PATCH /stores/:id`、`POST /stores/:id/corrections` | 门店、档案、仪表、纠正 |
| `POST /visits` → 含 `upload.uploadUrl` | 新建拜访 |
| `POST /visits/:id/segments`（表单字段 `file`、`index`） | 逐段上传录音 |
| `POST /visits/:id/segments/complete` | 传完：算时长与预估费用 → 待确认费用 |
| `POST /visits/:id/confirm-cost` | 确认费用 → 语音识别中（AI 未接入） |
| `GET /visits`、`GET /visits/:id` | 列表 / 详情（详情含录音链接、转写、分析） |
| `GET /todos`、`POST /todos/:id/done`、`POST /todos/:id/undo` | 待办 |
| `GET /assistant/today?storeId=`、`GET /assistant/brief/:storeId` | 今日待办大卡片、进店前简报（规则计算） |
| `POST /chat`、`/practice/*`、`POST /visits/:id/rejudge` | AI 功能，暂返回 501 |

## 本地开发与测试

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest                                   # 自动测试（临时 SQLite，不碰正式数据）
alembic upgrade head && uvicorn app.main:app --reload   # 默认用 ./data/dev.db
```
改了 `app/models.py` 后生成迁移：`alembic revision --autogenerate -m "说明"`。
