---
name: lark-sales-audio-asr-scripted
description: 从飞书多维表格附件字段批量下载销售录音，按费用确认门禁调用腾讯录音文件识别极速版或火山 LAS，保存原始结果，自动归类销售/客户/旁人并将带时间戳的完整业务角色转写安全回填原记录。用于“批量转写飞书录音”“切换腾讯 ASR”“异步跑 LAS”“给 speaker 标销售客户身份”“把完整对话写回多维表”等场景。
---

# 飞书销售录音转写流水线

把执行本 Skill 的 Agent 当作调度员：运行脚本、报告预估费用、等待用户确认、续查异步任务和处理残留；不要把多条完整录音载入对话上下文。

## 核心原则

- ASR 提供方由 `config.local.json` 的 `asr_provider` 决定：`tencent_flash` 或 `volc_las`。
- 腾讯默认使用可由普通极速版免费包抵扣的中英粤引擎 `16k_zh-PY`；火山固定使用 `las_asr_seed-2-0-lite` 和接口版本 `v1`。
- 腾讯 SecretId/SecretKey 只从 `TENCENT_ASR_SECRET_ID`、`TENCENT_ASR_SECRET_KEY` 读取；火山 Key 只从 `LAS_API_KEY` 读取。密钥不写配置、日志、飞书或命令参数。
- 未获得用户对本批次预估金额的明确确认，不允许 Submit。
- ASR 原始 speaker ID 与业务角色映射分开保存；角色修正不重跑 ASR。
- 下载、上传、Submit、Poll 和角色分析可并发；同一张飞书表的写回必须串行。
- 目标文本非空时默认跳过；写回后逐条复读一致才完成。
- 写回时自动初判「是否有效对话」（两层：时长硬门禁 + 模型内容判断），仅在字段为空时写入，人工改过不覆盖。

需要理解状态文件和失败恢复时读取 [执行与状态规范](references/workflow.md)。

## 标准流程

1. 运行 `--preflight`，实时检查 Base schema，列出“有录音、无转写”的记录。
2. 运行 `--prepare`，下载附件、检测时长、生成批次金额和本地状态；不提交 ASR。
3. 向用户报告 `时长秒数 ÷ 3600 × 当前提供方预估单价` 的金额并暂停。
4. 用户明确确认后，用完全一致的金额运行 `--submit-run`。腾讯极速版最多按配置的 5 路同步并发识别，完成后直接进入角色层；火山并发上传和 Submit，持久化 Task ID。
5. 仅火山任务需要用 `--poll-run` 做一次并发查询；可重复执行，不无限等待。
6. 完成后自动保存原始 JSON。auto 多方言模式先逐句忠实转换为普通话，再生成全局 speaker 画像和时间窗漂移候选，创建 role-map，执行映射后语义审计；必要时只修复冲突窗口，最终生成业务角色转写。
7. 非 `--dry-run` 时串行写回 `完整对话-文字` 并复读。
8. 查看运行目录的 `state.json`、`events.jsonl`、每条记录产物和错误。

## 常用命令

```powershell
# 安全配置腾讯凭证（请使用轮换后的新密钥；SecretKey 输入不回显）
powershell -ExecutionPolicy Bypass -File scripts/configure_tencent_credentials.ps1

# 只读预检
python scripts/pipeline.py --profile default --view <view_id> --preflight

# 下载并估价，不提交付费任务
python scripts/pipeline.py --profile default --view <view_id> --prepare --workers 4

# 中文多方言自动识别、最终输出普通话；仍然只下载和估价
python scripts/pipeline.py --profile default --view <view_id> --prepare --workers 4 --asr-language auto --output-language zh-CN

# 精确锁定多条记录，避免视图排序变化影响范围
python scripts/pipeline.py --profile default --view <view_id> --record-ids <id1,id2,id3> --prepare --workers 4 --asr-language auto --output-language zh-CN

# 用户确认金额后识别；腾讯会同步完成识别、角色归类和写回，火山只提交异步任务
python scripts/pipeline.py --submit-run "<run_dir>" --confirm-yuan <预估金额> --workers 4

# 仅火山：并发查询一次；完成项自动做角色归类并写回
python scripts/pipeline.py --poll-run "<run_dir>" --workers 6

# 只用已有 ASR 结果预览新版角色识别，不重跑 ASR、不写飞书
python scripts/pipeline.py --relabel-run "<run_dir>" --record-ids <id1,id2> --workers 4 --dry-run

# 备份旧角色产物并正式覆盖角色转写；必须显式确认 overwrite
python scripts/pipeline.py --relabel-run "<run_dir>" --record-ids <id1,id2> --workers 4 --overwrite

# 只生成本地结果，不写飞书
python scripts/pipeline.py --poll-run "<run_dir>" --workers 6 --dry-run
```

