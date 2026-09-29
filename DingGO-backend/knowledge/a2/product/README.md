---
type: domain
name: 产品知识
code: product
scope: 自有产品 / 品类 / 品牌 / 规格的识别线索、定位与适用边界；产品政策（见 policy/ 子目录）
exclude: 某门店的实例事实；含地址/人名的描述；竞品（见 competitor）
owner:
created: 2026-08-03
---

# product — 产品知识

## 收录范围

自有产品的品类/品牌/规格识别线索、产品定位与卖点、适用人群与边界。条目写「识别线索 + 适用边界」，不写实例事实。

## 排除边界

- 某门店发生的事（实例事实）——归录音证据，不归知识库
- 含地址后缀或人名的描述（会触发 store-profile 硬校验误报）
- 竞品资料（归 `competitor/`）

## 生命周期与修改检查单

- 新增条目：新开 ID `product-<topic>-<seq>`、`version=1`、`status=active`；文件 `version` +1。
- 修改条目：**ID 不变**，条目 `version` +1，文件内 `## 变更记录` 追加一行。
- 作废条目：**禁止物理删除**，`status: superseded` + `superseded_by: <新id>`。
- 提交前跑：`python scripts/knowledge_check.py --root <本仓库>\knowledge`。

## 消费模块

由流水线 manifest 决定（当前为空）。预计主要消费方：store-profile。
