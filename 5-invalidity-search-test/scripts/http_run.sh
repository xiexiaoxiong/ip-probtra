#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
WORK_DIR="$(cd "$SCRIPT_DIR/.." && pwd -P)"

required_env() {
  local name="$1"
  local value="${!name:-}"
  if [ -z "$value" ]; then
    echo "缺少必需环境变量 $name" >&2
    exit 1
  fi
  if [[ "$value" =~ REPLACE_|CHANGE_ME ]]; then
    echo "$name 仍为占位值" >&2
    exit 1
  fi
}

required_env INVALIDITY_ENV
required_env INVALIDITY_PORT
required_env INVALIDITY_DATABASE_URL
required_env INVALIDITY_DATABASE_SCHEMA
required_env INVALIDITY_ARTIFACT_ROOT
required_env INVALIDITY_ALLOWED_SOURCE_ROOTS

case "$INVALIDITY_ENV" in
  test)
    EXPECTED_PORT="5209"
    EXPECTED_SCHEMA="invalidity_test"
    EXPECTED_ARTIFACT_SUFFIX="invalidity/test"
    PREFIX="INVALIDITY_TEST"
    API_VARIABLE="INVALIDITY_TEST_API_URL"
    EXPECTED_API_URL="http://127.0.0.1:5209"
    MODULE1_VARIABLE="INVALIDITY_TEST_MODULE1_API_URL"
    EXPECTED_MODULE1_URL="http://127.0.0.1:5201/run"
    FORBIDDEN_PREFIX="INVALIDITY_PROD_"
    ;;
  prod)
    EXPECTED_PORT="5109"
    EXPECTED_SCHEMA="invalidity_prod"
    EXPECTED_ARTIFACT_SUFFIX="invalidity/prod"
    PREFIX="INVALIDITY_PROD"
    API_VARIABLE="INVALIDITY_PROD_API_URL"
    EXPECTED_API_URL="http://127.0.0.1:5109"
    MODULE1_VARIABLE="INVALIDITY_PROD_MODULE1_API_URL"
    EXPECTED_MODULE1_URL="http://127.0.0.1:5101/run"
    FORBIDDEN_PREFIX="INVALIDITY_TEST_"
    ;;
  *)
    echo "INVALIDITY_ENV 必须显式设置为 test 或 prod" >&2
    exit 1
    ;;
esac

if [ "$INVALIDITY_PORT" != "$EXPECTED_PORT" ]; then
  echo "$INVALIDITY_ENV 服务端口必须是 $EXPECTED_PORT" >&2
  exit 1
fi
if [ "$INVALIDITY_DATABASE_SCHEMA" != "$EXPECTED_SCHEMA" ]; then
  echo "$INVALIDITY_ENV schema 必须是 $EXPECTED_SCHEMA" >&2
  exit 1
fi
case "$INVALIDITY_ARTIFACT_ROOT" in
  *"/$EXPECTED_ARTIFACT_SUFFIX"|*"/$EXPECTED_ARTIFACT_SUFFIX/"*) ;;
  *)
    echo "$INVALIDITY_ENV 工件目录必须包含 /$EXPECTED_ARTIFACT_SUFFIX" >&2
    exit 1
    ;;
esac

required_env "$API_VARIABLE"
if [ "${!API_VARIABLE%/}" != "$EXPECTED_API_URL" ]; then
  echo "$API_VARIABLE 必须严格等于 $EXPECTED_API_URL" >&2
  exit 1
fi

required_env "${PREFIX}_API_TOKEN"

if [ "$INVALIDITY_ENV" = "test" ]; then
  required_env INVALIDITY_TEST_PARSER_TOKEN
else
  if [ -n "${INVALIDITY_PROD_MODULE1_API_TOKEN:-}" ]; then
    required_env INVALIDITY_PROD_MODULE1_API_TOKEN
  elif [ "${INVALIDITY_PROD_MODULE1_AUTH_MODE:-}" != "legacy_unauthenticated" ]; then
    echo "正式模块一必须配置 INVALIDITY_PROD_MODULE1_API_TOKEN，或显式设置 INVALIDITY_PROD_MODULE1_AUTH_MODE=legacy_unauthenticated" >&2
    exit 1
  fi
fi

required_env "$MODULE1_VARIABLE"
if [ "${!MODULE1_VARIABLE}" != "$EXPECTED_MODULE1_URL" ]; then
  echo "$MODULE1_VARIABLE 必须严格等于 $EXPECTED_MODULE1_URL" >&2
  exit 1
fi

for suffix in PATENT_PROVIDER NPL_PROVIDER LLM_BASE_URL LLM_API_KEY LLM_MODEL; do
  required_env "${PREFIX}_${suffix}"
done

PATENT_PROVIDER_VARIABLE="${PREFIX}_PATENT_PROVIDER"
if [ "${!PATENT_PROVIDER_VARIABLE}" = "patsnap" ] || [ "${!PATENT_PROVIDER_VARIABLE}" = "epo_ops" ]; then
  required_env "${PREFIX}_EPO_OPS_KEY"
  required_env "${PREFIX}_EPO_OPS_SECRET"
