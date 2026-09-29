---
type: domain
name: 业务规范
code: sop
scope: 销售→客户装机全流程、话术规范/红线、话术效果库（场景×异议×话术×反应×评价）、行动质检库（阶段×客户类型→推荐动作）
exclude: 法规条文（归 regulation/）；产品政策原文（归 product/policy/）；组织职责与归属（归 organization/）
owner:
created: 2026-08-05
---

# sop — 业务规范

## 收录范围

销售与区域经理的标准行为规范，三个子目录：

- `flow/`：全流程 SOP（首批 6 项：实体店价值固定口径、合规讲解标准、供应链回应标准、价格治理流程、陈列物料申请流程、系统操作 SOP）+ 销售→客户装机全流程
- `talktrack/`：话术效果库（需求 2：拜访场景、客户异议、标准话术、客户即时反应、是否听懂/接受、后续动作、最终推进结果、专家评价、适用边界；每条话术需带「为什么」与「何时可用」）
- `action-qc/`：行动质检库（需求 4：拜访阶段×客户类型 → 推荐动作、应追问问题、需携带资料、异议处理、跟进时限、成功标准、无效动作、优秀案例；每个动作有「为什么做」和「做到什么程度算完成」）

## 排除边界

- 法规条文——归 `regulation/`
- 产品政策（价格/控价/供应链承诺）——归 `product/policy/`，sop 条目用 `知识#` 引用，不重复存
- 组织职责/问题归属——归 `organization/`

## 生命周期与修改检查单

- 新增条目：新开 ID `sop-<topic>-<seq>`、`version=1`、`status=active`；文件 `version` +1。
- 修改条目：**ID 不变**，条目 `version` +1，文件内 `## 变更记录` 追加一行。
- 作废条目：**禁止物理删除**，`status: superseded` + `superseded_by: <新id>`。
- 提交前跑：`python scripts/knowledge_check.py --root <本仓库>\knowledge`。

## 消费模块

由流水线 manifest 决定。预计消费方：quotes（talktrack）、next-action（action-qc + flow）、concerns（口径交叉）。
