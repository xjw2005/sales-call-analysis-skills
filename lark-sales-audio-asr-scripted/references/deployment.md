# 部署指南

从零部署销售录音分析流水线(本 skill + 配套 skill)到新机器的完整流程。配套关系:`lark-sales-audio-asr-scripted`(ASR 转写+角色归类)→ `sales-call-analysis-scripted`(话术分析+回填)。

## 前置条件

- WorkBuddy + managed python 3.13+
- lark-cli 已登录(飞书用户身份,有 base 读写权限)
- 飞书 connector 已连接
- skill bundle zip
- ASR 凭证:火山 `LAS_API_KEY` 或腾讯 `TENCENT_ASR_SECRET_ID`+`SECRET_KEY`
- LLM 凭证:Ark `ARK_API_KEY`(火山方舟网关调 DeepSeek 模型)

## 步骤 1:安装 skill bundle

```bash
# 解压 zip,复制两个 skill 目录到用户级 skills 目录
python -c "import zipfile; zipfile.ZipFile(r'<bundle.zip>').extractall(r'<tmp_dir>')"
cp -r <tmp_dir>/lark-sales-audio-asr-scripted ~/.workbuddy/skills/
cp -r <tmp_dir>/sales-call-analysis-scripted ~/.workbuddy/skills/
# 从 config.example.json 复制 config.local.json 模板
cp ~/.workbuddy/skills/lark-sales-audio-asr-scripted/config.example.json ~/.workbuddy/skills/lark-sales-audio-asr-scripted/config.local.json
cp ~/.workbuddy/skills/sales-call-analysis-scripted/config.example.json ~/.workbuddy/skills/sales-call-analysis-scripted/config.local.json
```

## 步骤 2:安全审计

安装前扫 scripts/ 目录:subprocess 是否参数列表形式(无 shell=True)、网络是否只发往腾讯ASR官方/LLM/飞书、凭证是否从环境变量读取。详见各 skill 的 SKILL.md 核心原则。

## 步骤 3:工具依赖

```bash
PY="C:\Users\<user>\.workbuddy\binaries\python\versions\3.13.12\python.exe"
# las_sdk(火山 LAS CLI,含 lasutil)—— 不在 PyPI,用 TOS wheel
"$PY" -m pip install https://hyy-rd.tos-cn-beijing.volces.com/skills/sdk/las_sdk-0.2.0-py3-none-any.whl
# mutagen(音频时长估价)
"$PY" -m pip install mutagen
# ffmpeg(腾讯 ASR 音频标准化;火山方案不需要但建议装)
winget install --id=Gyan.FFmpeg -e --source winget --accept-source-agreements --accept-package-agreements --silent
```

