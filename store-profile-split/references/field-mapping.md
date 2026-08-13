# 档案维度 ↔ 主档字段映射

## 数据流

```
sales-call-analysis-scripted store-profile 模块
  → 02 门店拜访记录「门店档案」(fldinpMEZx)  单次录音快照（9 维度固定模板）
  → store-profile-split（本 SKILL）          取进店时间最新且非空的一份
  → 解析 7 个维度 → 填入 01 门店主档对应字段（只填空）
```

01 主档「门店档案（总）」(fld5zYTNvH) 是 02 表「门店档案」的 lookup raw_value 拼接，
仅作展示；本 SKILL 不解析它，直接读 02 表最新单份（结果等价、顺序可靠）。

## 映射表

| 档案维度（键） | 档案中的行格式 | 01 主档字段 | 写入值 |
|---|---|---|---|
| one_line | `一句话画像：{内容}` | 一句画像 | 内容原文 |
| basic | `门店基本信息（稳定档案）：{内容}` | 门店基本信息 | 内容原文 |
| categories_brands | `主营品类与品牌（当前状态）：{内容}` | 主营品类与品牌 | 内容原文 |
| business_model | `经营模式（未确认）：未确认` | 经营模式 | 内容原文（含「未确认」） |
| selection_motion | `选品偏好（当前状态）：{内容}` | 选品偏好 | 内容原文 |
| price_profit | `利润偏好（当前状态）：{内容}` | 利润偏好 | 内容原文 |
| cooperation_preferences | `合作偏好与排斥项（稳定档案）：{内容}` | 合作偏好与排斥项 | 内容原文 |
| 关键证据 / 档案口径 / 证据边界 | 证据块与元信息行 | —（不写入） | 保留在 02 表原文 |

## 关键规则

1. **状态标记不写入**：`（稳定档案）/（当前状态）/（未确认）` 只用于解析定位，
   主档字段只写内容原文。
2. **未确认照写**：档案维度内容为「未确认」时，主档字段也写「未确认」
   （用户 2026-08-06 决策：一切以档案口径为准，不省略不加工）。
3. **只填空**：主档字段已有内容（手填或历史写入）时跳过；`--overwrite` 才覆盖。
4. **内容可含全角括号**：解析按「标签（状态）：」前缀锚定行首，内容里的
   `（）` 不影响截取（self_test 有断言覆盖）。
5. **与流水线一致性**：`SECTION_LABELS` 必须与 `sales-call-analysis-scripted/scripts/pipeline.py`
   的 `PROFILE_SECTIONS` 完全一致，由 self_test.py 自动断言。

## 目标 Base（config.local.json）

- Base `GoKJbQJhnak3nCs82Jnciuv3nNb`（正式生产表，即分析流水线 visit_prod profile）
- 01 门店主档 `tbl6LAWVHP2E9nX7`
- 02 门店拜访记录 `tblx9Hjo1mxrBMiw`
