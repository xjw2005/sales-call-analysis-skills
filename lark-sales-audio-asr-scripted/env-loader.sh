#!/bin/bash
# 从注册表 HKCU\Environment 加载用户级环境变量到当前进程
# 用途:WorkBuddy 进程可能未继承 setx 设置的用户级环境变量,定时任务的 agent 需主动加载
# 用法: source env-loader.sh
# 注:reg 命令可能被安全策略禁用,改用 python winreg 读取
PY="C:/Users/YUOU/.workbuddy/binaries/python/versions/3.13.12/python.exe"
for v in DEEPSEEK_API_KEY LAS_API_KEY LLM_API_URL LASUTIL_PATH TENCENT_ASR_SECRET_ID TENCENT_ASR_SECRET_KEY; do
  val=$("$PY" -c "import winreg; k=winreg.OpenKey(winreg.HKEY_CURRENT_USER,'Environment'); print(winreg.QueryValueEx(k,'$v')[0])" 2>/dev/null)
  [ -n "$val" ] && export "$v=$val"
done
