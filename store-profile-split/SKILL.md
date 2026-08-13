---
name: store-profile-split
description: 承接销售录音分析流水线（sales-call-analysis-scripted）的 store-profile 模块，把 02 门店拜访记录中最新一次「门店档案」按固定模板解析拆解，填入 01 门店主档对应的 7 个具体维度字段（一句画像、门店基本信息、主营品类与品牌、经营模式、选品偏好、利润偏好、合作偏好与排斥项），让主档可读、可筛选。只填空字段，未确认项按档案原文口径照写，不合并多次档案、不改飞书字段结构。
---

# 门店档案拆分（store-profile-split）

## 用途

分析流水线跑完 `store-profile` 模块后，02 门店拜访记录的「门店档案」字段是单次录音快照（一句话画像 + 6 个事实维度 + 关键证据 + 档案口径）；01 门店主档的「门店档案（总）」只是把这些快照拼接成一大坨（lookup raw_value），不可读、不可筛选。

本 SKILL 把**进店时间最新一次**（且档案非空）的档案拆解，逐维度填入 01 门店主档已有的 7 个字段，让主档成为可筛选的门店画像。

## 设计约束（用户 2026-08-06 确认，不要推翻）

1. **源**：直接读 02 拜访记录进店时间最新一条的「门店档案」单份文本；不解析「门店档案（总）」拼接文本（lookup 聚合顺序不可靠）。
2. **只填空**：主档目标字段已有内容则跳过不写（铁律 10）；`--overwrite` 才强制覆盖。
3. **未确认按原文**：档案某维度写「未确认」就原样写「未确认」进主档字段，不省略、不加工；不携带 `（稳定档案）` 状态标记。
4. **单次快照**：不合并多次档案（铁律 7）；关键证据/档案口径/证据边界留在 02 表原文，不进主档。
5. **不改飞书字段结构**：不新增/不删除/不重命名字段。

## 字段映射

| 档案维度（store-profile 输出） | 01 门店主档字段 |
|---|---|
| 一句话画像 | 一句画像 |
| 门店基本信息 | 门店基本信息 |
| 主营品类与品牌 | 主营品类与品牌 |
| 经营模式 | 经营模式 |
| 选品偏好 | 选品偏好 |
| 利润偏好 | 利润偏好 |
| 合作偏好与排斥项 | 合作偏好与排斥项 |
| 关键证据 / 档案口径 / 证据边界 | 不写入（保留在 02 表可溯源） |

完整说明见 [references/field-mapping.md](references/field-mapping.md)。

## 命令

所有命令在本 Skill 目录下用 PowerShell 执行：

```powershell
python scripts/self_test.py                        # 自检（解析器 + 与流水线映射一致性）
python scripts/self_test.py --live                 # 自检 + 从飞书实读核对字段
python scripts/split.py --dry-run --verbose        # 预览全表将要填的内容，不写飞书
python scripts/split.py --dry-run --record-ids recxxx1,recxxx2   # 指定主档记录预览
python scripts/split.py --record-ids recxxx1,recxxx2             # 指定记录试跑（写回+复读）
python scripts/split.py                            # 全表只填空字段
python scripts/split.py --overwrite                # 全表用最新档案覆盖已有内容（慎用）
```

## 流程

1. 分析流水线跑完 store-profile（02 表「门店档案」已写入、复读通过）。
2. 运行 `python scripts/split.py --dry-run` 预览：每条主档记录将填入哪些字段、来源拜访是哪条。
3. 人工核对预览无误后，先指定 1-2 条主档记录正式试跑（写回 + 逐字段复读）。
4. 确认效果后全表执行（默认只填空字段，可重复执行，已填的自然跳过）。

## 输出与校验

- 每条记录打印：来源拜访 record_id、每个将写入/已写入字段及值摘要。
- 写回采用逐字段 `+record-upsert`（串行），随后 `+record-get` 复读一致才算该字段成功。
- 结尾汇总：处理条数 / 无档案跳过 / 填入字段数 / 已有内容跳过数 / 档案缺维度条数 / 复读不一致数。
- 无档案（该门店从未拜访或档案为空）的记录跳过并计入统计，不报错。

## 边界

- 本 SKILL 只拆「门店档案」一个字段，不触碰其他六个分析模块字段（显性/隐性需求、关心类目、打分、金句、下一步行动）。
- 不改任何飞书字段结构；「门店档案（总）」lookup 是否改为只取最新，属字段配置变更，需用户另行授权。
- 档案解析失败（缺少全部维度）时记 warning，不写空值、不覆盖。

## 配置

`config.local.json`（真实配置）与 `config.example.json`（模板）：
- `base`：01/02 表所在 Base token
- `master_table`：01 门店主档 table_id
- `visit_table`：02 门店拜访记录 table_id
- `visit_link_field`：02 表中指向主档的关联字段名（默认 `关联门店`）
- `visit_date_field`：02 表进店时间字段名（默认 `进店时间`）
- `visit_profile_field`：02 表门店档案字段名（默认 `门店档案`）
- `fields`：主档字段名 → 档案维度键（`one_line`/`basic`/`categories_brands`/`business_model`/`selection_motion`/`price_profit`/`cooperation_preferences`）
