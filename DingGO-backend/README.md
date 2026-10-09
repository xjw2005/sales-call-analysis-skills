# DingGo 销售助手 · 后端（第一期）

Python + FastAPI + MySQL，给小程序 `DingGO-AI-Saler` 提供真实数据，替代飞书多维表（01 门店主档、02 门店拜访记录）。
第一期只做基础功能：登录、门店、拜访（含无录音拜访）、录音上传、费用确认、待办（含月度目标）、今日待办、进店前简报、旧数据导入。
**AI 录音链路（转写 + 分析）已接入**，见「AI 录音链路」一节，配置密钥后启用；首页对话已接入（`POST /chat`），陪练尚未接入（返回 501）。

## 上线顺序总览

严格按这个顺序，不要跳步（尤其不要在部署验证通过前导入数据）：

| 步骤 | 做什么 | 看哪一节 |
|---|---|---|
| 1 | 部署后端（Docker，拉不下镜像就用不用 Docker 的方式） | 「在阿里云服务器上部署」 |
| 2 | 部署后自测：确认登录、建门店、读写数据库都通；自测数据清掉 | 「部署后自测」 |
| 3 | **导入试运行**（不写库），把报告发给项目负责人，等确认 | 「导入飞书旧数据」第 2 步 |
| 4 | 确认后**正式导入**（加 `--commit`） | 「导入飞书旧数据」第 3 步 |
| 5 | 配置小程序密钥：把 `WX_SECRET` 填进服务器 `.env`，并把 `DEV_LOGIN` 改为 `false`，重启（AppSecret 由项目负责人直接交给运维，不要发到聊天里） | 「登录说明」 |
| 6 | 3 位旧人员各用小程序登录一次，管理员把新账号合并到旧账号 | 「导入飞书旧数据」→「人员」 |
| 7 | 小程序切换到真实后端（`useMock` 改为 `false`，`baseUrl` 填服务器地址） | 「小程序连接后端」 |

- 第 3、4 步导入数据**不依赖登录**，可以在拿到 AppSecret 之前先做。
- 第 6 步**必须在第 5 步之后**：开发登录下每次登录都会产生新账号，这时合并账号没有意义（见「登录说明」）。

## 给运维 / AI 助手的约定

负责部署的人（包括 AI 助手）请遵守：

1. **不要修改代码**。部署只需要改 `.env`；遇到报错，把报错原文发给项目负责人，不要自己改代码绕过去。
2. **密码、密钥、Excel 都不能进 Git，也不要贴到聊天里**：`.env`（数据库密码、`JWT_SECRET`、`ADMIN_TOKEN`）自己生成、自己保存；Excel 只放在服务器上。`.env` 已在 `.gitignore` 里，不要 `git add -f`。
3. **`WX_SECRET`（小程序密钥）项目负责人以后单独提供**：在拿到之前保持 `DEV_LOGIN=true`，不要自己去找或猜；**拿到之前不要让真实用户登录，也不要合并账号**。拿到后填进 `.env`，同时把 `DEV_LOGIN` 改成 `false`（否则任何人都能免验证登录）。
4. **导入必须先试运行**（不加 `--commit`），把完整报告发给项目负责人，**得到确认后**才能加 `--commit`。
5. **导入之前先清掉自测数据**（见「部署后自测」末尾），否则自测门店会混进正式数据。
6. 每完成一步，把结果（命令输出，去掉密码后）告诉项目负责人。

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

### 部署后自测

`/health` 只说明程序起来了。下面几条命令再确认：登录、写数据库、读数据库、权限都真的通。
在**服务器上**执行（`BASE` 换成实际地址；需要 `.env` 里 `DEV_LOGIN=true`，登录用的是开发登录）：

