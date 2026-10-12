# 交接文档：DingGo（AI 赋能销售云）

> 写给接手的 AI / 开发者。读完这一份，应该能不依赖任何聊天记录继续干活。
> 写于 2026-10-12，对应提交 `3b87381`。**接手后请随着进展更新本文档**（尤其是「当前状态」和「待办」两节）。
> 项目里还有两份更细的 README：`DingGO-backend/README.md`（后端、部署、接口、权限）和 `DingGO-AI-Saler/README.md`（小程序）。本文档讲全局、历史和决策，细节去那两份里查。

---

## 0. 三十秒版

- **做什么**：一个微信小程序 + 后端，给 A2 奶粉销售团队用。销售去母婴店拜访时**录音**，系统自动**转写 → 分析 → 生成门店档案和待办**，首页有一个「阿福式」的 **AI 对话助手**，能查门店、帮忙排今天去哪几家店、练习话术（AI 陪练）。
- **代码在哪**：GitHub 仓库 `https://github.com/xjw2005/sales-call-analysis-skills`，开发分支 `claude/awesome-pasteur-2fhgln`（目前所有工作都在这个分支上，没有合并到 main）。
- **两个项目目录**：`DingGO-backend/`（FastAPI + MySQL）和 `DingGO-AI-Saler/`（微信小程序，纯 JS）。
- **线上**：后端用 Docker 跑在用户自己的阿里云 Ubuntu 服务器上，目前只能用 `http://公网IP:8000` 调试（没有域名 / HTTPS / 备案）。
- **谁干什么**：你（AI）写代码、推送到 GitHub；用户在本机用微信开发者工具拉代码、编译、真机测试；**另一个 AI「Codex」只负责在服务器上部署和执行脚本，不改代码**。
- **最大的未完成项**：「按位置推荐附近的门店」——后端已写完并提交，小程序侧、地址批量解析、门店坐标补全还没做（见第 9 节）。

---

## 1. 仓库与分支

| 项 | 内容 |
|---|---|
| 仓库 | `xjw2005/sales-call-analysis-skills`（GitHub） |
| 分支 | `claude/awesome-pasteur-2fhgln` |
| 最新提交 | `3b87381`（附近的店·后端部分） |
| 服务器上已部署到 | `2c879f7`（`3b87381` **还没部署**，它带数据库迁移 0009） |
| 提交数 | 写本文时 66 个（之后还会增加） |

仓库根目录：

```
DingGO-AI-Saler/            ← 微信小程序（产品前端）
DingGO-backend/             ← 后端（产品后端）
lark-sales-audio-asr-scripted/   ┐
sales-call-analysis-scripted/    │ 产品化之前的「飞书 + 脚本」流水线（Claude Code skill）。
classify-sales-call-roles/       │ 后端的 AI 录音链路就是从这里移植来的；
store-profile-split/             ┘ 现在只作参考和备份，不要在这里加新功能。
HANDOFF.md                  ← 本文档
```

> 仓库名叫 `sales-call-analysis-skills` 是历史原因（最初只是几个分析 skill 的备份）。产品代码是后来加进去的 `DingGO-*` 两个目录。

**`.gitignore` 要点**：`**/config.local.json`（含飞书表标识和本机路径，已从历史里移除，**不要再提交**）、`.env`、各种音频文件。本地工作区里可能还留着几个 `config.local.json`，那是忽略的，别 `git add -f`。

---

## 2. 项目背景

### 2.1 业务
- **公司/场景**：A2 奶粉的销售团队（区域经理 + 业务员），日常跑**母婴店**（重庆、云南、贵州等地，一千多家门店）。
- **销售的痛点**：去店里谈完，要手工写拜访记录、门店档案、下一步行动；记不住老板的临时承诺（「过两天带空罐来」）；不知道今天该去哪几家；缺话术练习。
- **目标**：销售到店后点一下录音，其余交给系统：转写 → 判断是否有效对话 → 分析（需求、关心点、合作进展评分、金句、门店档案、下一步行动、上次行动闭环）→ 自动生成待办和提醒；并且有个会聊天的助手帮他安排今天的拜访。
- 产品名：**DingGo**（对话里助手自称「DingGo」，副标题「AI 赋能销售云」）。

### 2.2 来龙去脉
1. **2026-08**：先有一套「飞书多维表格 + Python 脚本」的流水线（即仓库里那 4 个 skill）：从飞书下载录音 → 火山/腾讯 ASR 转写 → 角色标注（销售/客户/旁人）→ 有效性判断 → 大模型分析 → 写回飞书。
2. **2026-09-28**：做微信小程序（先全是演示数据）和后端第一版（FastAPI + MySQL）。
3. **2026-09-29**：后端按新的表设计重做；把飞书旧数据（门店、拜访、待办、总门店清单）导入；接入 AI 录音链路（把 skill 里的校验规则移植成后端服务）。
4. **2026-09-30 ~ 10-12**：今日计划、销量缺口、对话助手（工具调用、流式、会话、记忆、限流）、绑定码、AI 陪练、大量交互修复、定位、附近的店……

### 2.3 数据现状（导入时的数字，之后会变）
- 1072 家门店、273 条拜访（大多是飞书导入的**历史拜访**，`legacy=true`，分析结果是整段原文而不是结构化数据）、8862 条总门店清单（智生活 + 西港平台的门店名录和每月销量）。
- 4 个用户：3 个是**旧人员账号（还没绑定微信）**，1 个是用户自己的经理账号（已绑定）。
- **所有门店都没有经纬度**（只有文字地址）——这是「附近的店」的最大前提问题。
- 历史拜访的录音文件**还没迁移**到服务器（只有文件名），所以历史录音不能确认识别。

---

