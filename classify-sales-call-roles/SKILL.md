---
name: classify-sales-call-roles
description: 将带时间戳和 speaker ID 的销售拜访、电话录音或客户沟通 ASR 结果稳健归类为“销售、客户、旁人”，支持同一角色被拆成多个 speaker、同一 speaker 在长录音中发生身份漂移、场景切换、多人插话和电话转场；通过全局画像、时间窗漂移检测、segment 覆盖、映射后语义审计和定向修复生成可追溯业务角色转写。用于区分销售与客户、批量标注角色、修复 speaker 聚类漂移或为销售录音分析准备可靠角色输入。
---

# 销售通话角色识别

## 核心原则

- 不把完整转写交给文本模型逐句判断。
- 先用脚本压缩出每个 speaker 的统计和代表发言，再判断少量 speaker-to-role 映射。
- 不假设 speaker ID 全程稳定；长录音必须检查时间窗内的角色漂移。
- 只让模型输出全局映射和时间段覆盖；用脚本给全部发言添加标签。
- 应用映射后做确定性语义审计；只把冲突窗口交给模型修复，不逐句调用。
- 严重冲突未收敛时停止写回并标记 `role_error`，不把明显错误的角色转写交给下游分析。
- 只输出 `销售`、`客户`、`旁人` 三种角色。

## 工作流

1. 如果输入还是飞书多维表格中的音频，先使用 `lark-sales-audio-asr-scripted` 获取含时间戳、utterances 和 speaker ID 的 JSON；本 Skill 只处理 ASR 结果与角色映射。
2. 运行画像脚本，不要先把完整 transcript 读入上下文。画像包含全局 speaker 统计、代表发言和 5 分钟时间窗漂移候选：

```powershell
python scripts/build_role_profile.py <asr-result.json> --output <role-profile.md>
```

3. 只读取生成的 `role-profile.md`。按以下顺序判断：
   - 找出介绍公司、平台、产品、政策、合作方案或持续推进成交的人，标为 `销售`。
   - 找出代表门店或购买方讨论顾客、库存、利润、进货、经营困难和决策的人，标为 `客户`。
   - 把店员、陪同者、短暂插话者、电话另一端的非主要客户及其他参与者标为 `旁人`。
   - ASR 可能把同一人拆成多个 speaker ID；允许多个 ID 同时映射为 `客户` 或 `销售`。
4. 检查 `Temporal role drift candidates` 和场景边界。同一 speaker 在不同时窗出现相反的强销售/客户证据时，必须增加 segment 覆盖；普通短暂停顿不创建 segment。
5. 写出 `role-map.json`。完整格式见 [role-map-schema.md](references/role-map-schema.md)。
6. 在生成最终文本前审计映射：

```powershell
python scripts/audit_role_map.py <asr-result.json> --role-map <role-map.json> --output <role-audit.json>
```

7. `role-audit.json` 存在冲突窗口时，只把 speaker 画像、当前映射和冲突窗口交给角色模型修复，最多两轮；每轮必须使 `conflict_windows` 确定性下降。严重冲突仍未收敛时停止。
8. 批量生成最终文本：

```powershell
python scripts/apply_role_map.py <asr-result.json> --role-map <role-map.json> --output <labeled-transcript.txt>
```

9. 验证输出只含三种角色、无 `[spkN]` 残留、时间戳递增，且最终 `role-audit.json` 不含严重冲突。需要写回飞书多维表格时，再组合使用 `lark-base`。

## 自动兜底

角色模型不可用时使用确定性兜底：

1. 将销售证据分最高的 speaker 设为 `销售`。
2. 在其余 speaker 中，将客户证据分最高且发言时长较长、包含经营或决策表达的 speaker 设为 `客户`。
3. 其余 speaker 设为 `旁人`。
4. 对兜底映射运行同一语义审计；若仍为严重冲突，输出 `role_error`，只重试角色层，不重新提交 ASR。

## Token 限制

- 默认每个 speaker 最多保留 8 条代表发言。
- 画像总长度默认不超过 10000 字符；时间窗部分只保留发生角色冲突的 speaker 和少量强证据。
- 不让模型重写带角色的完整 transcript。
- 不按 utterance 逐条调用模型；初始映射一次，必要时最多两次冲突修复。
- 如果画像仍无法判断，只允许使用 `role-audit.json` 中的冲突窗口；禁止回退到整篇全文。

## 资源

- `scripts/build_role_profile.py`：压缩完整 ASR，输出 speaker 画像、证据分和场景边界候选。
- `scripts/audit_role_map.py`：按时间窗审计映射后的角色语义冲突，输出可机器判断的质量结果。
- `scripts/apply_role_map.py`：确定性应用角色映射与时间段覆盖，生成带时间戳的完整文本。
- [role-map-schema.md](references/role-map-schema.md)：输入格式、映射格式和示例。
- [role-prompt.md](references/role-prompt.md)：只生成/修复角色映射的专属提示词。
