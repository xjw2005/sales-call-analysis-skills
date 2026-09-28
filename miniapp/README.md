# AI 赋能销售云 · 微信小程序（前端第一版）

把现有「录音 → 转写 → 销售分析 → 门店档案」流水线的结果搬到小程序上呈现，界面参考支付宝「蚂蚁阿福」。
当前全部使用**演示数据**（`config/index.js` 的 `useMock: true`），不需要后端就能在开发者工具里点通所有页面。

## 怎么打开

1. 拉取本仓库，微信开发者工具 → 导入项目 → 目录选 `miniapp/`。
2. AppID 选「测试号」或填你自己的 AppID（`project.config.json` 里默认是 `touristappid`）。
3. 不需要「构建 npm」，没有第三方依赖。

## 页面

| 底部标签 / 页面 | 路径 | 内容 |
|---|---|---|
| 助手（首页） | `pages/home` | AI 问答界面。助手先开口，推送「今天建议你做的事」卡片（到期跟进、待确认录音、拜访复盘、进店前简报、异议提醒、久未拜访、录音过短）；下面是固定选项；底部保留输入框自由提问。顶部有「拜访四步」进度和「AI 陪练」入口；☰ 抽屉里有 AI 陪练、我的门店、对话记录 |
| 门店 | `pages/store/list` | 门店管理，搜索；没有门店时显示空状态 +「添加门店」 |
| 拜访 | `pages/visit/list` | 拜访记录，按 全部/待确认/处理中/已完成/无效 筛选 |
| 我的 | `pages/me` | 本月统计、隐私告知、清空对话 |
| 录音 | `pages/record` | 选门店、拜访阶段、是否合作；大录音按钮（10 分钟自动续段、来电中断恢复）；导入聊天记录里的音频；档案纠正 |
| 拜访详情 | `pages/visit/detail` | 处理进度时间线、费用确认、无效录音、洞察/话术/行动/对话 四个标签，点时间戳跳播 |
| 门店详情 | `pages/store/detail` | 三个圆环（合作进展/关心点/行动闭环）、七维度档案、拜访记录、行动闭环、纠正档案 |
| 添加门店 | `pages/store/edit` | 名称、省市、地图选址 |
| 进店前简报 | `pages/brief` | 门店画像、上次没做完的事、本次目标、该问的问题（按档案「未确认」维度生成）、可能遇到的异议 |
| 我的待办 | `pages/todo` | 来自分析结果的建议行动，按到期排序；标记完成、复制微信跟进话术 |
| 异议应对 | `pages/objection` | 常见异议的应对要点和参考话术 |
| AI 陪练 | `pages/practice/list`、`pages/practice/chat` | 选场景，AI 扮演老板对话 4 轮，结束后打分并给参考说法 |

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

## 后端需要提供的接口（`useMock` 改为 `false` 后调用）

| 接口 | 用途 |
|---|---|
| `POST /chat` `{question, storeId, history}` → `{text, suggestions}` | 首页 AI 问答（后端接大模型，结合门店数据作答） |
| `GET /assistant/feed?storeId=` → `{cards, steps}` | 首页主动卡片与拜访四步进度 |
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
