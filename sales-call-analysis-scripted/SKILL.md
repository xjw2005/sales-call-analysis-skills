---
name: sales-call-analysis-scripted
description: 用无状态脚本流水线批量分析带时间戳、已标注销售/客户/旁人的ASR销售录音，生成显隐需求、九类客户关心点、合作进展打分评估、整理后的销售金句、门店档案和下一步行动策略，并经原话回查、确定性计分、实时飞书schema校验、风险模块复跑/第三裁决后安全回填飞书多维表格。用于“批量分析销售录音”“跑销售话术流水线”“回填关心类目/显隐需求/合作进展打分/核心金句/门店档案/下一步行动策略”“rescue失败记录”等场景。
---

# 销售录音分析脚本流水线

把执行本 Skill 的 Agent 当作调度员：启动脚本、查看结构化汇总、处理最终残留；不要把多条客户原文批量读入对话上下文。

## 核心原则

- 每条录音使用全新模型调用，不跨记录共享客户信息。
- 七个模块必须分别使用专属提示词和独立 API 请求；每个请求都重新提供同一份完整录音转写，禁止合并请求。
- 模型只引用确定性的发言 ID；原话和时间戳由脚本从转写中提取。
- 首轮代码硬校验不通过或写后复读不一致时，不写对应模块。
- 风险复核只比较证据、类目状态、评分和档案状态等业务判断骨架，不比较标题、解释和润色文字；默认复核未收敛时保留首轮硬校验合格结果并记录警告，`--review-policy strict` 才阻止写回。
- 默认只填完全为空的模块；部分已有内容进入人工清单，除非显式指定覆盖。
- 门店档案是单次录音快照，不跨录音合并，不把当前状态写成永久属性。
- 门店档案固定输出九个维度：一句话画像、门店基本信息、主营品类与品牌、经营模式、选品偏好、利润偏好、合作偏好与排斥项、关键证据、档案口径；不得增减。
- store-profile 分析时会从 01 门店主档读取门店地址（`address_fields` 配置的字段，如 `门店所在省`/`详细地址`），作为可信已知事实注入提示词：地址可直接写入门店基本信息，无需录音证据支持（来源为主档清单），写入时 `state_type` 用 稳定档案、证据留空；该门店无关联主档或地址为空时不注入。
- store-profile 分析时还注入「门店档案纠正（dsr填写）」（`correction_field` 配置）：DSR 人工填写的门店事实补充与修正，权威性高于现场速记与转写，冲突时以纠正为准；纠正中的行动推进类信息不属于档案维度，不写入档案正文；字段为空时不注入。
- 下一步行动策略先读取飞书`是否达成合作`单选字段作为唯一合作状态来源，再按未合作/已合作两套规则分析完整对话；模型不得自行改判合作状态，飞书正文不重复展示该控制字段。
- 下一步行动策略先判断`触发/不触发/待验证`，只把对话结束后仍待执行的事项列为“已确认的后续事项”；现场已完成、正在执行或客户仅简单附和的动作不重复列入。建议动作包含事项主题、责任人、时间框架和验收标准；证据仅用于内部校验，飞书正文不展开时间戳和原话。

运行前按需读取 [输出与校验规范](references/output-spec.md)。修改模型提示词时读取 `references/prompts/` 中的通用规则和对应模块提示词。

## 标准流程

1. 读取 `config.local.json` 的 profile，实时检查 Base、表、视图、字段类型和关心类目选项。
2. 拉取视图记录，先过滤空转写；按模块检查目标字段是否为空。
3. 每条记录按七个模块分别调用：显性需求、隐性需求、九类关心点、销售对话效能、销售金句、门店档案、下一步行动策略。每次调用都输入完整录音，但只解决一个任务。
4. 单个模块校验失败时，仅用该模块的专属提示词修复一次；其他模块结果不受影响。
5. `--review flagged` 下只复跑高风险模块；业务判断骨架不一致时执行第三次独立分析，第三次与前两次之一一致则按多数结果处理。
6. 仅把通过校验的字段按 `record_id` 更新回原记录，随后逐字段复读。
7. 实时查看 `runs/<时间戳>/events.jsonl` 的模块级状态；完成后查看 `done.jsonl`、`manual.jsonl`、`errors.jsonl` 和 `run_manifest.json`。
8. 用 `--rescue latest` 只重处理上一轮首轮硬校验失败模块；默认同一根运行只允许一轮 rescue，仍失败则转人工。人工结果仍须通过同一校验器。

## 常用命令

```powershell
# 本地文件预览，不写飞书
python scripts/pipeline.py --input-file "<带角色转写.md>" --modules all --review off

# 视图试跑
python scripts/pipeline.py --profile default --view <view_id> --count 2 --dry-run --review flagged --workers 2

# 只检查schema、空记录和待处理模块，不调用模型
python scripts/pipeline.py --profile default --view <view_id> --preflight

# 正式补空字段（默认16个“录音×模块”任务并发）
python scripts/pipeline.py --profile default --view <view_id> --modules all --review flagged

# 严格模式：复核未收敛时转人工且不写回
python scripts/pipeline.py --profile default --view <view_id> --modules all --review flagged --review-policy strict

# 仅处理指定的多条记录
python scripts/pipeline.py --profile default --view <view_id> --record-ids <id1,id2,id3> --modules all --review flagged

# 只处理部分模块；needs 是显性+隐性的便捷别名，但仍会发出两次请求
python scripts/pipeline.py --profile default --view <view_id> --modules needs,store-profile --dry-run

# 显式覆盖指定模块
python scripts/pipeline.py --profile default --view <view_id> --overwrite needs,store-profile

# 清理上一轮残留
python scripts/pipeline.py --profile default --view <view_id> --rescue latest

# 只有确认额外模型消耗后，才显式提高同一根运行的rescue预算
python scripts/pipeline.py --profile default --view <view_id> --rescue latest --max-rescue-rounds 2
```

