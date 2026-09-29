# 知识库索引

> 导航用。机器权威清单由「目录扫描 + frontmatter」得出，本文件不是数据源。

## 8 个知识库（2026-08-05 定稿）

- [[knowledge/regulation/README|regulation — 跨境法规]]（静态·人工审定）
- [[knowledge/product/README|product — 自有产品 + 产品政策]]（静态·人工审定）
- [[knowledge/competitor/README|competitor — 竞品]]（静态·人工填写）
- [[knowledge/sop/README|sop — 业务规范：流程 / 话术效果库 / 行动质检库]]（静态·人工审定）
- [[knowledge/categories/README|categories — 14 类业务类目]]（静态·人工审定）
- [[knowledge/scoring/README|scoring — 院边店效果模型与评分标准]]（静态·人工审定）
- [[knowledge/organization/README|organization — 组织职责：RACI / 服务目录 / 通讯录指引]]（静态·人工审定）
- [[knowledge/archive/README|archive — 门店档案快照]]（动态·机器生成）

## 主题文件

| 领域 | 文件 | 文件级 ID | 状态 |
|---|---|---|---|
| categories | 类目总表.md | categories-master | active（9↔14 映射） |
| categories | 14 类目定义文件（价格敏感.md 等 15 个） | 每文件一主题 | -001 定义 active / -002 边界 draft |
| sop | flow/ 6 文件（实体店价值口径/合规讲解标准/供应链回应标准/价格治理流程/陈列物料流程/系统操作SOP） | sop-flow-* | active |
| organization | 通讯录指引.md | organization-roster-guide | active（动态数据放飞书） |
| organization | raci/ 4 文件（注册失败/价格利润/代运营开户/政策承诺） | organization-raci-* | -001 主责 active / -002 流程 draft |
| organization | a2-组织职责.md | organization-a2-roles | active（a2 品牌方供稿） |
| product | a2-紫白金.md | product-a2-platinum | active（a2 品牌方供稿） |
| regulation | 跨境电商法规.md | regulation-cross-border | active（a2 品牌方供稿） |
| — | （sop/talktrack、sop/action-qc、scoring、service-catalog、competitor、product/policy、archive/stores 待内容） | — | — |

## 维护约定

- 新增领域：建 `<code>/README.md`（用 `_templates/domain.md`）→ 本文件登记 → `CHANGELOG.md` 记一条。
- 新增/修改条目：按 `README.md` 生命周期节；机器一致性由 `scripts/knowledge_check.py` 强制。
- 类目双口径规则（打标 9 类 / 内容 14 类）见 `README.md` 与 `categories/类目总表.md`。
