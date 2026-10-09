# AI 赋能销售云 · 微信小程序（前端第一版）

把现有「录音 → 转写 → 销售分析 → 门店档案」流水线的结果搬到小程序上呈现，界面参考支付宝「蚂蚁阿福」。
当前全部使用**演示数据**（`config/index.js` 的 `useMock: true`），不需要后端就能在开发者工具里点通所有页面。

## 怎么打开

1. 拉取本仓库，微信开发者工具 → 导入项目 → 目录选 `DingGO-AI-Saler/`。
2. 已配置项目 AppID（`project.config.json`），可直接真机调试。
3. 不需要「构建 npm」，没有第三方依赖。

## 页面

| 底部标签 / 页面 | 路径 | 内容 |
|---|---|---|
| 助手（首页） | `pages/home` | 参考阿福：每次新建对话，助手先问候：有今日计划报进度，有到期约定先提醒，都没有就问今天去哪个区，点选后给候选门店，勾选确定「今日计划」。欢迎卡里有一行今日提醒和两个选项。点「今日待办」→ 对话里出现一张大卡片（今日计划、到期的约定和跟进、待处理录音，可直接完成/复制话术/去录音）；点「AI 销售陪练」→ 进入对话内陪练（选场景 → 用底部输入框回答 4 轮 → 点评卡，可退出）。输入框保留自由提问；☰ 抽屉里有陪练入口、对话记录 |
| 门店 | `pages/store/list` | 门店管理，搜索 + 筛选（合作状态、拜访情况：有拜访/从未拜访/30天未访/近30天有访、负责人），分页加载；4 种合作状态标签；没有门店时显示空状态 +「添加门店」 |
| 拜访 | `pages/visit/list` | 拜访记录，按 全部/待确认/处理中/已完成/无效 筛选 |
| 我的 | `pages/me` | 本月统计、隐私告知、清空对话 |
| 录音 | `pages/record` | 选门店、拜访阶段、是否合作；大录音按钮（10 分钟自动续段、来电中断恢复）；导入聊天记录里的音频；档案纠正；可选「门店状况」；这次没录音可「直接登记拜访」（选原因）；现场速记随拜访保存 |
| 拜访详情 | `pages/visit/detail` | 处理进度时间线、费用确认、无效录音、洞察/话术/行动/对话 四个标签，点时间戳跳播；新增「拜访信息」卡（目的、门店状况、现场速记、调研）、无录音拜访、从飞书导入的「历史记录」（分析原文分段展示） |
| 门店详情 | `pages/store/detail` | 三个圆环（合作进展/关心点/行动闭环）、七维度档案、拜访记录、行动闭环、纠正档案 |
| 添加门店 | `pages/store/edit` | 名称、省市、地图选址 |
| 助手记住了什么 | `pages/memory` | 「我的」里进入：待确认的记忆（记住 / 不用）、已记住的别名（修改、删除；经理可设为团队通用）、手动「教助手一个说法」。对话里助手想记住某个说法时会弹出确认卡 |
| 绑定账号 | `pages/bind` | 第一次使用、微信还没绑定到人员时自动进入：输入管理员发的 8 位绑定码，成功后进入首页 |
| 进店前简报 | `pages/brief` | 门店画像、上次没做完的事、本次目标、该问的问题（按档案「未确认」维度生成）、可能遇到的异议 |
| 我的待办 | `pages/todo` | 来自分析结果的建议行动，按到期排序；月度目标类待办显示「目标 / 已达成 / 差额」进度条；标记完成、复制微信跟进话术 |
| 异议应对 | `pages/objection` | 常见异议的应对要点和参考话术 |

演示小技巧：在「拜访」里打开「张记副食」那条待确认记录，点「确认识别」，约 20 秒内能看到识别 → 整理 → 7 个模块分析 → 完成的全过程。

## 目录

```
config/      全局开关（演示数据 / 真实接口地址）
model/       演示数据（门店、拜访、转写、分析结果）
services/    取数据：store / visit / chat / upload；useMock=false 时改走真实接口
utils/       常量（拜访阶段、九类关心点、八个评分维度、七个档案维度，与 Python 流水线一致）、格式化
components/  visit-card、score-ring、status-timeline、empty-state、ai-badge
custom-tab-bar/  底部悬浮标签栏
pages/       页面
assets/      图标与空状态插画（SVG）
```

## 连接真实后端（DingGO-backend）

1. 按 `DingGO-backend/README.md` 把后端部署到服务器。
2. 改 `config/index.js`：`useMock: false`，`baseUrl: 'http://服务器公网IP:8000'`。`mockAI: false`（首页问答走后端大模型；服务器没配置密钥时会提示）；`mockPractice` 保持 `true`（陪练仍用演示数据，直到后端接入）。
3. 开发者工具「详情 → 本地设置」勾选「不校验合法域名…」；手机上用真机调试。
4. 首次请求会自动 `wx.login` 登录，凭证存在本机。

## 后端需要提供的接口（`useMock` 改为 `false` 后调用）

| 接口 | 用途 |
|---|---|
| `POST /chat` `{question, storeId, history}` → `{text, suggestions}` | 首页 AI 问答（后端接大模型，结合门店数据作答） |
| `GET /assistant/today?storeId=` → `{hint, stat, steps, sections, suggestions}` | 首页「今日待办」大卡片 |
| `GET /assistant/brief/:storeId` | 进店前简报 |
| `GET /todos`、`POST /todos/:id/done`、`POST /todos/:id/undo` | 待办（后端需把“周五前”等时限解析成具体日期） |
| `GET /practice/scenarios`、`POST /practice/start`、`POST /practice/turn`、`POST /practice/finish` | AI 陪练（后端接大模型扮演客户并点评） |
| `GET /stores`、`GET /stores/:id`、`POST /stores` | 门店列表 / 详情（含档案、仪表、行动）/ 新建 |
| `POST /stores/:id/corrections` | 门店档案纠正 |
| `POST /visits` → 含 `upload: {uploadUrl, formData}` | 新建拜访并返回录音上传凭证 |
| `GET /visits`、`GET /visits/:id` | 拜访列表 / 详情（状态、模块进度、分析结果、转写） |
| `POST /visits/:id/confirm-cost`、`POST /visits/:id/rejudge` | 确认识别费用 / 重新判定有效性 |

返回字段结构以 `model/analysis.js`、`model/seed.js` 为准，与 `sales-call-analysis-scripted/references/output-spec.md` 一一对应。

## 还没做 / 要真机验证

- 登录（`wx.login` 换 token）、区域经理角色。
- 息屏或切后台时录音是否持续，需要真机测试。
- AI 问答、陪练点评目前是按关键词拼的演示结果；接入大模型后建议改为流式输出。
- 简报里的问题、异议话术来自 `utils/playbook.js` 固定话术库，后期可由大模型按门店情况生成。
- 主动提醒推送到微信需用订阅消息（一次性订阅），尚未接入。