fi
if [ "${!PATENT_PROVIDER_VARIABLE}" = "patsnap" ]; then
  required_env "${PREFIX}_PATSNAP_API_KEY"
  required_env "${PREFIX}_PATSNAP_BASE_URL"
  required_env "${PREFIX}_PATSNAP_COUNT_PATH"
  required_env "${PREFIX}_PATSNAP_SEARCH_PATH"
  PATSNAP_BASE_VARIABLE="${PREFIX}_PATSNAP_BASE_URL"
  PATSNAP_COUNT_VARIABLE="${PREFIX}_PATSNAP_COUNT_PATH"
  PATSNAP_SEARCH_VARIABLE="${PREFIX}_PATSNAP_SEARCH_PATH"
  if [ "${!PATSNAP_BASE_VARIABLE}" != "https://connect.zhihuiya.com" ]; then
    echo "$PATSNAP_BASE_VARIABLE 必须严格等于 https://connect.zhihuiya.com" >&2
    exit 1
  fi
  case "${!PATSNAP_COUNT_VARIABLE}" in
    /search/patent/query-search-count|/search/patent/query-search-count/v2) ;;
    *)
      echo "$PATSNAP_COUNT_VARIABLE 不在官方候选路径白名单" >&2
      exit 1
      ;;
  esac
  case "${!PATSNAP_SEARCH_VARIABLE}" in
    /search/patent/query-search-patent|/search/patent/query-search-patent/v2) ;;
    *)
      echo "$PATSNAP_SEARCH_VARIABLE 不在官方候选路径白名单" >&2
      exit 1
      ;;
  esac
fi

MODEL_VARIABLE="${PREFIX}_LLM_MODEL"
case "${!MODEL_VARIABLE}" in
  glm-4.6v*) ;;
  *)
    echo "$MODEL_VARIABLE 必须是 glm-4.6v 系列多模态模型" >&2
    exit 1
    ;;
esac

while IFS= read -r name; do
  if [[ "$name" == "$FORBIDDEN_PREFIX"* ]] && [ -n "${!name:-}" ]; then
    echo "$INVALIDITY_ENV 进程中发现跨环境变量 $name，拒绝启动" >&2
    exit 1
  fi
done < <(compgen -e)

if [ ! -f "$WORK_DIR/src/invalidity/main.py" ]; then
  echo "缺少服务入口 $WORK_DIR/src/invalidity/main.py" >&2
  exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "未找到 uv 命令" >&2
  exit 1
fi

mkdir -p "$INVALIDITY_ARTIFACT_ROOT"
cd "$WORK_DIR"

# Start the API with an explicit environment allow-list.  This protects both
# direct runs and PM2 restarts from inheriting unrelated credentials from an
# old daemon or a developer shell.
CLEAN_ENV=(
  env -i
  "HOME=${HOME:-$WORK_DIR}"
  "PATH=${PATH:-/usr/local/bin:/usr/bin:/bin}"
  "LANG=${LANG:-en_US.UTF-8}"
  "LC_ALL=${LC_ALL:-}"
  "TMPDIR=${TMPDIR:-/tmp}"
  "PYTHONUNBUFFERED=1"
  "PYTHONDONTWRITEBYTECODE=1"
)

for name in \
  INVALIDITY_ENV \
  INVALIDITY_PORT \
  INVALIDITY_DATABASE_URL \
  INVALIDITY_DATABASE_SCHEMA \
  INVALIDITY_ARTIFACT_ROOT \
  INVALIDITY_ALLOWED_SOURCE_ROOTS \
  "$API_VARIABLE" \
  "$MODULE1_VARIABLE" \
  "${PREFIX}_API_TOKEN" \
  "${PREFIX}_PATENT_PROVIDER" \
  "${PREFIX}_PATSNAP_API_KEY" \
  "${PREFIX}_PATSNAP_BASE_URL" \
  "${PREFIX}_PATSNAP_COUNT_PATH" \
  "${PREFIX}_PATSNAP_SEARCH_PATH" \
  "${PREFIX}_EPO_OPS_KEY" \
  "${PREFIX}_EPO_OPS_SECRET" \
  "${PREFIX}_NPL_PROVIDER" \
  "${PREFIX}_LLM_BASE_URL" \
  "${PREFIX}_LLM_API_KEY" \
  "${PREFIX}_LLM_MODEL" \
  INVALIDITY_TEST_PARSER_TOKEN \
  INVALIDITY_PROD_MODULE1_API_TOKEN \
  INVALIDITY_PROD_MODULE1_AUTH_MODE \
  INVALIDITY_MAX_ROUNDS \
  INVALIDITY_MAX_CANDIDATES_PER_QUERY \
  INVALIDITY_WORKER_CONCURRENCY \
  INVALIDITY_WORKER_POLL_SECONDS \
  INVALIDITY_JOB_LEASE_SECONDS \
  INVALIDITY_LLM_TIMEOUT_SECONDS \
  INVALIDITY_LLM_DIRECT_ATTEMPT_TIMEOUT_SECONDS \
  INVALIDITY_LLM_DIRECT_PROBE_TIMEOUT_SECONDS \
  INVALIDITY_I2_LLM_TIMEOUT_SECONDS \
  INVALIDITY_I2_LLM_DIRECT_ATTEMPT_TIMEOUT_SECONDS \
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

exec "${CLEAN_ENV[@]}" uv run --frozen uvicorn invalidity.main:app \
  --app-dir src \
  --host 127.0.0.1 \
  --port "$INVALIDITY_PORT"