## 3. 总体架构与协作方式

```
微信小程序 (DingGO-AI-Saler)
   │  HTTPS/HTTP（目前是 http://公网IP:8000，调试模式）
   ▼
FastAPI (DingGO-backend, 单个 uvicorn 进程)
   ├── MySQL 8（Docker，数据在 ./data/mysql）
   ├── 本地文件存储（录音在 ./data/uploads）
   ├── 大模型：OpenAI 兼容接口（现在只用 DeepSeek，模型名 deepseek-flash）
   └── 语音转写：火山 LAS（lasutil 命令行）
```

**部署形态**：阿里云 Ubuntu + `docker compose`（`db` = mysql:8.0，`api` = 本仓库 Dockerfile 构建）。服务器上项目目录是 `/home/ubuntu/DingGO-backend`，里面有 **`.env`（密钥，不在 Git 里）和 `data/`（数据库和上传文件）——同步代码时必须保留这两样**。`api` 容器启动命令会先 `alembic upgrade head` 再起 `uvicorn`。

**工作流（很重要）**：

1. 你改代码 → 跑测试 → `git push` 到 `claude/awesome-pasteur-2fhgln`。
2. **小程序**：用户在本机 `git pull`，用微信开发者工具重新编译，真机/预览测试。
3. **后端**：用户把一段「给 Codex 的提示词」发给 Codex，Codex 在服务器上拉最新代码、同步到 `/home/ubuntu/DingGO-backend`（保留 `.env`、`data/`）、`sudo docker compose up -d --build api`、做自测、汇报。
4. **每次推送后要明确告诉用户：这次要不要让 Codex 部署**（只改了 `DingGO-AI-Saler/` 就不用；改了 `DingGO-backend/` 就要；带迁移要特别说明）。
5. Codex 的提示词模板见第 12.3 节。**Codex 不改代码**，出错就把完整报错原文贴回来。

---

## 4. 已完成的功能（按模块）

### 4.1 登录、账号、权限
- 微信登录（`WX_SECRET` 在服务器 `.env` 里，只由用户交给 Codex 配置，**不进聊天、不进 Git**）。
- **绑定码**（`app/routers/auth.py`、`admin.py`，表 `bind_codes`）：不认识的微信**不会自动建账号**，而是返回 `needBind` 让小程序跳到「绑定账号」页，输入管理员发的 8 位一次性码。码 30 分钟有效、用一次作废、库里只存哈希、同一微信错 5 次锁 10 分钟、绑定令牌与登录令牌互不通用。管理员用 `POST /admin/users/{id}/bind-code`（`X-Admin-Token`）生成。`REQUIRE_BIND_CODE` 默认开；开发登录 `DEV_LOGIN` 服务器上必须是 false。
- **账号合并**（`services/accounts.py`）：把新建的微信账号并到旧人员账号；**自动发现所有指向 `users.id` 的外键**一起迁移（对话、记忆、计划、提问记录等），已有业务数据的账号拒绝合并。以后新增任何指向 `users.id` 的表，都会被自动覆盖，不用改合并逻辑。
- **权限模型**（`services/access.py`）：自己 + 下属（`team_ids`）；门店按负责人过滤；经理能看下属的店和拜访；对话、记忆、计划、练习**只有本人**能读写，别人的 id 一律 404。

### 4.2 门店、拜访、录音链路
- 门店：列表（带筛选）、详情（档案七维度、仪表圆环、行动闭环、历史拜访）、新建（地图选点）、纠正档案、设置位置（新）。
- 拜访：新建 → 分段上传录音 → 确认费用 → AI 流水线 → 结果。状态机见 `services/constants.py`（`cost_pending → asr_running → role_classifying → analyzing → reviewing → done` 以及 `invalid_*`/`failed`/`no_recording`/`partial_manual`）。
- **AI 录音链路**（`app/ai/`）：`transcribe.py`（火山 LAS 转写、多段合并、角色标注 `roles/*.py`、有效性判断）→ `core.py`（移植自 skill 的分析核心：提示词 `prompts/`、证据校验、`call_llm`）→ `adapt.py`（把模型结果适配成小程序结构，含证据时间戳）→ `worker.py`（后台线程池 + `visit_pipeline` 表，断点续跑，**失败重试不重复提交 ASR 以免重复计费**，`retry`/`rejudge` 接口）。
- 分两种模式：首访破冰（7 个模块）、日常维护（4 个模块 + 行动闭环）。有效性门槛 120 秒（用户决策）。
- 知识库：`DingGO-backend/knowledge/a2`（用户提供的 Obsidian 目录），分析和对话会按需引用。
- 无录音拜访可直接登记（选原因）；**拜访打卡位置**：录音页开始录音时自动获取手机位置，提交时带给后端（见 4.9）。

### 4.3 今日计划与待办
- **今日计划**（`services/plans.py`，表 `visit_plans`）：销售先选今天去哪几家（按区县、最近拜访过的、到期的约定、销量缺口、附近），勾选后确定；创建拜访时自动把对应计划标为「已去」。
- **新对话主动问候**：有计划报进度；有到期约定先提醒；都没有就问今天去哪个区。
- **销量缺口**：基准月 = 有数据的门店占比 ≥ 20%（至少 3 家）的最近一个月；平时水平 = 前最多 3 个月的平均；本期 < 平时的 70% 且平时 ≥ 2 才算偏低。
- **待办的口径**（三处统一，见 `DingGO-AI-Saler/services/todo.js` 的 `loadTodoCenter()`）：**今日计划要去的店 + 未完成的跟进事项 + 待确认费用的录音**（历史导入的录音不算）。首页「今日待办」卡片只列今天要处理的，「我的待办」页列全部，「我的」页入口的红色数字也是它。
- 待办**可以删除**：计划里的店「移出计划」，跟进事项「删除」（后端是 `PATCH /todos/{id}` 状态设 `cancelled`）。重新分析某条拜访时，**销售已删除或已完成的同一条（话题和内容一样）不会再被重新生成**（`services/ingest.py`）。

