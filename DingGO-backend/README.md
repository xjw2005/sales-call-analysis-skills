# DingGo 销售助手 · 后端（第一期）

Python + FastAPI + MySQL，给小程序 `DingGO-AI-Saler` 提供真实数据，替代飞书多维表（01 门店主档、02 门店拜访记录）。
第一期只做基础功能：登录、门店、拜访录音上传、费用确认、待办、今日待办、进店前简报。
**AI 功能（语音识别、销售分析、问答、陪练）尚未接入**：确认费用后拜访停在「语音识别中」；分析结果可先用导入工具写入。

## 在阿里云服务器（Ubuntu）上部署

**服务器要求**：Ubuntu 22.04 或更新（自带 Python 3.10+）；内存至少 2GB（MySQL 8 占用较多，1GB 容易被系统杀掉）；硬盘按录音量预留（约每小时录音 20MB）。

有两种部署方式：**A. Docker（推荐，一条命令启动）**；如果服务器拉不下 Docker 镜像（国内访问 Docker Hub 经常失败），用 **B. 不用 Docker**。两种方式的 `.env` 配置相同。

### 第 0 步：拿代码（两种方式都要）

仓库是私有的，`git clone` 会要求登录：用户名 `xjw2005`，密码处填 GitHub **Personal Access Token**（github.com → Settings → Developer settings → Personal access tokens → classic，勾选 `repo`），不是登录密码。
```bash
sudo apt update && sudo apt install -y git
git clone -b claude/awesome-pasteur-2fhgln https://github.com/xjw2005/sales-call-analysis-skills.git
cd sales-call-analysis-skills/DingGO-backend
```
服务器连不上 GitHub 时：在自己电脑上把 `DingGO-backend` 文件夹打成 zip，用 `scp` 或阿里云控制台「远程连接 → 文件上传」传到服务器再解压。

### 第 1 步：填配置（两种方式都要）

```bash
cp .env.example .env
nano .env        # 改完按 Ctrl+O 回车保存，Ctrl+X 退出
```
- 把所有「请改成…」换掉。**密码只用英文字母和数字**（不要用 `@ : / # ? %` 等符号，会让数据库地址解析出错）。
- `MYSQL_PASSWORD` 必须和 `DATABASE_URL` 里 `dinggo:` 后面、`@` 前面的密码**完全一样**。
- `JWT_SECRET`、`ADMIN_TOKEN` 可以用 `openssl rand -hex 32` 生成。
- `PUBLIC_BASE_URL` 填 `http://服务器公网IP:8000`。

### A. Docker 部署

1. **装 Docker**（任选其一；阿里云 Ubuntu 默认用阿里云 apt 源，第二种更稳）：
   ```bash
   curl -fsSL https://get.docker.com | sudo bash -s docker --mirror Aliyun
   # 或：sudo apt install -y docker.io docker-compose-v2
   sudo systemctl enable --now docker
   ```
2. **配置镜像加速**：阿里云控制台「容器镜像服务 ACR → 镜像工具 → 镜像加速器」复制专属地址，按页面说明写入 `/etc/docker/daemon.json`，然后 `sudo systemctl restart docker`。
3. **启动**：`sudo docker compose up -d --build`（第一次要下载 `mysql:8.0`、`python:3.11-slim` 两个镜像并自动建表，几分钟）。
   若报 `pull access denied`、`timeout`、`TLS handshake` 之类拉镜像失败的错误，说明服务器拉不到 Docker Hub，改用下面的 **B**。
4. 更新代码：`git pull && sudo docker compose up -d --build`；看日志：`sudo docker compose logs -f api`。

### B. 不用 Docker 部署

```bash
# 1. 装 MySQL 和 Python
sudo apt install -y mysql-server python3-venv python3-pip
# 2. 建数据库和账号（把 你的密码 换成 .env 里 MYSQL_PASSWORD 的值）
sudo mysql -e "CREATE DATABASE dinggo CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'dinggo'@'localhost' IDENTIFIED BY '你的密码';
GRANT ALL PRIVILEGES ON dinggo.* TO 'dinggo'@'localhost'; FLUSH PRIVILEGES;"
# 3. .env 里的数据库地址改成本机：把 DATABASE_URL 中的 @db:3306 改成 @localhost:3306
sed -i 's/@db:3306/@localhost:3306/' .env
# 4. 装依赖、建表
python3 -m venv .venv
.venv/bin/pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
.venv/bin/alembic upgrade head
# 5. 设为系统服务（开机自启、崩溃自动重启）
sed "s#__DIR__#$(pwd)#g; s#__USER__#$(whoami)#g" deploy/dinggo-api.service | sudo tee /etc/systemd/system/dinggo-api.service
sudo systemctl daemon-reload && sudo systemctl enable --now dinggo-api
```
更新代码：`git pull && .venv/bin/pip install -r requirements.txt && .venv/bin/alembic upgrade head && sudo systemctl restart dinggo-api`；看日志：`sudo journalctl -u dinggo-api -f`。

### 第 2 步：放行端口并检查（两种方式都要）

1. 阿里云控制台 → 该服务器的「安全组」→ 入方向添加 **TCP 8000**，来源 `0.0.0.0/0`。
2. 如果系统防火墙开着（`sudo ufw status` 显示 active），再执行 `sudo ufw allow 8000/tcp`。
3. 浏览器打开 `http://服务器公网IP:8000/health`，显示 `{"ok":true}` 即成功；`/docs` 是全部接口说明页。

### 备份

- 数据库：`sudo docker compose exec db sh -c 'mysqldump -udinggo -p"$MYSQL_PASSWORD" dinggo' > backup.sql`（方式 B：`mysqldump -udinggo -p dinggo > backup.sql`）。
- 录音文件：`data/uploads/` 目录（方式 A、B 相同）。
- 建议在阿里云控制台给服务器磁盘开「自动快照」。

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