```bash
BASE=http://127.0.0.1:8000

# 1. 健康检查  → {"ok":true}
curl -s $BASE/health; echo

# 2. 开发登录，拿登录凭证（应输出一串 100+ 个字符的长度）
TOKEN=$(curl -s -X POST $BASE/auth/wx-login -H 'Content-Type: application/json' \
  -d '{"code":"smoke-test","name":"自测"}' | sed -E 's/.*"token":"([^"]+)".*/\1/')
echo "token 长度: ${#TOKEN}"

# 3. 新建一家自测门店（写数据库）  → 返回 {"id":"1",...,"name":"自测-可删除",...}
R=$(curl -s -X POST $BASE/stores -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"name":"自测-可删除"}')
echo "$R" | cut -c1-120
STORE_ID=$(echo "$R" | sed -E 's/^\{"id":"([0-9]+)".*/\1/')

# 4. 读回门店列表（读数据库）  → 列表里能看到「自测-可删除」，中文不能是问号
curl -s $BASE/stores -H "Authorization: Bearer $TOKEN" | cut -c1-100

# 5. 登记一条无录音拜访  → 返回里 "status":"no_recording"
curl -s -X POST $BASE/visits -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d "{\"storeId\":\"$STORE_ID\",\"stage\":\"日常维护\",\"recordingMode\":\"none\",\"noRecordingReason\":\"自测\"}" | cut -c1-140

# 6. 今日待办  → {"count":0,"hint":"今天暂无到期的跟进事项",...}
curl -s "$BASE/assistant/today" -H "Authorization: Bearer $TOKEN" | cut -c1-90

# 7. 不带凭证必须被拒绝  → {"message":"请先登录"}
curl -s $BASE/stores; echo
```

7 条输出都和注释里写的一致，才算部署成功。任何一条不一致：看日志（`sudo docker compose logs api` 或 `sudo journalctl -u dinggo-api`），把报错原文发给项目负责人。

**自测完必须清掉自测数据**（导入正式数据之前）：
```bash
# Docker 部署：
sudo docker compose exec -T db sh -c 'mysql -udinggo -p"$MYSQL_PASSWORD" dinggo' <<'SQL'
DELETE FROM visits WHERE store_id IN (SELECT id FROM stores WHERE name = '自测-可删除');
DELETE FROM stores WHERE name = '自测-可删除';
DELETE FROM users WHERE openid = 'dev-smoke-test';
SQL
# 不用 Docker：把上面三条 DELETE 语句放进  mysql -udinggo -p dinggo  里执行
```

### AI 录音链路（转写 + 分析）

销售确认识别费用后，后台线程自动处理：**火山 LAS 转写 → 多段合并 → 角色标注（销售/客户/旁人）→ 有效性判断 → 分析模块（首访 7 个 / 日常 4 个）→ 写入分析结果、门店档案、待办**。逻辑移植自 `sales-call-analysis-scripted` 和 `lark-sales-audio-asr-scripted`，证据校验规则与原流水线一致；提示词在 `app/ai/prompts/`，知识库在 `knowledge/a2/`（更新后重新部署即可）。

- 启用条件：`.env` 里同时填了 `LAS_API_KEY`、`LLM_API_URL`、`LLM_API_KEY`（见 `.env.example`）；没填时「确认识别」会提示服务还没配置，其他功能不受影响。密钥只写在服务器 `.env`，不要提交、不要发到聊天里。
- 火山 LAS 命令行 `lasutil` 在 Docker 镜像构建时安装（`LAS_SDK_URL`）；构建日志出现 `WARNING: las_sdk 安装失败` 说明网络不通，转写会提示「找不到 lasutil」。
- 进度存在 `visit_pipeline` 表（新增迁移 `0002`，部署时自动升级）：进程重启后自动从断点继续；**已提交的转写任务不会重复提交**（防重复计费）。
- 失败处理：拜访详情页显示失败的步骤和原因，销售点「重新处理」从失败的那一步继续。若失败发生在「提交转写」且无法确认火山那边是否已建任务，禁止自动重试；管理员先到火山 LAS 控制台核对，确认没有任务后 `POST /admin/visits/{id}/retry?force=true`。
- 校验不通过的模块不会写入（状态为 `partial_manual`，其他模块照常显示）；全部模块都不通过则标为失败。
- 录音被判「过短/内容无效」后不再分析；销售可点「重新判定」，直接用已有转写进入分析（不再花转写费）。
- 成本：转写按录音时长计费；每条录音的分析约 4–7 次模型调用（`REVIEW_MODE=flagged` 时有风险的模块会再复核 1–2 次）。`DAILY_VISIT_LIMIT` 限制每人每天确认识别的条数。
- 联调建议：先用一段 1–2 分钟的短录音走一遍（确认识别 → 看转写、角色、门店页圆环），再让同事使用。

