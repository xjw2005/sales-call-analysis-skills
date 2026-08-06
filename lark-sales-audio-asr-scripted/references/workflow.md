# 执行与状态规范

## 状态流

火山异步链路：

`prepared → submitted → pending/running → completed → role_labeled → written`

腾讯极速版同步链路：

`prepared → role_labeled → written`

失败状态：

- `prepare_error`：附件下载或时长检测失败。
- `submit_error`：上传或 Submit 失败；没有 Task ID 时可重试 Submit。
- `submit_uncertain`：Submit 已开始但没有拿到明确响应；禁止自动重提，先在 LAS 控制台核对 Task ID，避免重复计费。
- `asr_failed`：LAS 返回 FAILED/TIMEOUT；禁止自动重新 Submit。
- `asr_error`：腾讯在返回明确失败信息后停止；可在确认未成功识别后重试。
- `asr_uncertain`：腾讯请求已发送但响应状态未知；禁止自动重试，先核对调用记录和账单。
- `result_error`：腾讯原始响应已成功保存，但本地统一转换或角色处理失败；只修复本地层，禁止重跑 ASR。
- `role_error`：角色映射或转写验证失败；只重跑角色层。
- `write_error`：飞书写回或复读失败；只重跑写回。

## 幂等规则

- `state.json` 是批次状态 SSOT；火山每个 record_id 只对应一个活动 Task ID，腾讯每个 record_id 只允许一次已确认成功的极速版请求。
- 火山已有 Task ID 的记录禁止再次 Submit；腾讯已有 `tencent-response.json` 的记录禁止再次请求 ASR。
- Poll 只用于火山，可重复执行；终态记录不重复请求 LAS。
- `role_labeled` 和 `written` 记录可从本地产物恢复，不重跑 ASR。
- 已完成记录需要升级角色映射时使用 `--relabel-run`；`--dry-run` 写入本地 `role-preview/`，正式回写必须显式 `--overwrite`，旧角色产物备份到 `role-history/`。
- 写回前再次读取目标字段；非空且未指定 overwrite 时停止该记录。

## 运行产物

```text
runs/<timestamp>/
├── state.json
├── events.jsonl
└── records/<record_id>/
    ├── audio.<ext>
    ├── submit.json                 # 火山
    ├── tencent-response.json       # 腾讯原始响应
    ├── result.json
    ├── role-profile.md
    ├── role-map.initial.json
    ├── role-map.json
    ├── role-audit.json
    └── labeled-transcript.txt
```

## 验收

- 火山 Submit 返回非空 Task ID，Poll 最终为 COMPLETED 且 business_code 为 0。
- 腾讯返回 code=0、非空 request_id，并将原始响应单独保存。
- `data.result.text` 与 `data.result.utterances` 非空。
- utterance 包含可解析的起止时间和 speaker。
- 多 speaker 录音必须保留全部 speaker ID；单 speaker 录音仍需通过语义角色归类与审计，不直接假定业务身份。
- 角色画像已检查时间窗漂移；`role-audit.json` 不得保留严重语义冲突。
- 最终文本每行均为 `[时间–时间] 销售|客户|旁人：原文`。
- 最终文本无 `[spkN]` 残留，时间戳不倒退。
- 写回后复读与本地文本完全一致。
