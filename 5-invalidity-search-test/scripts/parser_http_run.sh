#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
WORK_DIR="$(cd "$SCRIPT_DIR/.." && pwd -P)"

required_env() {
  local name="$1"
  local value="${!name:-}"
  if [ -z "$value" ]; then
    echo "测试解析兼容服务缺少 $name" >&2
    exit 1
  fi
  if [[ "$value" =~ REPLACE_|CHANGE_ME ]]; then
    echo "$name 仍为占位值" >&2
    exit 1
  fi
}

for name in \
  INVALIDITY_ENV \
  INVALIDITY_ARTIFACT_ROOT \
  INVALIDITY_ALLOWED_SOURCE_ROOTS \
  INVALIDITY_PARSER_PORT \
  INVALIDITY_TEST_PARSER_TOKEN; do
  required_env "$name"
done

if [ "$INVALIDITY_ENV" != "test" ]; then
  echo "测试解析兼容服务只能以 INVALIDITY_ENV=test 运行" >&2
  exit 1
fi
if [ "$INVALIDITY_PARSER_PORT" != "5201" ]; then
  echo "INVALIDITY_PARSER_PORT 必须是 5201" >&2
  exit 1
fi
case "$INVALIDITY_ARTIFACT_ROOT" in
  */invalidity/test|*/invalidity/test/*) ;;
  *)
    echo "测试解析兼容服务工件目录必须包含 /invalidity/test" >&2
    exit 1
    ;;
esac
while IFS= read -r name; do
  if [[ "$name" == INVALIDITY_PROD_* ]] && [ -n "${!name:-}" ]; then
    echo "测试解析兼容服务发现正式变量 $name，拒绝启动" >&2
    exit 1
  fi
done < <(compgen -e)

# The parser is a neutral file-processing trust boundary.  Even if a parent
# shell exported the full 5209 configuration, do not pass unrelated database,
# API, provider, or model credentials into the uvicorn child process.
unset \
  INVALIDITY_PORT \
  INVALIDITY_DATABASE_URL \
  INVALIDITY_DATABASE_SCHEMA \
  INVALIDITY_TEST_API_URL \
  INVALIDITY_TEST_API_TOKEN \
  INVALIDITY_TEST_MODULE1_API_URL \
  INVALIDITY_TEST_PATENT_PROVIDER \
  INVALIDITY_TEST_NPL_PROVIDER \
  INVALIDITY_TEST_LLM_BASE_URL \
  INVALIDITY_TEST_LLM_API_KEY \
  INVALIDITY_TEST_LLM_MODEL

if [ ! -f "$WORK_DIR/src/invalidity/parser_service.py" ]; then
  echo "缺少测试解析兼容服务入口 $WORK_DIR/src/invalidity/parser_service.py" >&2
  exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "未找到 uv 命令" >&2
  exit 1
fi

cd "$WORK_DIR"
CLEAN_ENV=(
  env -i
  "HOME=${HOME:-$WORK_DIR}"
  "PATH=${PATH:-/usr/local/bin:/usr/bin:/bin}"
  "LANG=${LANG:-en_US.UTF-8}"
  "LC_ALL=${LC_ALL:-}"
  "TMPDIR=${TMPDIR:-/tmp}"
  "INVALIDITY_ENV=$INVALIDITY_ENV"
  "INVALIDITY_ARTIFACT_ROOT=$INVALIDITY_ARTIFACT_ROOT"
  "INVALIDITY_ALLOWED_SOURCE_ROOTS=$INVALIDITY_ALLOWED_SOURCE_ROOTS"
  "INVALIDITY_PARSER_PORT=$INVALIDITY_PARSER_PORT"
  "INVALIDITY_TEST_PARSER_TOKEN=$INVALIDITY_TEST_PARSER_TOKEN"
)
for name in \
  INVALIDITY_SOURCE_FETCH_TIMEOUT_SECONDS \
  INVALIDITY_SOURCE_MAX_BYTES \
  INVALIDITY_IMAGE_MAX_BYTES \
  INVALIDITY_SOURCE_MAX_REDIRECTS \
  UV_CACHE_DIR \
  UV_PROJECT_ENVIRONMENT; do
  if [ -n "${!name:-}" ]; then
    CLEAN_ENV+=("$name=${!name}")
  fi
done

exec "${CLEAN_ENV[@]}" uv run --frozen uvicorn invalidity.parser_service:app \
  --app-dir src \
  --host 127.0.0.1 \
  --port "$INVALIDITY_PARSER_PORT"
