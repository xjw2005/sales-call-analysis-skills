# 知识库变更日志

> 记录跨文件的重要变更（新增/作废领域、大规模条目改动）。单个条目的小修改记在文件内 `## 变更记录`。

## 2026-08-07
- **a2 品牌内容入库**（a2 品牌方供稿，非线下销售中心）：`organization/a2-组织职责.md`（5 角色）、`product/a2-紫白金.md`（5 条目）、`regulation/跨境电商法规.md`（5 条目）；根/product/regulation 三个 index 同步登记。
- **draft 状态引入**（用户拍板）：`STATUS_ENUM` 新增 `draft`；draft 条目不注入、不挂载、不触发空壳警告。
- **占位条目 draft 化**：categories 15 类目 -002（边界/正反例待品牌方）、organization/raci 4 个 -002（流程/话术边界）改 draft；-001（定义/主责）保持 active；sop/flow 6 项保持 active（有实质来源）。
- **categories 条目标签补类目名**（如 `类目, 价格敏感, 定义`）：query 可按类目精准筛选（用户决策：标签必须带类目名）。
- **manifest 挂载基线**（P0 修复，原全空）：concerns→categories 定义（1800）、store-profile→product/a2+类目（2500）、implicit-needs→product/a2（1000）、next-action→raci -001（800）；explicit-needs/effectiveness/quotes 留空（对应库无 active 内容）。生产表预检注入 4 模块、593~2248 token，draft 零泄漏。
- **代码与文档对齐**：manifest.schema.json entry ID 正则放宽多段、status 枚举加 draft；README 三处对齐（query 仅 tags/status、关联校验当前为单向、文件名允许中文）+ 生命周期加「待审」行；self_test 基线改为显式无知识状态。

## 2026-08-07（续）
- **跨境电商法规更新**（经销商 PPT 更新版 250728(1) 清洗重录）：`regulation/跨境电商法规.md` v1→v2——新增「与传统海淘/代购差异」条目（-006，代购/海淘/跨境电商对比表）；-004 补充政策依据（2019 年 39 号公告）与异常交易监控指标（集中度/行为特征/物流异常）；-001 补充模式本质；-002 补充业务链路；修正 PPT 笔误（零进口→零售进口）。
- **「用途:」标签约定落地**（方案一）：a2 产品/类目/raci 共 66 处标签补 `用途:` 标签；manifest 4 个模块挂载改为按用途标签 query 收——新内容打对标签自动挂载，配货单零维护。
- **next-action 守卫**：manifest instruction 增加「RACI 仅用于问题归属判断，不得编造时限/政策/流程细节」。
- **挂载覆盖检查**（knowledge_check 新增）：active 条目未被任何模块注入 → 告警（当前暴露 22 条待决策：a2 组织职责/法规/流程标准）。
- **模块提示词补知识边界**：implicit-needs/concerns/store-profile/next-action 各补一句「知识只用于理解/归类，不替代录音证据」。

## 2026-08-06
- **首批内容条目落地**（内容期开工，全部为初稿、标注待审定来源）：
  - `sop/flow/` 6 项（来源：DSR 语料库 V2）：实体店价值口径、合规讲解标准、供应链回应标准、价格治理流程、陈列物料流程、系统操作 SOP
  - `organization/raci/` 4 项（来源：需求文档）：注册失败→技术、价格利润→商品、代运营开户→运营、政策承诺→合规确认
  - `categories/` 15 项（9 类定义按 concerns 九类口径，6 新类 + 其他为骨架）：价格敏感/物流/控价防窜/培训/售后/合作模式/利润/系统工具/其他/库存风险/合规/客户资产保护飞单/线上获客/经营压力/决策链
- 待品牌方/后台部门供稿部分均已在条目内标注「待补充」。

## 2026-08-05
- **8 库结构定稿**（5 库构想 → 8 库）：新增 `categories/`（14 类业务类目）、`scoring/`（院边店效果模型）、`organization/`（RACI/服务目录/通讯录指引）；`sop/` 扩为 flow / talktrack / action-qc 三个子目录；`product/` 新增 policy/ 子目录；删除 `industry/`、`methodology/` 占位域。
- **类目双口径规则落地**（用户决策）：打标按 9 类、内容按 14 类；`categories/类目总表.md` 建 9↔14 映射。
- **评分决策**：以需求文档为准（阶段模型+双维拆分），评分维度重新设计后置，知识库先行。
- 新增专用模板：category.md、scoring-stage.md、raci.md。
- 每个库建 README（domain 声明）+ index（符号表）+ 子目录占位说明。

## 2026-08-03
- 初始化知识库架构：README 约定总纲、index 索引、四领域目录（product / industry / competitor / methodology，均为占位）。
- 与 `sales-call-analysis-scripted` 流水线接入：`knowledge_root` 配置、`scripts/knowledge.py`、`references/knowledge/manifest.json`（selection 暂空）。
