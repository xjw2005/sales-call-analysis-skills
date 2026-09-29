# 分析知识库（Knowledge Base）

「AI 电话语音识别分析」的分析用知识库。**由分析流水线运行时消费**：脚本按模块清单（manifest）把相关条目注入每个分析模块的 system prompt，帮助模型理解与归类，不替代录音证据。

初版为纯 Markdown（本目录），内容量大了之后切 RAG（每条原子条目即一个检索 chunk，格式无需重写）。设计思路参考卡帕西（Karpathy）LLM Wiki：编译器模式（入库一次编译建链，运行时只读不检索）、index 符号表、lint 校验。

## 谁来消费

- **机器**：`sales-call-analysis-scripted` 流水线（`scripts/knowledge.py` + `references/knowledge/manifest.json`）。机器定位一律靠 frontmatter 的 `id`，不靠文件名。
- **人 / AI 助手**：直接在本仓库用 Obsidian 阅读、维护。

## 目录结构（8 库，2026-08-05 定稿）

```
knowledge/
├── README.md       本文件（约定总纲）
├── index.md        全库符号表（人工导航；机器权威清单 = 目录扫描 + frontmatter）
├── CHANGELOG.md    全局变更日志
├── TODO-设计.md     设计 TODO（当前 8 库定稿 + 待办）
├── _templates/     模板（domain / entry / category / scoring-stage / raci）
├── regulation/     跨境法规（静态·人工审定）
├── product/        自有产品 + policy/ 产品政策（静态·人工审定）
├── competitor/     竞品（静态·人工填写）
├── sop/            业务规范：flow/ 流程 + talktrack/ 话术效果库 + action-qc/ 行动质检库
├── categories/     业务类目：14 类定义/边界/正反例（静态·人工审定）
├── scoring/        效果模型与评分标准：专案目标 + stage/ 六阶段 + 评分口径
├── organization/   组织职责：raci/ + service-catalog/ + 通讯录指引
└── archive/        沉淀档案：stores/ 门店档案快照（动态·机器生成）
```

## 领域

| 领域目录 | 领域代码 | 收录范围 | 性质 |
|---|---|---|---|
| `regulation/` | `regulation` | 跨境法规（规则卡依据） | 静态·人工审定 |
| `product/` | `product` | 自有产品 + 产品政策（policy/） | 静态·人工审定 |
| `competitor/` | `competitor` | 竞品 | 静态·人工填写 |
| `sop/` | `sop` | 流程 / 话术效果库 / 行动质检库 | 静态·人工审定 |
| `categories/` | `categories` | 14 类业务类目定义/边界/正反例 | 静态·人工审定 |
| `scoring/` | `scoring` | 院边店效果模型与评分标准 | 静态·人工审定 |
| `organization/` | `organization` | RACI / 服务目录 / 通讯录指引 | 静态·人工审定 |
| `archive/` | `archive` | 门店档案快照 | 动态·机器生成 |

**新增领域规则**：先在根目录新建 `<code>/README.md`（用 `_templates/domain.md`），代码 `[a-z0-9]{1,20}`（目录名即代码）全局唯一；目录深度最多 2 层；文件名（建议 kebab-case，允许中文）只供人读，机器定位靠 frontmatter `id`。

## 类目双口径规则（用户 2026-08-05 决策，不要推翻）

1. **打标口径**：分析模块「关心类目打标」按原有 **9 类**（价格敏感/物流时效/控价防窜/培训支持/售后保障/合作模式/利润空间/系统工具/其他），飞书字段与流水线白名单不改。
2. **内容口径**：知识库内容按需求文档 **14 类**收集（定义/边界/正反例），`categories/` 建 14 个类目条目。
3. `categories/类目总表.md` 维护 9↔14 映射；若将来打标升级，映射表即依据。

## 关联约定（机器可解析，Obsidian 只做展示）

- **机器关联**：frontmatter `related: [知识#id]` + 正文 `知识#id` 行内引用——确定性、可校验（`knowledge_check.py` 当前实现：正文引用必须指向 active 条目；双向一致性校验为待办，未实现）。
- **人看关联**：`[[wikilink]]` 供 Obsidian Graph view 可视化——**Obsidian 是展示层，不负责生成关联**。
- **双向规则**：「声明必有落点」——A 声明关联 B 时三选一：B 反向声明 / B 正文显式引用 A / B 在 index.md 符号表中。
- **不用自动建链插件**（向量相似度自动建链）：相似 ≠ 相关，产生噪音且不可审计。正确方式是 AI 半自动建链：入库时由 AI 生成候选关联（每条上限 5 条、逐条附理由），人工审核，机器校验兜底。
- query 型选择只做**结构化字段筛选**（当前实现支持 `tags`/`status`；`type`/`scope` 留待 RAG 期），不做 embedding 语义检索。

## 文件与条目格式（RAG-ready）

一个「主题」一个文件，文件级 frontmatter：

```markdown
---
type: knowledge
domain: categories        # 领域代码
id: categories-price-sensitive   # 文件级ID：<domain>-<topic>
title: 价格敏感
version: 1
status: active
updated: 2026-08-06
tags: [类目, 价格敏感]      # 必须含本主题名（query 按它筛选）
source:
  - type: 文档
    value: 需求文档
---
```