## 今日计划（先决定去哪几家店，再到店录音）

销售一般先决定今天去哪几家店（常按片区集中跑），到店后录音。首页每次**新建对话**时，助手先检查今天有没有真正的待办：
- 有今日计划：报计划和进度（已去几家），可直接「到店录音」；
- 没有计划但有到期的约定（例如录音里说「过两天带空罐来」）：先提醒，问要不要安排进今天；
- 都没有：问「今天准备去哪个区的门店？」，给出「我常跑的区」「最近拜访过的店」「我承诺过的事」等选项。
点选后给出候选门店（带理由：约定到期、N 天没去、从未拜访……），勾选后「确定今日计划」写入 `visit_plans`；创建拜访时自动把对应计划标为已去。

- 「今日待办」= 今日计划 + 到期的约定 + 待确认费用的录音；简报、久未拜访等不再放进待办。
- AI 分析出的「已确认后续事项」（销售、客户、双方的）也会生成待办，按时限文字解析到期日。
- 接口：`GET /plans/greeting`、`GET /plans/suggest?kind=district|recent|commitments&district=`、`GET /plans/today`、`POST /plans`、`DELETE /plans/{id}`。
- 按片区依赖门店的区县：门店表里区县多数为空，用 `scripts/backfill_district.py` 补（先总门店清单，再从地址解析；默认试运行，`--commit` 写库；补不了的会列出名称）。
  Docker 里：`docker compose exec api python scripts/backfill_district.py`，看结果没问题再加 `--commit`。
- 迁移 `0003_visit_plans` 在容器启动时自动执行。
- **销量缺口**：候选里的「销量低于平时的店」（`kind=gap`，也会在按区选店时排前面并带理由）。销量取总门店清单里的按月销量 `store_directory.monthly_sales`，只对能关联到清单的门店有效。规则：「本期」取销量数据里最新、且至少 20% 门店已有数据的月份（不晚于当月，数据没更新到当月时拿上个月对比，不会把所有店当成 0 销量）；「平时水平」取本期之前最多 3 个月的平均；平时水平不足 2 的店不提醒；本期低于平时水平 70% 算偏低，按差额从大到小。阈值在 `app/services/plans.py` 顶部。销量数据需要定期重新导入才会更新。
- 之后的阶段：月度目标合计（月中冲刺）、大模型对话选店、地图路线（依赖门店坐标）。

## 首页对话（AI 问答、选店、排计划）

`POST /chat/stream`（小程序用，逐行 JSON 流式返回）和 `POST /chat`（一次返回）。大模型每轮输出一个 JSON 动作，最多 4 轮工具调用，工具结果来自数据库：

| 工具 | 作用 |
|---|---|
| `list_regions` | 省/市/区县及门店数 |
| `search_stores` | 按省市区、关键词、合作状态、拜访情况（从未去/30 天没去/近期去过）、排序（综合优先级/销量偏低/最久没去/最近去过）找门店 |
| `add_to_plan` / `remove_from_plan` | 对话里把门店加入、移出今日计划（可说「前三家」「第二家」「XX 不去了」） |

