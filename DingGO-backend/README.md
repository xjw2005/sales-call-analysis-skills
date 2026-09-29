# DingGo 销售助手 · 后端（第一期）

Python + FastAPI + MySQL，给小程序 `DingGO-AI-Saler` 提供真实数据，替代飞书多维表（01 门店主档、02 门店拜访记录）。
第一期只做基础功能：登录、门店、拜访（含无录音拜访）、录音上传、费用确认、待办（含月度目标）、今日待办、进店前简报、旧数据导入。
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
4. 更新代码：`git pull && sudo docker compose up -d --build`（启动时自动升级数据库）；看日志：`sudo docker compose logs -f api`。

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

## 数据库表

| 表 | 内容 |
|---|---|
| `users` | 人员：销售 / 经理、所属经理、区域；旧数据导入的人还没有微信身份（`openid` 为空） |
| `stores` | 门店（01 门店主档）：自己的编号；旧编号 `ST-xxxx`；平台 + 门店 ID（只在都有值时唯一）；4 种合作状态；联系人电话等 |
| `store_directory` | 总门店清单（智生活 + 西港，8862 家）：平台侧名录和每月销量，门店按「平台 + 门店 ID」匹配 |
| `store_profile_sections` | 门店档案七维度，每个维度一行（内容、状态、原文证据、来源拜访） |
| `store_corrections` | DSR 手填的门店事实：分析门店档案时作为权威输入 |
| `visits` | 拜访（02 门店拜访记录）：实际拜访人、当时的经理、进店/离店时间、目的、门店状况、无录音原因、调研信息…；不一定有录音 |
| `visit_segments` | 录音分段（旧数据只有文件名，`file_missing` 为真） |
| `visit_transcripts` | 转写：逐句结构 + 原文 |
| `visit_analysis` | 每个分析模块一行：原始结果、纠正后的结果、模型和提示词版本 |
| `correction_events` | 纠正记录：谁把哪个分析结果或档案维度从什么改成了什么 |
| `todos` | 后续行动：AI 建议 / 主管指派 / 自建 / 导入；含月度目标（目标、达成，差额算出来不存） |

## 权限规则

- 每个人（销售或经理）能看到：自己名下的门店、自己拜访过的门店，以及**下属**的这些（下属关系由用户表的「所属经理」决定，最多 3 层）。
- 拜访：自己拜访的，或者自己名下门店的（别人去过我的店，我能看到）。
- 只有拜访人本人或其上级能上传录音、确认费用、修改分析结果；只有门店负责人或其上级能改门店资料和档案。
- 待办：默认只看自己的；`GET /todos?scope=team` 看包含下属的。经理可以把待办指派给下属。
- 联系人和电话只在门店详情里返回，列表不返回。
- 管理员操作（改人员、合并账号、导入分析）用请求头 `X-Admin-Token`（`.env` 里的 `ADMIN_TOKEN`）；也可以在 `/docs` 页面里试。

## 导入飞书旧数据

飞书多维表导出的 Excel（含 01 门店主档、02 门店拜访记录、03 后续行动、总门店清单）可以一次导入。
Excel 里有真实电话和客户对话，**不要提交到 Git**，只放在服务器上。

1. 把 Excel 传到服务器（例如 `scp 导出.xlsx ubuntu@服务器IP:~/`）。
2. **试运行**（不写库，只出报告）：
   ```bash
   # Docker 部署：先把文件放进容器
   sudo docker compose cp ~/导出.xlsx api:/tmp/feishu.xlsx
   sudo docker compose exec api python scripts/import_feishu_xlsx.py --file /tmp/feishu.xlsx
   # 不用 Docker：
   .venv/bin/python scripts/import_feishu_xlsx.py --file ~/导出.xlsx
   ```
   报告里看这几项：各表条数；`external_id_conflicts`（平台 + 门店 ID 重复的店，后出现的会清空 ID，需要你到飞书里确认哪个对）；`store_unmatched` / `store_ambiguous`（后续行动里对不上或重名的门店）；`with_recording_but_no_transcript`（有录音但没有转写的拜访）；「时区自查」（默认按 UTC 解释 Excel 里的时间，比例正常就不用管）。
