#!/bin/bash

set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "用法: $0 <test|prod> <start|stop|status>" >&2
  exit 2
fi

ENVIRONMENT="$1"
ACTION="$2"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd -P)"

case "$ENVIRONMENT" in
  test)
    SERVICE_DIR="$PROJECT_ROOT/5-invalidity-search-test"
    ENV_FILE="$SERVICE_DIR/.env.test.local"
    CONFIG_FILE="$PROJECT_ROOT/ops/pm2/test/ecosystem.config.cjs"
    PROCESS_NAMES=("patent-invalidity-test-module1" "patent-invalidity-test-api")
    ;;
  prod)
    SERVICE_DIR="$PROJECT_ROOT/5-invalidity-search-prod"
    ENV_FILE="$SERVICE_DIR/.env.prod.local"
    CONFIG_FILE="$PROJECT_ROOT/ops/pm2/prod/ecosystem.config.cjs"
    PROCESS_NAMES=("patent-invalidity-prod-api")
    ;;
  *)
    echo "未知环境: $ENVIRONMENT" >&2
    exit 2
    ;;
esac

case "$ACTION" in
  start|stop|status) ;;
  *)
    echo "未知操作: $ACTION" >&2
    exit 2
    ;;
esac

PM2_STATE_ROOT="$PROJECT_ROOT/ops/pm2/.state"
PM2_HOME="$PM2_STATE_ROOT/$ENVIRONMENT"
if [ "$PM2_HOME" = "${HOME:-}/.pm2" ]; then
  echo "拒绝使用默认 ~/.pm2" >&2
  exit 1
fi
if [ -L "$PM2_STATE_ROOT" ] || [ -L "$PM2_HOME" ]; then
  echo "PM2 state 目录不允许是符号链接: $PM2_HOME" >&2
  exit 1
fi

PM2_BIN="$(command -v pm2 || true)"
if [ -z "$PM2_BIN" ]; then
  echo "未找到 pm2 命令" >&2
  exit 1
fi

if [ "$ACTION" != "start" ] && [ ! -f "$PM2_HOME/pm2.pid" ]; then
  echo "[$ENVIRONMENT] 独立 PM2 尚未启动: $PM2_HOME"
  exit 0
fi

if [ "$ACTION" = "start" ]; then
  if [ ! -f "$ENV_FILE" ]; then
    echo "缺少 $ENV_FILE；请从 $SERVICE_DIR/.env.example 复制并填写" >&2
    exit 1
  fi
  if [ -L "$ENV_FILE" ]; then
    echo "隔离环境文件不允许是符号链接: $ENV_FILE" >&2
    exit 1
  fi
  ENV_MODE="$(stat -f '%Lp' "$ENV_FILE" 2>/dev/null || stat -c '%a' "$ENV_FILE")"
  if [ $((8#$ENV_MODE & 077)) -ne 0 ]; then
    echo "隔离环境文件权限必须为 600 或更严格，当前为 $ENV_MODE: $ENV_FILE" >&2
    exit 1
  fi
  if [ "$ENVIRONMENT" = "prod" ] && [ ! -f "$SERVICE_DIR/CURRENT_RELEASE" ]; then
    echo "正式快照尚未 promotion，缺少 $SERVICE_DIR/CURRENT_RELEASE" >&2
    exit 1
  fi
fi

umask 077
if [ -L "$SERVICE_DIR/.logs" ]; then
  echo "日志目录不允许是符号链接: $SERVICE_DIR/.logs" >&2
  exit 1
fi
mkdir -p "$PM2_HOME" "$SERVICE_DIR/.logs"
chmod 700 "$PM2_STATE_ROOT" "$PM2_HOME" "$SERVICE_DIR/.logs" 2>/dev/null || true

CANONICAL_PM2_HOME="$(cd "$PM2_HOME" && pwd -P)"
if [ "$CANONICAL_PM2_HOME" != "$PM2_HOME" ]; then
  echo "PM2_HOME 真实路径与隔离路径不一致: $CANONICAL_PM2_HOME" >&2
  exit 1
fi
OTHER_ENVIRONMENT="test"
if [ "$ENVIRONMENT" = "test" ]; then
  OTHER_ENVIRONMENT="prod"
fi
OTHER_PM2_HOME="$PM2_STATE_ROOT/$OTHER_ENVIRONMENT"
if [ -d "$OTHER_PM2_HOME" ]; then
  if [ -L "$OTHER_PM2_HOME" ]; then
    echo "另一环境 PM2_HOME 不允许是符号链接: $OTHER_PM2_HOME" >&2
    exit 1
  fi
  CANONICAL_OTHER_PM2_HOME="$(cd "$OTHER_PM2_HOME" && pwd -P)"
  if [ "$CANONICAL_OTHER_PM2_HOME" = "$CANONICAL_PM2_HOME" ]; then
    echo "测试与正式 PM2_HOME 解析为同一目录，拒绝操作" >&2
    exit 1
  fi
fi

CLEAN_ENV=(
  env -i
  "HOME=${HOME:-$PROJECT_ROOT}"
  "PATH=${PATH:-/usr/local/bin:/usr/bin:/bin}"
  "PM2_HOME=$PM2_HOME"
  "LANG=${LANG:-en_US.UTF-8}"
  "LC_ALL=${LC_ALL:-}"
  "TMPDIR=${TMPDIR:-/tmp}"
  "USER=${USER:-unknown}"
  "LOGNAME=${LOGNAME:-${USER:-unknown}}"
  "SHELL=/bin/bash"
)

case "$ACTION" in
  start)
    echo "[$ENVIRONMENT] PM2_HOME=$PM2_HOME"
    echo "[$ENVIRONMENT] config=$CONFIG_FILE"
    exec "${CLEAN_ENV[@]}" "$PM2_BIN" start "$CONFIG_FILE" --update-env
    ;;
  stop)
    exec "${CLEAN_ENV[@]}" "$PM2_BIN" stop "${PROCESS_NAMES[@]}"
    ;;
  status)
    exec "${CLEAN_ENV[@]}" "$PM2_BIN" status
    ;;
esac