- **对话保存在服务端**（`chat_sessions` / `chat_messages`）：前端只传会话号 `sessionId`（不传就新开一段），历史、「刚才给销售看的门店」都由服务端按会话取，不信任前端回传；会话只有本人能读、能写、能删（别人的会话号一律 404）。接口：`GET /chat/sessions`、`GET /chat/sessions/{id}`、`DELETE /chat/sessions/{id}`。每次还会带上今天的计划；提到店名就带上那家店的资料。
- **限流排队**（单进程内存实现，**服务不要多开 worker**）：每人同一时间只能有一个提问在进行；每人每分钟 `CHAT_PER_MINUTE`（默认 10）、每天 `DAILY_CHAT_LIMIT`（默认 200，按 `chat_logs` 统计，重启不清零）；全局同时向大模型提问的人数 `CHAT_MAX_CONCURRENT`（默认 6），超出的排队（流式时提示「你排在第 N 位」），最多等 `CHAT_QUEUE_WAIT_SECONDS`（默认 40 秒），排队人数超过 `CHAT_MAX_QUEUE`（默认 20）直接提示稍后再试。大模型返回 429（被限速）时提示「AI 现在请求比较多」。
- **流式**：事件 `status`（正在查门店…）→ `delta`（回答文字的一小段）→ `final`（完整结果，含候选门店 `cands`、更新后的计划 `plan`、`logId`）；出错是 `error` 事件。模型输出的是 JSON，服务端边收边把 `reply` 字段的文字取出来推送。
- **记录与反馈**：每次提问写一条 `chat_logs`（耗时、首字耗时、模型调用轮数、token、用到的工具、出错信息）；销售点「有用/没用」调用 `POST /chat/feedback`。优化提示词、估算成本都看这张表，例如：
  `SELECT DATE(created_at), COUNT(*), AVG(duration_ms), AVG(first_token_ms), SUM(tokens) FROM chat_logs GROUP BY 1;`
  `SELECT question, reply, feedback_note FROM chat_logs WHERE rating = -1 ORDER BY id DESC LIMIT 50;`
- 小程序 `transport: 'cloud'`（云托管）不支持分片，会退回一次性返回。迁移 `0004_chat_logs`、`0005_chat_sessions`、`0006_user_memories` 容器启动时自动执行。

## 助手记忆（学自 eigent 的轻量记忆设计）

助手只记「这个人怎么说话、怎么用」：地区别名（「城东」= 官渡区 + 呈贡区）、常跑区域、纠正过的做法；**不记**销量、计划、拜访这些会变的业务事实（永远实时查库）。表 `user_memories`（迁移 `0006`）。

- **模型只能提议，销售确认才生效**：模型调用 `propose_memory` 得到 `pending`（待确认），小程序对话里出现「记住 / 不用」条，销售点了才变 `active`；只有销售在这句话里明确说了（「以后城东就指官渡和呈贡」，且「城东」确实出现在原话里）才直接生效——模型谎称“用户说了”也不算。
- **只带相关的**：每次提问只取别名出现在这句话里的记忆（偏好类量很少，总带），总量不超过 400 字；待确认的不会被当事实使用。
- **每人最多 30 条**，待确认的提议 14 天没人理自动清掉。每人只能读写自己的；经理可把自己的别名/偏好设为「团队通用」（`POST /memories/{id}/team`），其下属提问时也会用到（标注「团队通用」）；纠正类只对自己生效。
- 管理接口：`GET /memories`、`POST /memories`（手动添加，直接生效）、`POST /memories/{id}/confirm`、`PATCH /memories/{id}`、`DELETE /memories/{id}`；小程序「我的 → 助手记住了什么」使用。
- 别名指向多个地区时，`search_stores` 的省/市/区县参数可写「官渡区|呈贡区」（竖线表示任意一个）。

**会话层**：每轮保存上一轮的查询条件和结果数（`chat_sessions.state`）；对话超过 14 条文字后，早先的内容压成不超过 500 字的摘要（`summary`，后台线程异步做，失败不影响对话），之后提问带「摘要 + 最近 6 条原文」。顶部选中的门店只有当前这句话提到它（店名，或「这家 / 它」）才展开资料，否则只作背景。

## 备份

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
- **人员**：Excel 里的销售/区域经理会建成没有微信身份的用户（角色默认经理，可用 `--default-role sales` 改）。**前提：已配置 `WX_SECRET` 且 `DEV_LOGIN=false`**（见「登录说明」）。之后他们每人用小程序登录一次（会产生新账号），再由管理员合并到旧账号：
  ```bash
  curl http://服务器IP:8000/admin/users -H "X-Admin-Token: 你的令牌"          # 看谁是 bound=false（旧人员）、谁是新登录的
  curl -X POST http://服务器IP:8000/admin/users/merge -H "X-Admin-Token: 你的令牌" -H "Content-Type: application/json" \
       -d '{"fromId": "新登录账号的id", "intoId": "旧人员的id"}'
  ```
  合并后再登录，进的就是旧人员账号，能看到自己的门店和拜访。新账号已经有数据时不能合并。