## 默认飞书映射

- 输入附件：`录音文件`
- 输出文本：`完整对话-文字`
- 行标识：`门店编号`、`门店名称`、`门店联系人`

字段映射由 `config.local.json` profile 决定。脚本不自动建字段、删字段或改字段类型。

## 依赖

- `lark-cli`：读取记录、下载附件、写回并复读。
- 腾讯极速版只使用 Python 标准库发送同步 HTTPS 请求，不需要额外 SDK。
- `lasutil`：仅火山提供方用于上传、Submit、Poll；可通过 `LASUTIL_PATH` 指定。
- `mutagen`：准确读取 MP3/M4A 时长，避免多时间轴容器造成估价偏差。
- `classify-sales-call-roles`：生成全局画像、时间窗漂移候选、role-map、映射后审计和确定性角色转写。
- 文本模型配置：只读取紧凑画像及审计冲突窗口来生成或修复 role-map，不逐句读取整篇转写。
- 方言转普通话：文本模型逐句转换完整方言 ASR；保留原始 JSON、时间戳和 speaker ID，并对 ID 完整性与标签不变性做代码校验。

## 执行边界

- 一个单元格包含多个附件时默认进入错误清单，不猜选哪一个。
- 下载后保留原附件名作元数据，本地统一使用 `audio.<扩展名>`，避免中文或特殊文件名影响下载和后续工具。
- `PENDING/RUNNING` 是正常状态；不得当作失败或完成，也不得无限轮询。
- `FAILED/TIMEOUT` 记录业务码和错误，不写空文本。
- 不覆盖已有 `完整对话-文字`，除非显式使用 `--overwrite`。
- 不把 speaker 0/1 直接等同于销售/客户。
- 腾讯 `16k_zh-PY` 会请求开启说话人分离；单 speaker 录音仍进入语义角色归类和审计，不直接把 speaker 0 等同于销售。
- 腾讯请求前默认将附件标准化为 16kHz、16bit、单声道 WAV；保留原始录音，不改变计费时长。
- 腾讯免费大模型并发默认按 5 路封顶；`--workers` 高于 5 不会突破账户并发限制。
- 腾讯同步请求发出后如果网络中断，状态记为 `asr_uncertain`，禁止自动重试，先核对调用记录以避免重复计费。
- 腾讯原始响应已成功保存后，如本地转换或角色层失败，状态记为 `result_error`，只修复本地层，不重跑 ASR。
- 不假设 speaker ID 在长录音中始终代表同一个人；审计仍有严重冲突时停止写回并只重试角色层。
- 当前固定使用 2026-07-28 已成功验收的请求参数组合；VAD、静音窗口和热词上下文不得未经单条验证直接加入整批请求。
- 识别后产品映射纠错属于独立后处理，不做无边界替换。
- auto 模式不发送 `audio.language`，由中文大模型识别普通话及所支持方言；纯粤语可显式使用 `yue-CN`。
- 方言转普通话失败时不写回飞书，只保留原始方言 ASR 供重试。
- 「是否有效对话」判定：录音时长（音频元数据优先，转写末行时间戳兜底）不足 `valid_min_seconds`（**当前配置 120 秒**，用户 2026-08-11 决策从 180 降到 120；config.local.json 为准）直接「录音过短」；达到时长后由文本模型读完整转写判断内容是否属于商业场景、有无记录价值，输出 有效/内容无效 与理由（判定详情记入 `events.jsonl`）；模型失败时保守判「内容无效」（人工可在飞书改回）。字段三态：有效/录音过短/内容无效。

