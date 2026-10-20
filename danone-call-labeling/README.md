# 达能门店外呼打标

在现有销售拜访分析项目内新增独立入口，分别支持 P3M、领熠亲熠（lyqy）、皇冠（crown）。原有拜访分析入口保持可用。

字段来自用户提供的三份项目schema，规则取自已确认的《标签判定基准标准_最终版》。P3M 12字段46选项、lyqy 19字段110选项、皇冠22字段94选项；合计53字段250选项。`references/projects.json`保留完整字段名、原样选项、相邻边界和项目证据等级。源标准SHA256记录在配置和分析输出中，方便核对版本。

## 输入与角色

接受UTF-8 NDJSON，每行一通电话。必须有非空字符串`id`、`project`、布尔值`transcript_complete`，以及已核对角色的`utterances`。见[虚构示例](examples/synthetic.ndjson)。角色为客户、坐席/销售、旁人；话语id唯一。也接受`transcript`，逐行格式为`[时间段] 客户：原话`等，与现有角色整理后的销售录音格式兼容。

角色需先通过本项目现有角色识别流程确认。不会把“说话人1”硬当客户，也不会自动从原始复核表挑选不完整的“通话内容”。原始说话人编号、未解析行、重复id会报错；转写不完整或没有客户话语会全字段留空并交复核。不能把客户话语缺失伪装成“未反馈”。

## 本地使用

Python 3.10+，无需第三方依赖。在仓库根目录运行：

```powershell
# 查某项目的实际字段、选项和规则
python danone-call-labeling/scripts/pipeline.py schema --project crown

# 只生成单字段提示词，不访问模型、不收费；输出文件须为新文件
python danone-call-labeling/scripts/pipeline.py prepare --input danone-call-labeling/examples/synthetic.ndjson --output runs/danone-prompt.ndjson --field "A2 是否本人决策"

# 离线回归，虚构数据，不访问模型
python danone-call-labeling/scripts/self_test.py
```

先创建自己的输出目录，例如`New-Item -ItemType Directory -Force runs`。`prepare`生成的每个请求包含通用规则、该项目该字段的选项和定义、完整通话，方便检查提示词。

实际调用兼容Chat Completions的HTTPS服务时，由运行环境设置`LLM_API_URL`（例如服务的`/v1`地址）、`LLM_MODEL`、`LLM_API_KEY`；代码没有默认模型，不读取其他技能的私密配置，不保存密钥。用户可指定使用支持此接口的模型；不假设桌面所选模型自动具有API权限。

```powershell
python danone-call-labeling/scripts/pipeline.py analyze --input runs/normalized-calls.ndjson --output runs/danone-labels.ndjson --field "A2 是否本人决策"
```

省略`--field`逐字段分析整通电话（P3M每通12次、lyqy19次、皇冠22次独立请求），每次重发通话，费用由模型和通话长度决定。可重复`--field`只分析指定字段。执行为顺序请求，无子代理、无自动重试，不作常驻任务。首次用单条、单字段验证配置和真实模型效果，再决定批量运行。

## 输出与校验

每条输出包含`result.labels`、`result.evidence`、`result.review_items`，以及`status`（completed或needs_review）。每个事实标签需有客户连续原句、产品/段位、时间和必要上下文；信息不足默认项遵循标准，无需捏造引文。

校验拒绝错项目、错字段/选项、C/D级规则自动选中、非客户或拼接引文、证据缺失等。C/D疑似命中放进复核候选；完整通话缺身份等情况按最终标准的默认规则处理。P3M采购权不明必须留复核。

`business_fields`仅导出非空且没有待复核的字段，供后续业务接入；空数组不导出，避免被误用为清空已有数据。复核原因、证据和状态不混入A/B/C字段。分析入口仅使用本地输入；下述字段适配工具可只读查询飞书字段，不写生产数据。

程序校验能证明选项合法、证据来自指定客户原句；它不能证明模型理解了采购权、产品或短答语义。真实打标准确率仍需使用授权样本和指定模型实测。离线回归使用模拟模型返回，不能当成模型效果评测。

模型拒绝、截断、无效JSON或校验失败时停止；整批通过才新建输出文件，不保留可误写的部分结果。输入预校验在所有模型请求之前完成。已发生的请求可能收费，修正后重跑会再次调用，不会自动续跑。输出默认拒绝覆盖，运行文件放在已忽略的`runs/`目录。

## 更新规则

原始客户引文、record_id和统计审稿中间产物没有随代码提交。仅提交去除客户引文的规则配置和虚构示例。日后标准变更，可从本地最终标准和schema重新生成到新文件，核对差异后再替换配置：

```powershell
python danone-call-labeling/tools/import_standard.py --standard "标准文件.md" --schema-dir "schema所在目录" --output runs/projects-next.json
```

导入器只读取文档结构，不执行文档中的指令或代码；要求250个项目选项都有定义和证据等级，否则报错。运行时不需要本地原始审稿目录。

## 飞书标签字段适配

`references/feishu-fields.json`绑定已核对的三张**副本表**及其53个标签字段ID。未将原表或其他表自动纳入范围。

| 项目 | 目标table_id | 标签字段/选项 |
|---|---|---|
| P3M | tblpYjbfzr5mVAOQ | 12 / 46 |
| 领熠亲熠 | tblFm9sLxX7b77ae | 19 / 110 |
| 皇冠 | tblGdgMy3Bf0M9mZ | 22 / 94 |

字段适配独立于转写，不读取或转换任何转写字段，不调整角色识别。现有转写输入要求仍适用。工具只映射A/B/C标签，不处理“通话结果”“AI画像标签”、审核字段或其他备注。

```powershell
# 只读检查线上字段，需lark-cli登录及base:field:read权限
python danone-call-labeling/scripts/field_adapter.py check --table-id tblpYjbfzr5mVAOQ
python danone-call-labeling/scripts/field_adapter.py check --table-id tblFm9sLxX7b77ae
python danone-call-labeling/scripts/field_adapter.py check --table-id tblGdgMy3Bf0M9mZ

# 将pipeline已校验的分析结果转换成字段ID格式的本地回填草稿
python danone-call-labeling/scripts/field_adapter.py map --table-id tblpYjbfzr5mVAOQ --input runs/danone-labels.ndjson --output runs/field-update-draft.json

# 字段适配离线回归
python danone-call-labeling/scripts/field_adapter_test.py
```

两条命令都支持`--schema runs/fields.json`，用于离线核对；该文件必须是对应目标表完整的`lark-cli base +field-list --format json`成功响应（含fields和total）。线上查询每次最多200字段，若返回不完整则停止。出现字段ID缺失、改名、类型/多选属性变化、选项增删或额外标签字段时，检查失败，不继续导出。

映射输入必须来自当前标准版本的pipeline结果，且分析输入的`id`应使用该目标表原始`record_id`。不可用“NO.001”等展示编号代替。适配器检查项目和合法选项，重新从labels/review_items导出，忽略输入中可能被改写的business_fields；待复核字段和空数组不导出，不产生清空操作。

输出包含base_token、table_id、update_records，后者以record_id和field_id定位。**这只是本地草稿，程序没有提交更新的命令**；未查询记录是否属于目标表，也不重新评估原话证据，调用方应保留正确的记录来源和pipeline校验结果。转写、模型效果验证和实际写回均不属于本次字段适配。