lasutil.exe 装后位于 `<python>\Scripts\`,该目录不在 PATH,需在 config(`lasutil_path`)或环境变量(`LASUTIL_PATH`)指定完整路径。

## 步骤 4:环境变量(6 个,用户级)

```bash
setx LLM_API_URL "https://ark.cn-beijing.volces.com/api/plan/v3"
setx ARK_API_KEY "<ark-key>"
setx LAS_API_KEY "<las-key>"
setx LASUTIL_PATH "C:\Users\<user>\.workbuddy\binaries\python\versions\3.13.12\Scripts\lasutil.exe"
setx TENCENT_ASR_SECRET_ID "<tencent-id>"
setx TENCENT_ASR_SECRET_KEY "<tencent-key>"
```

验证:`reg query "HKCU\Environment" | grep -iE "LLM_API_URL|ARK_API_KEY|LAS_API_KEY|LASUTIL_PATH|TENCENT_ASR"`

> setx 设的用户级变量,当前已运行进程读不到。WorkBuddy 主进程若在 setx 前启动,所有子进程(含定时任务 agent)都读不到——这是定时任务失败的最常见原因。

**定时任务/agent 执行脚本前必须加载环境变量**:`. C:\Users\<user>\.workbuddy\skills\lark-sales-audio-asr-scripted\env-loader.sh`(从注册表 HKCU\Environment 读取并 export)。该脚本随 skill 安装,已验证可加载 6 个变量。定时任务 prompt 的第一步必须是 source 它。长期治本也可重启 WorkBuddy 让主进程继承。

## 步骤 5:config 配置

### lark-sales-audio-asr-scripted/config.local.json

```json
{
  "asr_provider": "volc_las",
  "lasutil_path": "C:\\Users\\<user>\\.workbuddy\\binaries\\python\\versions\\3.13.12\\Scripts\\lasutil.exe",
  "operator_id": "las_asr_seed-2-0-lite",
  "operator_version": "v1",
  "region": "cn-beijing",
  "rate_yuan_per_hour": 1.6,
  "tencent_flash": { "app_id": "<腾讯AppID>", "engine_type": "16k_zh-PY", "rate_yuan_per_hour": 3.1, "max_concurrency": 5, "normalize_audio": true, "speaker_diarization": 1 },
  "role_llm_config_path": "C:\\Users\\<user>\\.workbuddy\\skills\\lark-sales-audio-asr-scripted\\role-llm.json",
  "hotwords": ["小安素", "全安素", "亲舒"],
  "profiles": {
    "default": {
      "base_token": "<base_token>", "table_id": "<02表table_id>", "view_id": "<view_id>",
      "attachment_field": "录音文件", "transcript_field": "完整对话-文字",
      "identity_fields": ["门店编号", "门店名称"],
      "valid_field": "是否有效对话", "valid_min_seconds": 60
    }
  }
}
```

切腾讯:`asr_provider` 改 `"tencent_flash"`,`lasutil_path` 留空,填 `tencent_flash.app_id`。

### lark-sales-audio-asr-scripted/role-llm.json(角色归类 LLM)

```json
{ "api_url": "https://ark.cn-beijing.volces.com/api/plan/v3", "api_key": "", "model": "deepseek-v4-flash" }
```

### sales-call-analysis-scripted/config.local.json

```json
{
  "api_url": "https://ark.cn-beijing.volces.com/api/plan/v3",
  "api_key": "",
  "model": "deepseek-v4-pro",
  "knowledge_root": "",
  "knowledge_enabled": true,
  "knowledge_mode": "manifest",
  "default_profile": "default",
  "profiles": {
    "default": {
      "base": "<base_token>", "table": "<02表table_id>", "default_view": "<view_id>",
      "source_field": "完整对话-文字", "notes_field": "现场速记",
      "identity_fields": ["门店编号", "门店名称"],
      "evidence_field": "门店档案-原文证据",
      "master_table": "<01门店主档table_id>", "master_link_field": "关联门店", "master_date_field": "进店时间",
      "master_history_days": 3,
      "stage_field": "拜访阶段1", "first_stage_choice": "陌拜破冰", "daily_stage_choices": ["日常拜访"],
      "closure_field": "上一次行动与这一次行动总结闭环",
      "history_profiles": 3, "history_actions": 1,
      "master_fields": { "一句画像": "one_line", "门店基本信息": "basic", "主营品类与品牌": "categories_brands", "经营模式": "business_model", "选品偏好": "selection_motion", "利润偏好": "price_profit", "合作偏好与排斥项": "cooperation_preferences" },
      "address_fields": ["门店所在省", "详细地址"],
      "correction_field": "门店档案纠正（dsr填写）",
      "valid_field": "是否有效对话"
    }
  }
}
```

> 注意:两个 skill 的 profile 字段名不同(ASR 用 base_token/table_id/view_id,analysis 用 base/table/default_view)。`valid_field` 在 analysis 的 config.example 里没有,必须手动加。

## 步骤 6:飞书表格映射

```bash
LARK="<lark-cli-path>"
# 列表(找 02 门店拜访记录 + 01 门店主档)
"$LARK" base +table-list --base-token <base_token> --as user --format json
# 列字段(验证字段名)
"$LARK" base +field-list --base-token <base_token> --table-id <table_id> --as user --format json
# 查 select 选项(验证拜访阶段选项)
"$LARK" base +field-search-options --base-token <base_token> --table-id <table_id> --field-id <field_id> --as user --format json
```

config 里的字段名必须和飞书实际字段名**完全一致**(含全角括号)。必须验证:录音文件(attachment)、完整对话-文字(text)、拜访阶段(select,选项名)、关联门店(link)、进店时间(datetime)、是否有效对话(select)、门店主档 table_id。

## 步骤 7:preflight 验证

```bash
cd ~/.workbuddy/skills/lark-sales-audio-asr-scripted
python scripts/pipeline.py --profile default --view <view_id> --preflight
cd ~/.workbuddy/skills/sales-call-analysis-scripted
python scripts/pipeline.py --profile default --view <view_id> --preflight
```

通过标准:无 RuntimeError,输出 records/pending 统计。

## 步骤 8:定时任务

用 automation_update 创建 recurring 任务:rrule `FREQ=DAILY;BYHOUR=21;BYMINUTE=30`,connectorIds `["feishu"]`,cwds 两个 skill 目录。prompt 包含:前置检查 → ASR 转写(费用门禁 ≤¥100 自动确认)→ 话术分析 → 飞书推送。费用门禁是 skill 硬性安全机制,不得绕过。

## 踩坑记录

1. **las_sdk 不在 PyPI**:`pip install lasutil` 失败;火山官方 las-cli(npm)子命令不兼容。用 TOS wheel URL 装 las_sdk。
2. **LLM 走 Ark 网关**:模型名是 `deepseek-v4-pro`/`deepseek-v4-flash`(Ark),不是 DeepSeek 官方的 `deepseek-chat`。api_url 是 `ark.cn-beijing.volces.com/api/plan/v3`。
3. **拜访阶段字段名/选项与默认值不同**:实际字段可能叫「拜访阶段1」,选项可能只有「陌拜破冰/日常拜访」。config.example 默认值几乎一定不符,必须用 lark-cli 查实际值。
4. **identity_fields 含不存在字段**:config.example 的「门店联系人」可能不存在,要去掉,否则 fetch_records 传空 field-id。
5. **analysis 缺 valid_field**:config.example 没有 valid_field,`profile.get("valid_field","")` 返回空,传空 `--field-id` 报 `field selection item N must not be empty`。必须手动加。
6. **lasutil/ffmpeg 不在 PATH**:装后位于 Scripts/,需在 config 或环境变量指定完整路径。
7. **环境变量继承**:setx 后当前进程读不到,需重启 WorkBuddy 或开新进程。定时任务必须在 prompt 第一步 `source env-loader.sh` 从注册表加载(见步骤 4)。
8. **classify-sales-call-roles 外部依赖**:ASR skill 角色归类依赖 classify-sales-call-roles skill(build_role_profile.py/apply_role_map.py/audit_role_map.py)。原 pipeline.py 硬编码了 12909 机器路径,已改为可配置 `ROLE_SKILL = Path(os.environ.get("CLASSIFY_ROLE_SKILL_PATH", str(ROOT.parent / "classify-sales-call-roles")))`。部署时必须把该 skill 装到 `~/.workbuddy/skills/classify-sales-call-roles/`,否则角色归类全部 role_error。装好后用 `--relabel-run "<run_dir>"` 重新角色归类(不重跑 ASR,无额外费用)。
9. **lark() 命令行太长(Windows cmd.exe 8191 限制)**:lark-cli 的 .CMD wrapper 走 cmd.exe,命令行参数上限 8191 字符。转写文本 >5000 字符时 `--json` 参数超限,报 GBK 乱码「命令行太长」,lark-cli 返回空。**已修复**:resolve_lark/_resolve_lark_bin 改为返回 `[node, run.js]` 列表,直接用 node 执行 run.js(CreateProcess 限制 32767),lark() 用 `[*LARK, *args, ...]` 展开列表。两个 skill 都已修。若仍遇此错,检查 resolve_lark 是否返回了 .CMD(说明修复未生效)。
10. **relabel 不判有效性**:`--relabel-run` 只做角色归类 + 写回转写,**不判「是否有效对话」**。analysis 只处理有效性=「有效」的记录,有效性为空的会被跳过。relabel 后需手动给记录设「是否有效对话=有效」(用 lark-cli record-upsert 批量设),否则 analysis 的 record_tasks 会偏少。
11. **analysis 默认不覆盖已有内容**:模块字段已有内容的记录,analysis 默认跳过(只填完全为空的模块)。需重新分析时用 `--overwrite <modules>`。判断哪些待分析:跑 preflight 看 record_tasks;读飞书字段确认是否为空。
12. **concerns 模块校验易失败**:LLM 输出的关心类目归因偶尔不通过硬校验(角色纠正冲突),走 review_fallback_primary(保留首轮,正常)或 validation_failed(转人工)。这是模型输出质量问题,非环境问题,skill 有 rescue 机制,不阻断其他模块。

## 问题速查索引

按现象快速定位:

| 现象 | 可能原因 | 对应条目 |
|------|---------|---------|
| `pip install lasutil` 失败 | las_sdk 不在 PyPI | 1 |
| LLM 调用报模型不存在 | 模型名用了 deepseek-chat(应为 deepseek-v4-pro/flash) | 2 |
| preflight 报 stage 选项缺少 | 拜访阶段字段名/选项与 config 不符 | 3 |
| `field selection item N must not be empty` | identity_fields 含不存在字段 或 缺 valid_field | 4, 5 |
| 定时任务中止「密钥未设置」 | WorkBuddy 进程未继承 setx 环境变量 | 7 |
| 角色归类全部 role_error | classify-sales-call-roles 未装 或 ROLE_SKILL 路径错 | 8 |
| 写回飞书失败,lark-cli 返回空 | 命令行太长(cmd.exe 8191 限制) | 9 |
| analysis record_tasks 偏少 | 有效性为空(relabel 没判) 或 模块已有内容 | 10, 11 |
| concerns validation_failed | LLM 输出校验不通过(模型质量问题) | 12 |