## 自检

```powershell
python scripts/self_test.py
python -m py_compile scripts/pipeline.py scripts/tencent_flash.py
```

计费声明：本方式的计费均为预估计费，与实际费用有差距，实际费用以对应云厂商账单为准。腾讯计费说明见 [腾讯云语音识别计费概述](https://cloud.tencent.com/document/product/1093/35686)，火山计费说明见 [Volcengine LAS 定价](https://www.volcengine.com/docs/6492/1544808)。


## 执行注意事项与坑（多次实战踩坑沉淀，勿重犯）

### 环境与密钥
- **定时任务环境变量**：Task Scheduler/cron 启动时不继承用户 shell 环境，`LAS_API_KEY`/`TENCENT_ASR_SECRET_ID` 读不到会导致任务直接中止。兜底方案：启动脚本前显式加载环境（从用户级环境变量或受保护文件），**密钥绝不写入任何脚本、配置、日志或仓库**。
- 付费门禁（红线）：`--prepare` 报预估金额后必须等用户明确确认，`--submit-run` 用相同金额提交；金额不一致会拒绝。

### 命令行长度（#9）
- 超长转写写回失败根因：lark-cli 若是 `.cmd` 包装，参数经 cmd.exe 上限 8191 字符。`resolve_lark()` 会解析 .cmd 里的 `.exe` 路径直调（32767 上限），**先确认 LARK 解析为 .exe**；解析失败时超长文本（>5000 字符）会写回失败。逐字段写（每次只传一个字段的 JSON）可进一步降低单次参数长度。

### relabel 与有效性（#10）
- `--relabel-run` 重新角色识别后**自动补判「是否有效对话」**（字段空才写，人工改过不覆盖）；早期版本没有此步骤，relabel 过的记录有效性为空。若遇旧数据有效性空，用 `judge_validity` + 直接 `+record-upsert` 补判（注意 `write_validity` 只填空，重判已有值必须直接 upsert，否则静默不写）。

### 并行会话冲突
- 多个会话/Agent 同时跑本流水线会互相干扰：prepare 下载的记录可能被另一会话先转写写回，本会话写回时报「目标字段已经有内容」（write_error 无害，飞书内容即同一批转写）；但**有效性判定不会执行**，事后需给这些记录补判。同一批数据只由一个会话跑。
- robocopy 备份时 Git Bash 循环里 exit=16 是假失败，单条跑并用 git status 核实。

### 有效性三态
- 字段三态：有效 / 录音过短 / 内容无效。时长硬门禁（<120 秒）→ 录音过短；模型读转写判内容 → 有效/内容无效；模型失败保守判内容无效（fail-closed，人工可改回）。判定详情记 `events.jsonl`。


## 部署（新机器从零安装）

完整步骤见 [references/deployment.md](references/deployment.md)（本 skill 与 `sales-call-analysis-scripted` 配套部署）。要点：

- **依赖**：las_sdk（火山 LAS CLI，TOS wheel 安装）、mutagen（时长估价）、ffmpeg（腾讯方案用）；lasutil 在 `<python>\Scripts\`，需用 `lasutil_path` 或 `LASUTIL_PATH` 指定完整路径。
- **环境变量（6 个，用户级 setx）**：`LLM_API_URL` / `ARK_API_KEY` / `LAS_API_KEY` / `LASUTIL_PATH` / `TENCENT_ASR_SECRET_ID` / `TENCENT_ASR_SECRET_KEY`。**setx 只影响新进程**——WorkBuddy 主进程在 setx 前启动时，所有子进程（含定时任务）都读不到，这是定时任务失败最常见原因。
- **定时任务兜底**：脚本执行前先 `source env-loader.sh`（从注册表 HKCU\Environment 读取并 export 6 个变量，不硬编码密钥）；定时任务 prompt 第一步必须是 source 它；长期治本可重启 WorkBuddy 让主进程继承。
- **凭证不入库**：`role-llm.json` 的 api_key 留空，走环境变量；config.local.json 含表 ID 不提交到公开仓库。
