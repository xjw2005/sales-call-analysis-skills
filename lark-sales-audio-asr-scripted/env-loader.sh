#!/bin/bash
# 从注册表 HKCU\Environment 加载用户级环境变量到当前进程
# 用途:WorkBuddy 进程可能未继承 setx 设置的用户级环境变量,定时任务的 agent 需主动加载
# 用法: source env-loader.sh
for v in ARK_API_KEY LAS_API_KEY LLM_API_URL LASUTIL_PATH TENCENT_ASR_SECRET_ID TENCENT_ASR_SECRET_KEY; do
  val=$(reg query "HKCU\Environment" /v "$v" 2>/dev/null | grep "$v" | awk '{print $NF}')
  [ -n "$val" ] && export "$v=$val"
done
