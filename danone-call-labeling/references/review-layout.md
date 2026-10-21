# 四列复核展示

每个业务字段后紧跟三列：

| 字段 | 类型 | 内容 |
|---|---|---|
| 原业务标签 | 保持现有多选 | 仅通过校验且无需复核的选项 |
| 判断状态·原字段名 | 单选 | 有原话依据、规则默认、待复核、未输出、技术失败 |
| 复核原因·原字段名 | 多选 | 身份冲突、产品不清、回答含糊、证据不足、规则争议、技术异常 |
| 依据校验·原字段名 | 文本 | 客户连续原话、起止时间和必要的一个问题上下文 |

仅待复核或技术失败填写原因。“未输出”与“待复核”分开；空标签不证明客户没有问题或没有需求。规则默认值不伪造原话，因此依据可为空。技术失败字段必须留空且列入复核。

原因按已保存的复核说明确定性归类，不另调模型。依据不再重复状态、候选、详细推理和错误日志；原句匹配通过不等于人工确认语义正确。身份冲突保留整句，避免裁掉同一句中的相反表述；短答保留必要问题。复核说明中的引文只有唯一匹配客户原句时才能恢复为依据。

## 本地导出

以下入口只生成schema或更新草稿；不创建字段、不改视图、不写记录、不调用模型：

```sh
python danone-call-labeling/scripts/review_layout.py schema --project p3m
python danone-call-labeling/scripts/review_layout.py export --table-id <已绑定副本表ID> --schema runs/fields.json --input runs/review-bundles.ndjson --view-fields runs/visible-fields.json --output runs/review-draft.json
python danone-call-labeling/scripts/review_layout_test.py
```

`--schema` 为完整 `lark-cli base +field-list --limit 200 --format json` 成功响应；省略时只读查询。`--view-fields` 可省略，提供时可为当前可见字段名数组或 `+view-get-visible-fields` 成功响应。已有可见业务字段才插入三列，不自动显示原本隐藏的业务字段；其他列保留原有相对顺序。

输入每行一个对象：

- `source`：原始通话对象（id、project、transcript_complete、带起止时间的transcript）；或 `{record: <normalize后的通话>, times: {话语ID: "00:01.000–00:03.000"}}`。
- `analysis`：当前规则版本的 `pipeline.py` 结果（id、standard_sha256、result等）。id必须与source一致。
- `technical_errors`：可选，`{完整字段名: 已保存的错误说明}`；对应字段的标签必须为空且在review_items中，不能借此掩盖非空错误标签。

导出前重新校验项目、记录ID、规则版本、合法选项、客户连续引文、时间戳及展示字段类型/选项。待复核字段不导出业务标签；忽略外部提供的business_fields，按校验后的labels重新生成。不会输出清空业务标签的操作；展示元数据会用空文本/空数组清掉旧的冗余说明和过期原因。

## 实际应用草稿时

字段需由已授权的 Base 操作创建并核对；新列名称以中文前缀开头，避免被识别为额外A/B/C业务标签。保存已存在字段与视图配置，按真实字段ID回填，同一表串行写入，写后读回。

大字段集合每次投影不超过40列，并核对各次读取的记录ID集合；field-list显式读取到完整schema（最多200字段，更多时需处理分页）。文本清空使用空字符串、多选使用空数组并读回，不能只根据成功返回认定清空完成。单选读回可能是单元素数组，按字段类型比较。再次写回前须确认源转写未变化；不要覆盖人工改过的标签或未经用户授权清空旧值。

这些草稿不能替代实际应用后的读回验证。导出本身不应触发费用或给客户标记“已人工确认”。
