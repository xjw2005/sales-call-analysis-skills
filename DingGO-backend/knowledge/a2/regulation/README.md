---
type: domain
name: 跨境法规
code: regulation
scope: 跨境商品销售相关的已审法规条文（下单规则、补税、报关/申报、进口资质、标签标识、广告合规）
exclude: 销售执行口径（归 sop/）；规则卡的具体话术表述（归 sop/合规讲解标准）
owner:
created: 2026-08-05
---

# regulation — 跨境法规

## 收录范围

「已审规则卡」背后的法规条文依据。AI 只引用已审版本，不自行解读或编造（铁律：不编造公司政策）。每条带法规/政策名称、生效时间、来源出处。

## 排除边界

- 销售怎么讲（执行口径）——归 `sop/`
- 产品卖点/价格——归 `product/`

## 生命周期与修改检查单

- 新增条目：新开 ID `regulation-<topic>-<seq>`、`version=1`、`status=active`；文件 `version` +1。
- 修改条目：**ID 不变**，条目 `version` +1，文件内 `## 变更记录` 追加一行。
- 作废条目：**禁止物理删除**，`status: superseded` + `superseded_by: <新id>`。
- 法规更新：政策变更时升版本并更新 `updated`（90 天未更新触发自检告警）。
- 提交前跑：`python scripts/knowledge_check.py --root <本仓库>\knowledge`。

## 消费模块

由流水线 manifest 决定。预计消费方：concerns（合规类目口径）、quotes（政策校验）、next-action（合规场景）。