七个实际请求模块：

- `explicit-needs`：显性需求(仅供参考)、显性需求_原句参考
- `implicit-needs`：隐性需求(仅供参考)、隐性需求_原句参考
- `concerns`：关心类目打标、场景化类目归因
- `effectiveness`：合作进展打分评估、原句参考_合作进展打分评估
- `quotes`：核心金句
- `store-profile`：门店档案
- `next-action`：下一步行动策略

`needs` 只是命令行别名，会展开成 `explicit-needs,implicit-needs`，不会合并为一次模型请求。

## 执行边界

- 输入必须包含时间戳和 `销售/客户/旁人`角色；只有 speaker ID 时先使用 `classify-sales-call-roles`。
- 不自动创建、删除或改名飞书字段，不新增关心类目选项。
- `显性需求(仅供参考)`和`隐性需求(仅供参考)`是AI辅助判断字段，不代表销售人员已经人工确认；流水线不得把结果写入未来可能新增的销售手填正式字段。
- `下一步行动策略`是AI跟进参考，不作为考核或追责依据；未合作门店输出给区域经理的沟通优化建议和二次拜访价值，已合作门店输出后续服务需求、服务动作与具体跟进时间。
- 不把“未打断、未质疑、沉默”当作认同证据。
- 不输出显性/隐性需求的“建议验证问题”。
- 销售效能等级阈值只用于代码计算，不在飞书正文展示规则版本或阈值。
- 默认模型为 `deepseek-v4-pro`，默认 `workers=16`；并发单元是“record_id × module”，所以单条录音的七个模块也可并发。风险复跑/第三裁决仍在各自模块内部顺序执行，写同一张表仍串行。
- 单模块首轮最多2次模型调用；风险双跑与第三跑各自最多2次。默认复核不收敛会写入首轮合格结果并产生 `review_fallback_primary`，不会无限循环；严格模式产生 `review_disputed` 并停止该模块。
- `events.jsonl` 实时记录每个模块的 `queued → started → primary_validated → ready_to_write → written`，或 `validation_failed/review_fallback_primary/review_disputed/exception`，不必等待整条录音结束才能定位进度。
- `--rescue` 不自动递归；跨进程读取 `run_manifest.json` 累计轮次，默认同一根运行最多1轮。提高预算必须显式传入 `--max-rescue-rounds`。
- `核心金句`基于连续销售原话删除语气词、重复词并轻度梳理语序，禁止新增事实或改变原意；`话术亮点`和`谈判逻辑链`保持不动。
- 写同一张表时串行提交；分析可以并发。

## 知识库注入

分析时可为模块注入「项目知识库」的通用分类参考（a2 品牌方知识库：法规/产品/竞品/业务规范/类目/评分/组织/门店档案），帮助模型理解与归类；知识**不是本条录音的事实**，注入块守卫句明确禁止据此补写录音未提及的事实。

- **知识库位置**：`config.local.json` 的 `knowledge_root`（指向 Obsidian 仓库 `knowledge/a2/`，a2 品牌方专用；当前设计全部针对 a2）；缺失或目录不存在时注入自动关闭，行为与未建知识库完全一致。
- **每模块消费什么**：`references/knowledge/manifest.json`（`file` = 该文件下全部 active 原子条目 / `entry` 单条 / `query` 按标签过滤，预留向量检索）；`max_tokens` 为每模块注入预算。新内容打对 `用途:` 标签（如 `用途:打标`、`用途:指派`）自动进对应模块注入集。
- **注入位置**：`system_prompt(module)` 的通用规则与模块提示词之间，同一模块跨记录一致，利于前缀缓存。
- **引用可追溯**：模型可写 `知识#<id>`；引用必须命中本次实际注入的条目，否则触发重试/人工兜底；引用全量记入 `done.jsonl` 的 `knowledge_refs`，飞书正文剥离引用标记（内部审计留痕）。
- **格式与生命周期**：见知识库 `README.md`（原子条目 = 未来 RAG chunk）。

常用命令：

```powershell
# 知识库一致性自检（独立入口，不调用模型）
python scripts/knowledge_check.py

# 流水线自带的等价检查
python scripts/pipeline.py --knowledge-check

# preflight 会打印每模块「将注入 N 行 / 约 M token」
python scripts/pipeline.py --profile default --view <view_id> --preflight

# 本运行禁用知识库注入（对比实验）
python scripts/pipeline.py --profile default --view <view_id> --no-knowledge --dry-run
```

## 自检

```powershell
python scripts/self_test.py
python -m py_compile scripts/pipeline.py scripts/knowledge.py scripts/knowledge_check.py
python scripts/knowledge_check.py
```
