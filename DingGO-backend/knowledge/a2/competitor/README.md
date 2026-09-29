---
type: domain
name: 竞品知识
code: competitor
scope: 竞品品牌 / 同框产品 / 对比口径
exclude: 自有产品（见 product）；某门店的实例事实；含地址/人名的描述
owner:
created: 2026-08-03
---

# competitor — 竞品知识

## 收录范围

竞品品牌、与自有产品同框的竞品、对比口径（价格带、卖点差异、识别线索）。用于识别录音中出现的竞品表述并归因。

## 排除边界

- 自有产品（归 `product/`）
- 某门店发生的事（实例事实）
- 含地址后缀或人名的描述

## 生命周期与修改检查单

- 新增条目：新开 ID `competitor-<topic>-<seq>`、`version=1`、`status=active`；文件 `version` +1。
- 修改条目：**ID 不变**，条目 `version` +1，文件内 `## 变更记录` 追加一行。
- 作废条目：**禁止物理删除**，`status: superseded` + `superseded_by: <新id>`。
- 提交前跑：`python scripts/knowledge_check.py --root <本仓库>\knowledge`。

## 消费模块

由流水线 manifest 决定（当前为空）。预计主要消费方：store-profile、quotes。
