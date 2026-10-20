# 电销转写配置与运行

本模式处理门店政策回访、SPC调研、领熠/亲熠分销录音。只产出原话转写、时间戳、渠道专员/客户/旁人角色，以及可选的时长和状态；不会调用 `danone-call-labeling` 或改动人工业务标签。业务打标仍是独立入口。

## 配置

复制 `config.telesales.example.json` 为自己的 `config.<项目>.local.json`，复制 `role-llm.example.json` 为 `role-llm.local.json`。这些新增本地配置已被 Git 忽略；仓库原有已跟踪的 `config.local.json` 不受影响。

- 三个示例 profile 为 `p3m`、`lyqy`、`crown`。先读取实际表和字段，再填写 Base、表、视图 ID；不猜ID，不自动建表或改列顺序。
- `scenario: telesales` 启用电销画像和审计。老板、店长、代接并回答业务问题的店员都可为客户；提问、复述和谈及线下销售不能直接证明身份。
- `role_display_names: {"销售":"渠道专员"}` 只替换转写行首角色，不替换发言中提及的“销售”。内部 role-map 保持兼容格式；不猜测真实姓名。
- `hotword_fields: ["门店名称"]` 将本条元数据实际送入 `request.corpus.context`；可通过 `record_hotwords` 按记录ID补充受控别名。公共词与本条门店词去重，不能带入其他记录的门店，也不能据此补写音频没说出的全称。
- `asr_request` 覆盖请求开关：示例开启标点、数字规范化、speaker 和逐句起止时间，关闭 DDC。Seed 算子的默认精修与 DDC 是两项能力；关闭 DDC 不代表关闭精修。
- `punctuation_only: true` 在角色转写后只插入标点和空格；代码校验不改字、不删词、不改数字，撤回新增在否定词内部的断句。校验失败不写回，保留原始ASR及角色产物供定向重试；“对啊”、犹豫和自我修正照原话保留。
- `role_llm_config_path` 可用相对于当前配置文件的路径。示例沿用项目选定的 DeepSeek 官方地址和模型，API Key 留空；接口是否支持该模型须以自己的账户为准。ASR角色模型失败不会触发业务打标。

密钥只从环境变量 `LAS_API_KEY` / `DEEPSEEK_API_KEY` 或显式 `--credentials-file <仓库外JSON>` 注入。私密JSON只含这两个键；不要把它放进提交、日志或命令参数值里。运行前检查是否继承了其他项目的 `ARK_API_KEY` / `LLM_API_URL`，这些既有环境覆盖项优先于模型配置。

## 时长与状态

`run_project.py` 在写操作后自动同步已配置的作业字段；直接运行旧的 `pipeline.py` 仍保留原行为。

- `transcript_field_id`、`duration_field_id`、`status_field_id` 优先于字段名；字段改名时按ID解析。未填ID时按精确字段名绑定实际ID。一个批次只使用自身 `profile_name` 对应的数据表；表ID不一致时拒绝写入。
- 时长写入 `duration_field` 指向的 **number** 字段，单位秒，原音频元数据四舍五入，包含等待和静音；不能向公式字段写数值。
- 如需“4分06秒”，由 Base 显示公式计算，隐藏秒数列即可：
  `IF(ISBLANK([通话时长（秒）]),"",TEXT(INT([通话时长（秒）]/60),"0")&"分"&TEXT(MOD([通话时长（秒）],60),"00")&"秒")`
- `status_field` 必须是含“成功/失败”的单选字段。正文写入且逐字读回一致、时长与状态复读一致后才算完成；明确错误写失败，排队、处理中、提交结果未知留空。没有录音的空白行不会被创建任务或填成0秒。
- 先投影读取当前表，再串行写有变化的行，并逐条读回；已完成正文不覆盖。`--sync-run` 只补元数据，不调用ASR或模型。

## 运行和恢复

在 Skill 目录执行，例如：

```sh
python scripts/run_project.py --config config.demo.local.json --profile p3m --preflight
python scripts/run_project.py --config config.demo.local.json --profile p3m --prepare --workers 4 --asr-language auto --output-language source
# 向用户报告此run的整批估价；明确确认后再提交。金额必须与state.json一致。
python scripts/run_project.py --config config.demo.local.json --submit-run runs/<批次> --confirm-yuan <预估金额> --workers 4
python scripts/run_project.py --config config.demo.local.json --poll-run runs/<批次> --workers 16
python scripts/run_project.py --config config.demo.local.json --sync-run runs/<批次>
```

每条命令可附加 `--credentials-file <私密JSON路径>`。默认不查找个人目录，不自动读取其他项目的凭证。入口对同一run加进程锁；配置字段刷新也串行，防止两个进程并发改写。不要对同一批记录建立多个独立run。

`--workers 16` 是本地查询/角色处理任务数，不代表服务端能并发识别16条。上传、提交和查询受 `las_min_request_interval` 节流（默认每进程1秒）；LAS明确429按有限退避重试，网络中断或未知Submit结果不能自动重提。已持久化Task ID的任务仅查询，不重新提交。

模型角色或标点失败时，对原run再次 `--poll-run` 只复用原Task ID并重做下游；本地已有合格正文时只重试写回。不自动无限轮询，不承诺固定完成分钟数；取决于录音时长、服务端排队和本地运行状态。电脑合盖休眠会暂停本地查询/写回，云端任务可能继续；可在本轮运行期间防止空闲休眠，合盖前应先确认影响。

当前Seed增强算子的时长估价只覆盖基础ASR，算子默认精修和角色/标点模型另外计费，不能把基础估价当总价。原始音频、请求（移除临时下载URL）、结果、内部role-map和展示转写保留在被忽略的 `runs/` 中，成功状态不能代表人工准确率验收。