### 4.4 首页对话助手（核心体验）
文件：`app/ai/chat.py`（主逻辑）、`app/routers/assistant.py`（接口）、小程序 `pages/home/`。
- **工具调用循环**（最多 4 轮，模型每次只输出一个 JSON）：`list_regions`、`search_stores`（省市区/关键词/合作状态/拜访情况/排序/**按距离**）、`add_to_plan`、`remove_from_plan`、`propose_memory`；最终动作 `show_stores`（候选卡片）或 `answer`。门店名、数字只能来自工具结果。
- **流式输出**：`POST /chat/stream` 返回 NDJSON（`status` / `delta` / `final` / `error`），后端用 `ReplyExtractor` 从模型边生成边输出的 JSON 里把 `reply` 取出来；小程序用 `wx.request` 的 `enableChunked` + 自己写的 UTF-8 行切分器（`utils/utf8.js`）。云托管（`transport: 'cloud'`）不支持分片，会退回一次性返回。
- **会话存服务端**（表 `chat_sessions`、`chat_messages`、`chat_logs`）：前端只传 `sessionId`；历史、「刚给销售看的候选门店」都由服务端取，不信任前端回传。超过 14 条文字消息会把更早的压成摘要（保留最近 6 条原文）。可单条删除、清空（`DELETE /chat/sessions[/id]`；删对话不删 `chat_logs`，那是优化提示词用的耗时/用量/反馈记录）。
- **门店资料上下文**（`store_context`）：提到店名才带那家店的资料——一句话画像、档案各维度、最近 3 次拜访的目的/速记/调研、历史导入的分析原文（摘要、需求、关心点、门店档案、下一步、闭环，每个模块限长）、待办。
- **限流排队**（`app/ai/limits.py`）：每人每分钟 10 次、每天 200 次；同时调用大模型最多 6 人，超过排队（最多 20 人、等 40 秒）；每人同时只能有一个问题在回答。**这些都是进程内存状态 → 后端必须只跑一个 uvicorn 进程**。
- **反馈**：回答后「有用 / 没用」写入 `chat_logs`。
- **记忆**（`app/ai/memory.py`，表 `user_memories`，学自 eigent）：只记「这个人怎么说话、怎么用」（别名如「城东」指哪几个区、常跑区域、纠正），**不记会变的业务事实**；模型只能提议，销售确认才生效（pending→active）；每人最多 30 条，待确认 14 天过期，每条 400 字；只在相关时注入；经理可设为团队通用。小程序「我的 → 助手记住了什么」管理。
- **提示词安全**：销售的话只作为数据放进提示词，不当指令；`call_llm` 固定要求 JSON 输出（`response_format: json_object`），所以**任何新提示词都必须输出 JSON，并且提示词里要出现「JSON」字样**。

### 4.5 AI 陪练（`app/ai/practice.py`，表 `practice_sessions`）
- 大模型扮演母婴店老板，销售逐句回答；6 个场景（首访开场、价格太贵、担心网上乱价、利润太薄、卖不动怎么办、到货太慢），场景定义（老板设定、开场切入角度、评分维度、参考话术）都在 `practice.py`。
- **开场白由模型生成**（随机性格 × 随机切入角度，所以每次不同）；**3~8 轮自适应**：老板谈出结论（最少 3 轮后）自动收尾，销售可随时「结束并点评」；点评按场景维度打 1~5 分并写评语，**模型输出缺维度或格式不对就不落库**。
- **模型失败时明确报错，不再悄悄用写好的台词**（这是个教训：之前的兜底让陪练「看起来正常其实没有 AI」）。
- 接口：`GET /practice/scenarios`、`POST /practice/start|turn|finish`；每人每天最多开始 30 次；练习记录只有本人能访问。
- **已知缺口（重要）**：老板和点评**没有真实的 A2 政策作依据**，参考话术里的数字（如「毛利六七十」）是演示版带过来的，销售编的数字系统分辨不出对错。要做好需要用户提供真实政策（返利、毛利、退换货、物流时效）。

### 4.6 小程序（`DingGO-AI-Saler/`）
- 页面（`app.json`）：`home`（助手首页）、`store/list|detail|edit|pick`、`visit/list|detail`、`record`、`brief`（进店前简报）、`todo`（我的待办）、`objection`（异议应对）、`memory`、`bind`、`me`。底部自定义 tabBar：助手 / 门店 / 拜访 / 我的。
- 三个开关（`config/index.js`）：`useMock`（全部用演示数据）、`mockAI`（首页问答用演示）、`mockPractice`（陪练用演示）；**仓库里的默认值是演示模式 + 占位服务器地址**。用户本地会改成 `useMock:false` 并填真实 `baseUrl`——**不要把用户的真实地址/配置提交进仓库**（拉代码时可能与用户的本地修改冲突，保留用户的）。
- 交互细节（都有测试）：流式显示；切换/新建/删除对话时**作废还在路上的旧请求**（`epoch`/`openSeq`），旧回答不会串进新对话；对话在别处被删后自动另开；对话可删可清空；门店列表**内存缓存 60 秒**（`utils/cache.js`，新建门店/拜访变化后失效，换人清空，刷新失败先用旧数据）；长列表页「回到顶部」按钮；点已选中的标签栏回顶；陪练重复点击/失败恢复；录音上传失败重试**不重复创建拜访**；录音管理器是全局唯一的，监听只注册一次。
- 时间一律按北京时间显示（`utils/format.js`）。
- **演示模式**（`model/*.js`）：本地假数据，陪练固定 4 轮脚本；真机上陪练条会标注「演示数据，非 AI」。

### 4.7 数据导入与整改脚本（`DingGO-backend/scripts/`）
- `import_feishu_xlsx.py`：导入飞书导出的 Excel（门店、拜访、待办、总门店清单）。**不要把真实 Excel 放进 Git**。
- `import_analysis.py`、`fix_legacy_data.py` + `fix_plan_2026-09.json`（合并 6 家重复门店、给 8 条待办挂门店、取消合计行）、`backfill_district.py`（给门店补区县；试运行时的输出是 970 / 57 / 45，对应「从总门店清单补 / 从地址解析补 / 补不了」三项，**我没有确认过是否已经 `--commit` 过，请以服务器上的实际数据为准**）。
- 约定：**脚本默认只试运行，加 `--commit` 才写库**；先看试运行结果再提交。

### 4.8 测试
- 后端：**101 条**（`pytest`，SQLite；`TEST_DATABASE_URL` 可指向专用 MySQL 测试库）。
- 小程序：**100 条**（`node --test "tests/*.test.js"`，用 Node 模拟 `wx`/`Page`，不需要微信开发者工具；`tests/harness.js`）。
- 习惯：修 bug 或加功能后做**「回退验证」**——把修复还原、确认新测试会变红、再恢复，证明测试真的有效。

### 4.9 定位与「附近的店」（进行中，见第 9 节）
- **已完成并提交（`7cd7735`）**：录音页「打卡位置」行（开始录音时自动获取位置，提交带给后端 `checkin`；失败不阻塞；可地图选点/清除；拜访详情显示并可「在地图上看」）；`app.json` 声明 `getLocation`；隐私说明补充。后端校验坐标范围。
- **已完成并提交（`3b87381`，后端，未部署）**：`services/geo.py`（距离、坐标校验）、`plans.nearby()`、`plans.search(near=…)`、`/plans/suggest?kind=nearby&lat=&lng=`、问候里的「离我近的店」选项、`POST /stores/{id}/location`、助手 `search_stores` 的 `near/radius_km/sort=distance` + `needLocation`、迁移 0009（`stores.geo_source`）。**位置只用于本次算距离，不给大模型看、不写入数据库/对话记录/日志**（有测试守着）。

---

## 5. 关键设计决策和原因（别随便推翻）

| 决策 | 原因 |
|---|---|
| 模型只翻译「条件」，门店名和数字全部由后端查库 | 防止模型编造门店/数字 |
| 对话、记忆、计划、练习都按 `user_id` 隔离，别人的 id 一律 404 | 不泄露是否存在；经理能看下属的**业务数据**，但看不到下属的**私人对话** |
| 记忆：模型只提议，销售确认才生效；只记说话习惯，不记业务事实 | 业务事实每次现查；避免错误记忆污染 |
| 单进程部署（不要多 worker） | 限流器和 AI 调度器在进程内存里；多进程会失效/重复调度。要扩容先把它们迁到 Redis/数据库 |
| 位置不给模型看 | 隐私；距离由后端算 |
| 旧账号绑定用一次性绑定码，而不是手机号 | 手机号授权需要小程序**企业主体**认证，用户在确认；绑定码零成本、可立即上线。手机号授权是可选的后续项 |
| 目前只用 DeepSeek（`deepseek-flash`） | 用户决策，暂不做多模型 |
| 不做「月度目标」设计 | 用户明确放弃 |
| 重新分析不重新生成销售已删除/完成的同一条待办 | 否则删掉的会回来 |
| 门店档案是共享数据加权限过滤，不是每人一份 | 门店属于组织，换负责人不能带走档案 |
| 用户不喜欢「备案」，HTTPS/域名方案未定 | 见第 10 节 |

---

## 6. 历史时间线（提交分组）

**阶段 A：飞书脚本流水线（2026-08）** `bf3a758`…`bae371d`
多附件合并转写、有效性自动初判、store-profile 动态更新、日常维护模式、按 Codex 审查修复 18 项、有效性门槛 180→120 秒、部署指南。

**阶段 B：小程序 + 后端第一版（09-28）** `cfbbeb5`…`73b1d6a`
先做小程序演示版（阿福式首页：两个选项「今日待办」「AI 销售陪练」→ 对话内大卡片），再做后端第一期（登录、门店、拜访上传与费用确认、待办、今日待办、简报）。

**阶段 C：重做表结构 + 导入真实数据（09-29）** `6d71a16`…`b15ea12`
后端按新表设计重做，导入飞书旧数据；整改脚本（合并重复门店等）；门店页筛选；时间统一北京时间；行动闭环显示历史原文。

**阶段 D：AI 录音链路（09-29）** `e4b6266`…`1cce892`
把 skill 的校验规则移植成后端服务；移除已提交的 `config.local.json`。

**阶段 E：今日计划与销量缺口（09-30）** `b4d1aa0`…`93e02a7`
可搜索的门店选择页（修了「只显示前 6 家」）；主动问候、按区/最近/约定选店；销量缺口。

**阶段 F：对话助手（10-09）** `e0bf811`…`7b79152`
自然语言选店 → 工具调用 → 上下文与计划工具 → 流式 → 服务端会话与限流排队 → 记忆 → 账号合并修复。踩坑：迁移 0006 在 MySQL 报 1101（TEXT 不能有默认值）、候选门店点「确定今日计划」报「来源不正确」（`SOURCES` 没放行）。

**阶段 G：绑定码（10-09）** `45ae681`、`1842ec2`

**阶段 H：AI 陪练（10-10）** `76929a7`、`1855d04`、`741528d`、`d10beba`
踩坑：陪练兜底台词让「看起来正常其实没 AI」→ 改为失败报错；开场白固定、轮数固定 4 → 改为模型生成、3~8 轮自适应；演示模式无标识 → 加「演示数据，非 AI」标注。

**阶段 I：对话读资料补全（10-10）** `512b0b2`
发现助手只读得到「一句话画像」：历史导入的分析是原文，没被带进上下文 → 补上。

**阶段 J：交互全面检查与修复（10-12）** `ac5bcf1`、`fc5464f`、`0655b5e`、`81e65f6`、`2628efb`、`5a940b0`、`2c879f7`
对话可删除（用户发现的基本操作缺失）；切换对话竞态；陪练重复点击；「我的」清空只清本机；录音上传重试重复建拜访；录音监听堆积；门店列表缓存；回到顶部；待办口径不一致（首页卡片有计划，点进去是空的）；待办可删除。

**阶段 K：定位与附近的店（10-12）** `7cd7735`、`3b87381`（进行中）。

---

## 7. 当前状态（接手时的快照）

- **代码**：分支最新 `3b87381`，工作区干净，后端 101 / 小程序 100 条测试全过。
- **服务器**：已部署到 `2c879f7`，迁移到 `0008`。**`3b87381` 未部署**（含迁移 `0009`，是加一个带默认值的列，安全）。
- **用户的小程序**：用户在本机拉代码测试；服务器地址和 `useMock:false` 在用户本地配置里。
- **据我所知没有开放的 PR**（我从未建过，用户也没要求过）；**不要自作主张建 PR 或合并到 main**。

---

## 8. 本次会话最后的对话（上下文）

用户先要求「让助手知道我的位置并推荐比较近的门店」。我读了代码后指出**核心卡点：1072 家门店都没有坐标**，随后用户问「是不是需要接地图 API？新门店进来能处理吗？」。我的回答（已发给用户，等他决定）：

- 已有的 1072 家：把文字地址转坐标**必须调地图服务**（腾讯位置服务 / 高德 / 百度），需要用户申请 Key，只用一次批量处理；不接 API 的替代办法是销售到店后手动「把当前位置设为门店位置」，坐标慢慢积累。
- 新门店三条路：小程序里地图选点（**不需要 API**，但现在的新建页把坐标丢了，要修）；Excel 导入的只有地址，需要地图 API；第一次拜访的打卡位置可自动补空白坐标（不需要 API，但有销售不在店里录音导致误记的风险）。
- 我的建议：两条路一起做——先做不需要 Key 的部分，Key 到了再加批量解析。
- **等用户回答的两个问题**：①要不要申请腾讯位置服务 Key；②要不要做「第一次拜访自动补门店坐标」。
- 另提醒：用户自己的手机定位依赖 `wx.getLocation`，**个人主体的小程序可能没有这个接口权限**（用户需在公众平台「接口设置」确认，我没查到确切规则）。

---

## 9. 准备怎么做：「附近的店」剩余工作

> 前提：先看用户对第 8 节两个问题的回答。下面按「不需要 Key」→「需要 Key」排列。

### 9.1 小程序侧（不需要 Key，可立即做）
1. **`services/location.js`**：新增 `currentPoint()`（内存里缓存 5 分钟，避免每次提问都重新定位）和「是否已授权」的判断（`wx.getSetting` 的 `scope.userLocation`）。已有 `getLocation()`、`chooseOnMap()`、`classify()`（错误分 denied/system/unsupported/cancel）。
2. **首页提问带位置**：`pages/home/index.js` 的 `askAI`——只在问题像在问位置时才取位置（正则如 `/附近|离我|周边|这附近|最近的店|身边|旁边/`），把 `location: {lat, lng}` 传给 `services/chat.js` 的 `askStream`/`ask`（现在只传 `question/storeId/sessionId`，要加字段）。不相关的问题**不带位置**（隐私最小化）。
3. **处理 `reply.needLocation`**：模型想按位置找店但这次没位置时，后端返回 `needLocation: true`。小程序新增一种消息类型（如 `locate`）：一张卡片「需要你的位置才能找附近的店 [允许获取位置]」，点按钮 → 获取位置 → **用同一个问题重新问一次**（不重复显示用户气泡）；被拒绝则解释并建议「也可以告诉我你在哪个区」。
4. **问候里的「离我近的店」**（`kind: 'nearby'`）：`tapOption` 里获取位置 → `services/plan.js` 的 `suggest` 加 `lat/lng` 查询参数 → 显示候选卡片（理由里已含「距你 xx」）。无权限的降级同上。后端已把 `nearby` 放进计划来源白名单。
5. **门店详情**：显示「门店位置：已记录 / 未记录」，加按钮「把我现在的位置设为门店位置」（弹确认「你现在在这家店里吗？」→ 获取位置 → `POST /stores/{id}/location`；`services/store.js` 新增 `setStoreLocation` 并 `cache.invalidate('stores')`）；已有坐标时可「在地图上看」（`wx.openLocation`）。
6. **新建门店页修复**：`pages/store/edit` 的 `chooseLocation` 现在只取了 `address`，**丢了经纬度**；要保存并随 `createStore` 提交（后端 `StoreIn.lat/lng` 已支持并校验，必须成对且在国内）。
7. 隐私说明（`pages/me`）补一句：推荐附近的店时才获取位置，只用于本次计算。
8. 每一项都补测试（用现有的 `tests/harness.js`；参考 `location.test.js`）。

### 9.2 后端可选项：第一次拜访自动补门店坐标（取决于用户回答）
`routers/visits.py` 创建拜访时：若 `store.lat is None` 且 `checkin` 合法且 `recordingMode == 'uploaded'`，则设置 `store.lat/lng` 并 `geo_source='checkin'`。**优先级：`manual` > `geocode` > `checkin`**，低优先级永远不覆盖高优先级。要补测试。

### 9.3 地址批量解析脚本（需要用户的地图 Key）
新建 `DingGO-backend/scripts/geocode_stores.py`，风格同 `backfill_district.py`（`sys.path` 插入、`argparse`、**默认试运行、`--commit` 才写库**、可重复运行、用可注入的 `fetch` 函数方便测试）：
- 对象：`lat IS NULL` 且 `geo_source != 'manual'` 的门店；地址 = 省 + 市 + 区县 + 地址（门店自己的优先，缺则取总门店清单 `store_directory`）；没有地址的跳过并报告。
- 接口：腾讯位置服务 WebService「地址解析」`GET https://apis.map.qq.com/ws/geocoder/v1/?address=…&region=<城市>&key=…`；返回 `status == 0` 时取 `result.location.{lat,lng}`、`result.reliability`、`result.level`、`result.address_components`。**状态码含义（每秒限流可重试、每日额度用完/Key 或来源 IP 未授权要中止）请以官方文档核对后再写死，我凭记忆写的不一定准**。
- **接受条件作成命令行参数**：可信度 `reliability`、精度级别 `level` 的阈值**只是初始猜测**，必须先试运行看分布再定；另外要求坐标在中国范围内（`geo.in_china`）、解析出的城市与门店城市一致。不合格的不写库，写进报告。
- 控频（默认约 4 次/秒，参数可调）；Key 只从环境变量（如 `TENCENT_MAP_KEY`）读，**不进命令行参数、不打印、不进 Git**；报告 CSV 写到服务器的 `data/`（含地址，不要进 Git）。
- 写库：`lat/lng/geo_source='geocode'`，分批提交。
- 新门店：可以定期跑脚本，或在服务器创建门店时自动解析（需要把 Key 配在服务器 `.env`）。
- 交给 Codex 的做法：Key 只由用户交给 Codex，Codex 在服务器上先试运行 → 把汇总和阈值分布贴回来 → 你定阈值 → 再 `--commit`。

### 9.4 部署
全部做完后，让 Codex 部署（`3b87381` 起含迁移 0009，不需要备份也可，但建议保留习惯）。部署后验证：`/health`、容器 running、日志无 ERROR、迁移版本 `0009`、只有一个 uvicorn 进程。

---

## 10. 其他待办与待决策

| 事项 | 状态 / 说明 |
|---|---|
| **给 3 位同事发绑定码** | 旧人员账号还没绑定微信。用户准备好后，让 Codex 调 `POST /admin/users/{id}/bind-code`，把 8 位码**私下**发给本人，不写进任何文件/日志。 |
| 手机号授权（`getPhoneNumber`） | 需要小程序企业主体认证；用户在确认主体类型。可行的话可作为绑定码之外的自动绑定方式。 |
| HTTPS / 域名 / 备案 | 未定，用户不喜欢备案。目前只有 `http://IP:8000` 调试模式；正式发布需要 HTTPS 合法域名（或走微信云托管 `transport:'cloud'`，但云托管不支持流式分片，会退回一次性返回）。 |
| **陪练缺真实政策** | 见 4.5。需要用户给真实 A2 政策，再把它放进场景、让老板和点评以它为准。 |
| `wx.getLocation` 权限 | 用户要在公众平台：①「用户隐私保护指引」里补充「位置信息」；②「接口设置」确认 `wx.getLocation` 是否需要申请（个人主体可能不支持）。不支持时只能用地图选点。 |
| 录音报 `operateRecorder:fail …` | 用户在**真机调试**模式下遇到过，原因未确认。已修了监听堆积并让错误完整显示。让用户用「预览」而不是「真机调试」测录音，如还报错把完整对话框截图发来。 |
| DeepSeek 并发/限流 | 不知道实际上限；`CHAT_MAX_CONCURRENT` 默认 6，可按观察调。 |
| 销量数据更新频率 | 总门店清单里 `monthly_sales` 哪些月份有数据、多久更新一次，Codex 的查询结果一直没回来；销量缺口逻辑依赖它。 |
| 历史录音迁移 | 飞书历史拜访的音频文件没迁到服务器；迁了之后这些拜访才能重新转写分析。 |
| 退出登录 / 换绑 | 没有入口，绑错账号只能让管理员处理。 |
| 删除错误的拜访记录 | 后端没有接口；待确认的录音在小程序里不能删。涉及权限和已有分析数据，要设计。 |
| 用户拒绝的/不做的 | 「身份行」（把角色/区域/上级注入提示词）——用户说暂时没必要；月度目标；多模型。 |
| 小改进（未做） | 陪练老板台词不是流式；`listStores` 无冷启动缓存（1072 家数据可能超过本地存储单项 1MB）；首页卡片「今日待办」与待办页数字口径差异可加说明。 |

---

## 11. 与用户协作的约定（务必遵守）

**红线**
- **密钥/AppSecret/Key 只交给 Codex 配在服务器，不进聊天、不进 Git。** 我（AI）曾拒绝用户给 SSH 私钥——不要索取服务器登录凭据。
- **不把真实手机号、真实 Excel、真实门店数据提交进 Git。** 同事姓名也不要写进仓库文档。
- **脚本先试运行（dry-run），再 `--commit`。**
- 同步代码到服务器时**保留 `.env` 和 `data/`**。
- **Codex 不改代码**，只部署、跑脚本、汇报；失败时把完整报错原文贴回来。
- 后端**只跑一个 uvicorn 进程**。
- 不要自作主张建 PR、合并或推到别的分支；所有开发推到 `claude/awesome-pasteur-2fhgln`。

**沟通偏好**
- 用**中文**，直接、少废话，常用表格和短清单；每次推送后说清楚：**改了什么、测试结果、要不要让 Codex 部署、用户下一步怎么验证**。
- 给 Codex 的提示词放在代码块里、分步骤、写明「失败就贴完整报错，不要自己改」。
- **诚实汇报**：没在真机/真实数据上验证的就明说「没验证」；测试是桩就说是桩。
- 用户明确要求过：**在同意他的观点之前，至少提出一个反驳性的意见**。他也不喜欢过度设计（「第一项暂时也没必要，回归主线」）——给建议时要有推荐、不要罗列一堆选项。
- 用户会发真机/模拟器截图报问题：先**读代码找真实原因**，不要猜；修完要说明怎么验证。
- 修复和新功能都要配测试；做「回退验证」证明测试有效。
- 用户的一个习惯：问题常来自真机使用中发现的「基本操作缺失」（如对话删不掉、待办删不掉），要主动做交互层面的多轮检查。

---

## 12. 开发 / 测试 / 部署手册

### 12.1 本地环境
```bash
# 后端
cd DingGO-backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q                     # 101 条，SQLite
# 本地跑：cp .env.example .env 后改好，再 uvicorn app.main:app --reload

# 小程序逻辑测试（Node ≥ 18）
cd DingGO-AI-Saler
node --test "tests/*.test.js"   # 100 条；注意带引号，Node 22 不接受目录参数
```
- 沙箱环境可能是临时的，虚拟环境和依赖可能不在，需要重新装。
- 迁移链验证（建表测试覆盖不到迁移文件）：在临时 SQLite 上 `DATABASE_URL=sqlite:///x.db JWT_SECRET=x ADMIN_TOKEN=x alembic upgrade head`。

### 12.2 写代码时的注意事项（踩过的坑）
- **MySQL**：`TEXT`/`JSON` 列**不能有默认值**（迁移 0006 因此失败过）；新增 `NOT NULL` 列要给 `server_default`。
- **新增「候选门店来源」**必须加进 `routers/plans.py` 的 `SOURCES`，否则前端「确定今日计划」报「来源不正确」。
- **大模型调用**（`core.call_llm`）固定 JSON 模式：提示词要输出 JSON 且包含「JSON」字样；`call_llm_stream` 用于流式。
- **用户输入只当数据**放进提示词；不要把坐标、手机号放进提示词或日志。
- 新增指向 `users.id` 的外键表，账号合并会自动覆盖，但要确认合并的冲突策略（保留旧人员的）。
- 小程序：
  - `wx.showToast` 的标题会被截断，长信息用 `showModal`。
  - 同一帧里连续 `setData` 清空再设回同一个值会被合并，滚动不触发（见 `push()` 里用回调的写法）。
  - 录音管理器、`wx.request` 的 `onChunkReceived` 等全局对象的监听**删不掉**，要「全局注册一次再转发」。
  - 页面里的异步请求回来时，页面状态可能已变（换了对话等）：用 `epoch` 之类的计数作废旧结果。
  - 请求失败不要静默：统一 `try/catch` + 提示；下拉刷新失败也要 `stopPullDownRefresh`。
  - 页面里的 `setInterval` 在测试里已被 `unref`，否则 Node 测试不退出。
  - 「真机调试」模式与「预览」行为可能不同，录音/定位问题优先用预览验证。
  - 用户本地的 `config/index.js` 有真实地址和 `useMock:false`，不要覆盖、不要提交。
- 后端日志里 `Traceback` 不能有；改完后端要跑全套测试。

### 12.3 给 Codex 的部署提示词模板
```
任务：部署最新后端，并确认没有出错。不要修改任何代码；不要输出任何密钥或 .env 内容。

1. 在本地把 GitHub 分支 claude/awesome-pasteur-2fhgln 拉到最新，确认最新提交是 <提交号>。把 DingGO-backend 同步到服务器 /home/ubuntu/DingGO-backend，保留服务器上的 .env 和 data/ 目录，不要覆盖它们。
2. （有迁移时）备份数据库：
   sudo docker compose exec db sh -c 'mysqldump --no-tablespaces -udinggo -p"$MYSQL_PASSWORD" dinggo' > ~/dinggo_before_<提交号>_$(date +%F).sql
3. 重新构建并启动：sudo docker compose up -d --build api
4. 自测并汇报：
   - /health 返回 {"ok":true}
   - sudo docker compose ps：api 是 running，重启次数为 0
   - sudo docker compose logs api --tail 80：没有 ERROR 或 Traceback
   - 迁移版本应为 <最新迁移号>
   - 只有一个 uvicorn 进程
5. 如果构建或启动失败，把完整报错原文贴给我，不要自己改任何东西。
```
生成绑定码（每人一次，码只发给本人）：
```
curl -X POST http://127.0.0.1:8000/admin/users/<用户id>/bind-code -H "X-Admin-Token: <服务器 .env 里的 ADMIN_TOKEN，不要回显>"
```

---

## 13. 代码地图

**后端 `DingGO-backend/`**
| 路径 | 职责 |
|---|---|
| `app/main.py` | 应用入口、异常处理（错误统一返回 `{"message": …}`） |
| `app/config.py` | 全部配置项和默认值（环境变量 / `.env`） |
| `app/models.py` | 所有表模型 |
| `migrations/versions/0001…0009` | Alembic 迁移（见下） |
| `app/routers/` | `auth`（登录/绑定）、`admin`（管理接口）、`stores`、`visits`、`todos`、`plans`、`assistant`（对话/陪练）、`memories`、`files` |
| `app/services/` | `access`（权限）、`summary`（门店/拜访/待办序列化）、`plans`（今日计划/推荐/附近）、`assistant`（今日待办面板/简报）、`ingest`（写入分析结果、生成待办）、`accounts`（合并/绑定）、`geo`（距离）、`due_date`、`playbook`、`audio`、`storage`、`timeutil`、`wx` |
| `app/ai/` | `core`（分析核心、`call_llm`）、`transcribe`（LAS/角色/有效性）、`adapt`、`worker`（流水线调度）、`chat`（对话）、`practice`（陪练）、`memory`、`limits`（限流排队）、`knowledge`、`prompts/`、`roles/` |
| `scripts/` | 导入/整改/回填脚本（默认试运行） |
| `tests/` | pytest；`conftest.py` 给了 `client`、`auth`、`login()`；`test_flow.make_store/make_visit` 是常用造数据 helper |
| `deploy/dinggo-api.service` | 不用 Docker 时的 systemd 方案 |

**迁移**：0001 初始 · 0002 visit_pipeline · 0003 visit_plans · 0004 chat_logs · 0005 chat_sessions · 0006 user_memories · 0007 bind_codes · 0008 practice_sessions · 0009 stores.geo_source。

**小程序 `DingGO-AI-Saler/`**
| 路径 | 职责 |
|---|---|
| `config/index.js` | 三个演示开关、`transport`、`baseUrl` |
| `services/request.js` | 登录、token、绑定、请求封装；换人时清缓存 |
| `services/*.js` | 对应后端接口（真实模式）和演示数据（`useMock` 时） |
| `pages/home/` | 首页对话（最复杂，流式/会话/陪练/今日计划/候选卡片） |
| `utils/cache.js` `backtop.js` `utf8.js` `format.js` `storage.js` | 缓存、回顶、UTF-8 切行、北京时间、本机存储 |
| `components/` | `visit-card`、`score-ring`、`empty-state`、`back-top`… |
| `tests/` | Node 测试 + `harness.js`（模拟 `wx`/`Page`） |

---

## 14. 后端接口一览

登录/账号：`POST /auth/wx-login`、`POST /auth/bind`、`GET /auth/me`
管理（需 `X-Admin-Token`）：`GET|POST /admin/users`、`PATCH /admin/users/{id}`、`POST /admin/users/merge`、`POST /admin/users/{id}/bind-code`、`POST /admin/visits/{id}/analysis`、`POST /admin/visits/{id}/retry`
门店：`GET|POST /stores`、`GET|PATCH /stores/{id}`、`POST /stores/{id}/corrections`、`POST /stores/{id}/location`、`PATCH /stores/{id}/profile/{key}`
拜访：`GET|POST /visits`、`GET|PATCH /visits/{id}`、`POST /visits/{id}/segments`、`…/segments/complete`、`…/confirm-cost`、`…/rejudge`、`…/retry`、`PATCH /visits/{id}/analysis/{module}`
待办：`GET|POST /todos`、`PATCH /todos/{id}`、`POST /todos/{id}/done|undo`
今日计划：`GET /plans/today`、`GET /plans/greeting`、`GET /plans/suggest`（`kind=district|recent|commitments|gap|nearby`）、`POST /plans`、`DELETE /plans/{id}`
助手：`GET /assistant/today`、`GET /assistant/brief/{store_id}`、`POST /chat`、`POST /chat/stream`、`POST /chat/feedback`、`GET|DELETE /chat/sessions`、`GET|DELETE /chat/sessions/{id}`
陪练：`GET /practice/scenarios`、`POST /practice/start|turn|finish`
记忆：`GET|POST /memories`、`PATCH|DELETE /memories/{id}`、`POST /memories/{id}/confirm|team`
其他：`GET /files/{key}`（带过期签名的录音下载）、`GET /health`

---

## 15. 配置项速查（环境变量，服务器 `.env`）

`DATABASE_URL`、`JWT_SECRET`、`WX_APPID`、`WX_SECRET`、`DEV_LOGIN`（上线必须 false）、`REQUIRE_BIND_CODE`（默认 true）、`PUBLIC_BASE_URL`、`ADMIN_TOKEN`、`ASR_PRICE_PER_HOUR`、`LAS_API_KEY`、`LAS_REGION`、`LLM_API_URL`、`LLM_API_KEY`、`LLM_MODEL`（服务器上用户设的是 `deepseek-flash`；代码默认值是 `deepseek-v4-pro`）、`ROLE_LLM_MODEL`、`REVIEW_MODE`、`WORKER_THREADS`、`DAILY_VISIT_LIMIT`，以及限流/陪练相关：`DAILY_CHAT_LIMIT`(200)、`CHAT_PER_MINUTE`(10)、`CHAT_MAX_CONCURRENT`(6)、`CHAT_MAX_QUEUE`(20)、`CHAT_QUEUE_WAIT_SECONDS`(40)、`DAILY_PRACTICE_LIMIT`(30)、`PRACTICE_MIN_ROUNDS`(3)、`PRACTICE_MAX_ROUNDS`(8)。完整清单见 `DingGO-backend/.env.example` 和 `app/config.py`。

---

## 16. 给下一位的建议开场

1. `git fetch && git checkout claude/awesome-pasteur-2fhgln && git log --oneline | head`，确认在 `3b87381` 或更新。
2. 装好环境，跑一遍两套测试，确认基线全绿（101 / 100）。
3. 读 `DingGO-backend/README.md` 的「上线顺序总览」「给运维 / AI 助手的约定」「权限规则」三节。
4. 问用户第 8 节的两个问题（地图 Key、是否自动补坐标）——**在没有答案前，先做 9.1 里不依赖 Key 的小程序侧部分**。
5. 每次推送后按第 11 节的格式向用户汇报，并说明要不要让 Codex 部署。