正文 = 多个**原子条目**，每个 = 一个 `##` 标题块。**条目是检索与注入的最小单位，也是未来 RAG 的 chunk**：

```markdown
## 定义与识别线索（categories-price-sensitive-001）

> [!kb] 条目元数据
> tags: 类目, 价格敏感, 定义
> version: 1
> updated: 2026-08-06
> status: active

正文：自包含的一则知识，200~500 token（CJK 约 340~850 字符）。
```

条目约定：
- 标题格式 `## <标题>（<id>）`，ID 在末尾括号内；机器用正则提取，chunker 不丢 ID。
- 条目元数据用 Obsidian callout `[!kb]`；`tags` 是检索 facet。
- **稳定 ID** `<domain>-<topic>-<seq>`：分配后**永不复用、永不改指**；含义变了就新开 ID，旧 ID 置 `status: superseded` + `superseded_by: <新id>`。
- 正文自包含；文件末尾可有 `## 变更记录`（解析器跳过）。

## 兼容红线（写条目前必读）

- 条目正文**不得出现地址后缀（省/市/区/县/镇/街道/路）或人名/「X总」样式**——会触发 `store-profile` 模块的专有信息硬校验误报。
- 条目**只放分类/枚举/识别线索/标准做法**，不放「某门店发生了 X」这类实例事实（实例事实归 `archive/`）。

## 生命周期

| 操作 | 规则 |
|---|---|
| 新增 | 新开 ID（seq 取该 topic 最大 +1）、`version=1`、`status=active`；文件 `version` +1；`CHANGELOG.md` 记一条 |
| 修改 | **ID 不变**；条目 `version` +1、`updated` 更新；文件 `version` +1；`## 变更记录` 追加一行 |
| 待审 | `status: draft`（占位/待审定内容专用）；draft 条目**不注入、不挂载、不触发空壳警告**；补全审定后改回 `active` |
| 作废 | **禁止物理删除**；`status: superseded` + `superseded_by: <新id>`；manifest 指向旧 ID 由 preflight 报错 |

> 版本递增同时是未来 RAG 增量重建的信号。时效性内容（政策/法规/服务时限）90 天未更新触发自检告警。

## 维护工作流（新 AI 上手必读）

**改任何内容前**：先读本文件 + `TODO-设计.md` 的「不要推翻」决策；拿不准口径先问人，不自行发明。

**status 判定口径**（写条目时必填）：
- `active` = 内容已审定、来源明确、能直接用于分析
- `draft` = 占位 / 待品牌方补充 / 未审定。draft 条目不注入、不挂载；补全审定后改回 active

**标签打标规范**：
- frontmatter 与条目 callout 的 `tags` 都要打；**必须含主题名**（如类目条目必须含「价格敏感」），否则 query 选不中
- 品牌内容加品牌标签（a2 产品/法规/组织职责都带 `a2`）；a2 与线下销售中心的内容按品牌标签区分，不混用

**新增一个知识文件的完整步骤**：
1. 用 `_templates/entry.md` 建文件：frontmatter + 原子条目（`## 标题（id）` + `[!kb]` callout）
2. 按上面的 status 口径与标签规范填写
3. 跑校验：`python scripts/knowledge_check.py --root <本目录>`（在 `sales-call-analysis-scripted` 技能目录下执行），**0 错误才算通过**
4. 在对应领域的 `index.md` 登记；根 `index.md` 需要时同步
5. `CHANGELOG.md` 记一条
6. 若该文件需要进某模块的注入集：改 `references/knowledge/manifest.json`（`file` = 该文件全部 active 条目 / `entry` 单条 / `query` 按 tags 筛选），然后跑一次预检确认注入量

**manifest 维护**：
- 挂载文件位置：`C:\Users\12909\.codex\skills\sales-call-analysis-scripted\references\knowledge\manifest.json`
- 三种类型：`file`（点名文件，取其中全部 active 条目）/ `entry`（点名单条，新条目不会自动进来）/ `query`（按 tags 收，新条目打对标签自动跟上）
- `max_tokens` 是注入预算，超了按条目 ID 序截断；内容多了调预算或收窄 query
- 改完跑预检：`python scripts/pipeline.py --profile visit_prod --view vewIf60qAA --preflight`（只读），看每模块注入行数/token

**改完必跑**：`knowledge_check`（0 错误）→ 预检（注入正常）→ 必要时 `self_test`

## 与流水线的对接

- `config.local.json` 的 `knowledge_root` 指向本目录；`knowledge_enabled=false` 或目录缺失时注入自动关闭，行为与未建知识库完全一致。
- 每模块消费什么知识由 `C:\Users\12909\.codex\skills\sales-call-analysis-scripted\references\knowledge\manifest.json` 决定（`file` = 该文件下全部 active 原子条目 / `entry` 单条 / `query` 结构化筛选，预留向量检索）。
- 模型输出可写 `知识#<id>` 引用注入的条目；流水线校验引用必须命中本次注入集，审计留日志，飞书正文剥离引用标记。
