---
type: domain
name: <领域名，中文>
code: <领域代码，[a-z0-9]{1,20}，目录名即代码>
scope: <收录范围：什么内容进这个领域>
exclude: <排除边界：什么内容明确不收>
owner: <负责人，可空>
created: YYYY-MM-DD
---

# <领域名>

## 收录范围

<什么内容写进这个领域；给出判断标准。>

## 排除边界

<什么内容明确不收；比如「某门店实例事实」「含地址/人名的描述」。>

## 生命周期与修改检查单

- 新增条目：新开 ID `<code>-<topic>-<seq>`（seq 取该 topic 最大 +1）、`version=1`、`status=active`；文件 `version` +1。
- 修改条目：**ID 不变**，条目 `version` +1，文件内 `## 变更记录` 追加一行。
- 作废条目：**禁止物理删除**，`status: superseded` + `superseded_by: <新id>`。
- 提交前跑一致性自检：`python scripts/knowledge_check.py --root <本仓库>\knowledge`。

## 消费模块

<由流水线 manifest 决定；领域本身不声明消费方。>