3. 报告没问题，**加 `--commit` 真正导入**，命令同上。可以重复运行：按 `ST-xxxx`、`RA-xxxx` 编号更新，不会重复插入。
4. 用完把 Excel 从容器里删掉：`sudo docker compose exec api rm /tmp/feishu.xlsx`。

导入后需要知道的：
- **人员**：Excel 里的销售/区域经理会建成没有微信身份的用户（角色默认经理，可用 `--default-role sales` 改）。他们每人用小程序登录一次（会产生新账号），再由管理员合并到旧账号：
  ```bash
  curl http://服务器IP:8000/admin/users -H "X-Admin-Token: 你的令牌"          # 看谁是 bound=false（旧人员）、谁是新登录的
  curl -X POST http://服务器IP:8000/admin/users/merge -H "X-Admin-Token: 你的令牌" -H "Content-Type: application/json" \
       -d '{"fromId": "新登录账号的id", "intoId": "旧人员的id"}'
  ```
  合并后再登录，进的就是旧人员账号，能看到自己的门店和拜访。新账号已经有数据时不能合并。
- **分析结果**是飞书里排好版的文字，按原文保存，小程序里「历史记录」标签按文本展示；等 AI 接入后，用已导入的转写重新分析，就能得到结构化结果。
- **录音文件**：Excel 里只有文件名，没有音频本身。这些拜访的录音标记为「文件未迁移」，暂时不能播放和识别，需要另外把飞书里的音频下载后放到服务器。
- **拜访阶段**旧叫法自动转换：陌拜破冰 → 首访破冰，日常拜访 → 日常维护。

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
| `GET/POST /stores`、`GET/PATCH /stores/:id` | 门店列表（摘要）/ 新建 / 详情（含档案、仪表、拜访、联系人）/ 修改（含合作状态） |
| `POST /stores/:id/corrections` | DSR 补充门店事实（AI 分析档案时优先采用） |
| `PATCH /stores/:id/profile/:key` | 直接修改档案某个维度，并留下纠正记录 |
| `POST /visits` | 新建拜访；`recordingMode: "none"` + `noRecordingReason` 是登记无录音拜访；可带进店时间、目的、门店状况、打卡、调研 |
| `POST /visits/:id/segments`（表单字段 `file`、`index`） | 逐段上传录音（mp3/m4a/aac/wav/amr/ogg/opus/flac，单段 ≤ 50MB） |
| `POST /visits/:id/segments/complete` | 传完：算时长与预估费用 → 待确认费用 |
| `POST /visits/:id/confirm-cost` | 确认费用 → 语音识别中（AI 未接入）；录音文件未迁移的历史拜访会被拒绝 |
| `GET /visits`、`GET /visits/:id`、`PATCH /visits/:id` | 列表 / 详情（含录音链接、转写、分析）/ 补充速记等 |
| `PATCH /visits/:id/analysis/:module` | 销售修改某个分析模块：原始结果保留，改后的另存，并留纠正记录 |
| `GET/POST /todos`、`PATCH /todos/:id`、`POST /todos/:id/done`、`/undo` | 待办：新建（可指派下属）、更新目标/达成/进展、完成、撤销 |
| `GET /assistant/today?storeId=`、`GET /assistant/brief/:storeId` | 今日待办大卡片、进店前简报（规则计算） |
| `GET/POST /admin/users`、`PATCH /admin/users/:id`、`POST /admin/users/merge`、`POST /admin/visits/:id/analysis` | 管理：人员、合并账号、写入分析结果（需 `X-Admin-Token`） |
| `POST /chat`、`/practice/*`、`POST /visits/:id/rejudge` | AI 功能，暂返回 501 |

## 本地开发与测试

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest                                   # 自动测试（临时 SQLite，不碰正式数据）
TEST_DATABASE_URL='mysql+pymysql://用户:密码@127.0.0.1:3306/测试库?charset=utf8mb4' pytest   # 想在 MySQL 上跑：用专门的测试库（会清空里面的表）
alembic upgrade head && uvicorn app.main:app --reload   # 默认用 ./data/dev.db
```
改了 `app/models.py` 后生成迁移：`alembic revision --autogenerate -m "说明"`。