- **分析结果**是飞书里排好版的文字，按原文保存，小程序里「历史记录」标签按文本展示；等 AI 接入后，用已导入的转写重新分析，就能得到结构化结果。
- **录音文件**：Excel 里只有文件名，没有音频本身。这些拜访的录音标记为「文件未迁移」，暂时不能播放和识别，需要另外把飞书里的音频下载后放到服务器。
- **拜访阶段**旧叫法自动转换：陌拜破冰 → 首访破冰，日常拜访 → 日常维护。

## 整改遗留数据

导入报告里的重复门店、没对上门店的待办，用 `scripts/fix_legacy_data.py` 按映射文件整改（默认试运行，`--commit` 才写库；整个文件一个事务，可重复运行）。

```json
{
  "merge_stores":    [{"keep": "ST-1005", "drop": "ST-1006"}],
  "set_external_id": [{"code": "ST-1005", "platform": "智生活", "externalId": "7378"}],
  "link_todo":       [{"topic": "登康：9月任务拆解", "storeCode": "ST-0123"}],
  "cancel_todo":     [{"topic": "合计"}]
}
```

- `merge_stores`：把 drop 门店的拜访、待办、档案、纠正记录并入 keep 后删除 drop；档案维度两边都有内容时保留 keep 的。
- `set_external_id`：把平台门店 ID 写回（占用它的门店必须先合并或改掉，否则报错）。
- `link_todo`：`topic` 是待办里显示的「原门店名：事项」，挂到指定门店后去掉名字前缀，执行人取门店负责人。
- `cancel_todo`：汇总行这类不是真待办的导入待办，标记取消（不删除）。
- Docker 里运行：把映射文件复制进容器后 `docker compose exec api python scripts/fix_legacy_data.py --plan /tmp/fix.json`，看结果没问题再加 `--commit`。
- 整改后不要再重新导入 Excel 的门店和待办部分，否则被合并的门店会按门店编号重新建出来。

## 登录说明

- `.env` 里 `WX_SECRET` 为空、`DEV_LOGIN=true` 时是**开发登录**：不校验微信身份，方便调试。
- 开发登录把小程序 `wx.login` 给的一次性 code 直接当作账号名，而这个 code **每次登录都不同**：同一个人重新登录、或者清了小程序缓存，就会变成一个新账号。所以它**只用于自测和联调，不能给真实用户用**，也**不要在开发登录模式下合并账号**（绑定的是没用的临时身份，换成真实登录后要全部重来）。
- 拿到 AppSecret 后填入 `WX_SECRET`，并把 `DEV_LOGIN` 改为 `false`，重启：`sudo docker compose up -d`。之后每个人用微信登录得到的是固定的身份。

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
| `POST /chat` `{question, storeId, history}` | 首页对话：大模型通过工具查真实数据——`list_regions`（省/市/区县及门店数）、`search_stores`（按省市区、关键词、合作状态、拜访情况、排序找门店），最多 4 轮，参数全部校验；结果以候选门店卡片（`cands`）返回；或依据门店资料和知识库回答问题，没有的数据如实说没有。每人每天上限 `DAILY_CHAT_LIMIT`（默认 200） |
| `/practice/*` | 陪练，暂返回 501 |
| `POST /visits/:id/retry`、`POST /visits/:id/rejudge` | 处理失败后重新处理；把无效录音人工判为有效并分析 |
| `POST /admin/visits/:id/retry?force=` | 管理员强制重试（无法确认是否已提交转写时用） |

## 本地开发与测试

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest                                   # 自动测试（临时 SQLite，不碰正式数据）
TEST_DATABASE_URL='mysql+pymysql://用户:密码@127.0.0.1:3306/测试库?charset=utf8mb4' pytest   # 想在 MySQL 上跑：用专门的测试库（会清空里面的表）
alembic upgrade head && uvicorn app.main:app --reload   # 默认用 ./data/dev.db
```
改了 `app/models.py` 后生成迁移：`alembic revision --autogenerate -m "说明"`。
