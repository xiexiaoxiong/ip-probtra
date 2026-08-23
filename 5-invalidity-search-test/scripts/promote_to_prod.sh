#!/bin/bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SOURCE_DIR="$(cd "$SCRIPT_DIR/.." && pwd -P)"
PROJECT_ROOT="$(cd "$SOURCE_DIR/.." && pwd -P)"
PROD_DIR="$PROJECT_ROOT/5-invalidity-search-prod"
RELEASES_DIR="$PROD_DIR/releases"
CURRENT_POINTER="$PROD_DIR/CURRENT_RELEASE"
CHARTER_PATH="$PROJECT_ROOT/PROJECT_CHARTER.md"
WORKFLOW_SPEC_PATH="$PROJECT_ROOT/docs/invalidity/WORKFLOW_SPEC.md"

APPLY=0
CONFIRMATION=""
RELEASE_ID=""

usage() {
  cat <<'USAGE'
用法:
  scripts/promote_to_prod.sh
  scripts/promote_to_prod.sh --apply --confirm <脚本输出的确认串> [--release-id <id>]

默认只做 dry-run。--apply 只创建新的不可变 release 并原子切换 CURRENT_RELEASE；
不会删除或覆盖已有 release，也不会启动、停止或重启 PM2。
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --apply)
      APPLY=1
      shift
      ;;
    --confirm)
      if [ "$#" -lt 2 ]; then
        echo "--confirm 缺少值" >&2
        exit 2
      fi
      CONFIRMATION="$2"
      shift 2
      ;;
    --release-id)
      if [ "$#" -lt 2 ]; then
        echo "--release-id 缺少值" >&2
        exit 2
      fi
      RELEASE_ID="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "未知参数: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

for command_name in find sort shasum awk rsync install; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "缺少命令: $command_name" >&2
    exit 1
  fi
done

for rule_file in "$CHARTER_PATH" "$WORKFLOW_SPEC_PATH"; do
  if [ ! -f "$rule_file" ]; then
    echo "缺少正式规则文件: $rule_file" >&2
    exit 1
  fi
done

tree_manifest() {
  local tree_root="$1"
  (
    cd "$tree_root"
    find . -type f \
      ! -path './.venv/*' \
      ! -path './.data/*' \
      ! -path './.logs/*' \
      ! -path './.pytest_cache/*' \
      ! -path './.release-rules/*' \
      ! -path '*/__pycache__/*' \
      ! -name '.DS_Store' \
      ! -name '.env.local' \
      ! -name '.env.test.local' \
      ! -name '.env.prod.local' \
      ! -name '.env.example' \
      ! -name 'AGENTS.md' \
      ! -name 'README.md' \
      ! -name 'RELEASE_MANIFEST.sha256' \
      ! -name 'RELEASE_METADATA' \
      ! -name 'PROMOTION_FAILED' \
      ! -path './scripts/promote_to_prod.sh' \
      ! -path './scripts/parser_http_run.sh' \
      -print | LC_ALL=C sort | while IFS= read -r relative_path; do
        file_hash="$(shasum -a 256 "$relative_path" | awk '{print $1}')"
        printf '%s  %s\n' "$file_hash" "$relative_path"
      done
  )
}

assert_no_promoted_symlinks() {
  local tree_root="$1"
  local relative_path
  while IFS= read -r relative_path; do
    case "$relative_path" in
      ./.venv/*|./.data/*|./.logs/*|./.pytest_cache/*|*/__pycache__/*) continue ;;
      ./.env.local|./.env.test.local|./.env.prod.local|./.env.example) continue ;;
      ./AGENTS.md|./README.md) continue ;;
      *)
        echo "promotion 源包含被纳入范围的符号链接，拒绝: $relative_path" >&2
        exit 1
        ;;
    esac
  done < <(cd "$tree_root" && find . -type l -print | LC_ALL=C sort)
}

assert_no_promoted_symlinks "$SOURCE_DIR"

MANIFEST="$(tree_manifest "$SOURCE_DIR")"
if [ -z "$MANIFEST" ]; then
  echo "没有可 promotion 的源文件" >&2
  exit 1
fi
SOURCE_DIGEST="$(printf '%s\n' "$MANIFEST" | shasum -a 256 | awk '{print $1}')"
CHARTER_DIGEST="$(shasum -a 256 "$CHARTER_PATH" | awk '{print $1}')"
WORKFLOW_SPEC_DIGEST="$(shasum -a 256 "$WORKFLOW_SPEC_PATH" | awk '{print $1}')"
PROMOTION_DIGEST="$(printf '%s\n%s\n%s\n' \
  "$SOURCE_DIGEST" \
  "$CHARTER_DIGEST" \
  "$WORKFLOW_SPEC_DIGEST" | shasum -a 256 | awk '{print $1}')"

CURRENT_RELEASE="NONE"
if [ -f "$CURRENT_POINTER" ]; then
  CURRENT_RELEASE="$(tr -d '\r\n' < "$CURRENT_POINTER")"
  if ! [[ "$CURRENT_RELEASE" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]]; then
    echo "现有 CURRENT_RELEASE 内容不合法，拒绝 promotion" >&2
    exit 1
  fi
fi

CONFIRM_TOKEN="PROMOTE_INVALIDITY_${PROMOTION_DIGEST:0:16}_FROM_${CURRENT_RELEASE}"
if [ -z "$RELEASE_ID" ]; then
  RELEASE_ID="$(date -u '+%Y%m%dT%H%M%SZ')-${PROMOTION_DIGEST:0:12}"
fi
if ! [[ "$RELEASE_ID" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]]; then
  echo "release id 只能包含字母、数字、点、下划线和连字符" >&2
  exit 2
fi
RELEASE_DIR="$RELEASES_DIR/$RELEASE_ID"

RSYNC_ARGS=(
  -a
  --exclude=.venv/
  --exclude=.data/
  --exclude=.logs/
  --exclude=.pytest_cache/
  --exclude='**/__pycache__/'
  --exclude='*.py[cod]'
  --exclude=.DS_Store
  --exclude=.env.local
  --exclude=.env.test.local
  --exclude=.env.prod.local
  --exclude=.env.example
  --exclude=AGENTS.md
  --exclude=README.md
  --exclude=scripts/promote_to_prod.sh
  --exclude=scripts/parser_http_run.sh
)

echo "source:          $SOURCE_DIR"
echo "source digest:   $SOURCE_DIGEST"
echo "charter digest:  $CHARTER_DIGEST"
echo "workflow digest: $WORKFLOW_SPEC_DIGEST"
echo "promotion digest:$PROMOTION_DIGEST"
echo "current release: $CURRENT_RELEASE"
echo "new release:     $RELEASE_ID"
echo "target:          $RELEASE_DIR"
echo "confirm token:   $CONFIRM_TOKEN"

if [ "$APPLY" -eq 0 ]; then
  echo
  echo "[dry-run] 不会写入正式目录。计划复制如下："
  rsync "${RSYNC_ARGS[@]}" --dry-run --itemize-changes "$SOURCE_DIR/" "$RELEASE_DIR/"
  echo
  echo "确认后执行："
  printf '  %q --apply --confirm %q --release-id %q\n' "$0" "$CONFIRM_TOKEN" "$RELEASE_ID"
  exit 0
fi

if [ "$CONFIRMATION" != "$CONFIRM_TOKEN" ]; then
  echo "确认串不匹配。请先重新运行 dry-run，并复制当次输出的 confirm token。" >&2
  exit 1
fi
if [ -e "$RELEASE_DIR" ]; then
  echo "目标 release 已存在，拒绝覆盖: $RELEASE_DIR" >&2
  exit 1
fi

mkdir -p "$RELEASES_DIR"
mkdir "$RELEASE_DIR"
rsync "${RSYNC_ARGS[@]}" "$SOURCE_DIR/" "$RELEASE_DIR/"
COPIED_MANIFEST="$(tree_manifest "$RELEASE_DIR")"
if [ "$COPIED_MANIFEST" != "$MANIFEST" ]; then
  {
    printf 'promotion_failed_at_utc=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    printf 'reason=source_changed_or_copy_mismatch\n'
    printf 'expected_source_digest=%s\n' "$SOURCE_DIGEST"
  } > "$RELEASE_DIR/PROMOTION_FAILED"
  echo "复制后的 manifest 与确认源不一致；测试目录可能在 promotion 期间发生变化。" >&2
  echo "该 release 未激活且不会被覆盖: $RELEASE_DIR" >&2
  exit 1
fi
printf '%s\n' "$MANIFEST" > "$RELEASE_DIR/RELEASE_MANIFEST.sha256"
mkdir "$RELEASE_DIR/.release-rules"
install -m 0444 "$CHARTER_PATH" "$RELEASE_DIR/.release-rules/PROJECT_CHARTER.md"
install -m 0444 "$WORKFLOW_SPEC_PATH" "$RELEASE_DIR/.release-rules/WORKFLOW_SPEC.md"
{
  printf '%s  .release-rules/PROJECT_CHARTER.md\n' "$CHARTER_DIGEST"
  printf '%s  .release-rules/WORKFLOW_SPEC.md\n' "$WORKFLOW_SPEC_DIGEST"
} > "$RELEASE_DIR/RULES_MANIFEST.sha256"
{
  printf 'release_id=%s\n' "$RELEASE_ID"
  printf 'source_digest=%s\n' "$SOURCE_DIGEST"
  printf 'charter_sha256=%s\n' "$CHARTER_DIGEST"
  printf 'workflow_spec_sha256=%s\n' "$WORKFLOW_SPEC_DIGEST"
  printf 'promotion_digest=%s\n' "$PROMOTION_DIGEST"
  printf 'promoted_at_utc=%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  printf 'previous_release=%s\n' "$CURRENT_RELEASE"
} > "$RELEASE_DIR/RELEASE_METADATA"

CURRENT_RELEASE_BEFORE_SWITCH="NONE"
if [ -f "$CURRENT_POINTER" ]; then
  CURRENT_RELEASE_BEFORE_SWITCH="$(tr -d '\r\n' < "$CURRENT_POINTER")"
fi
if [ "$CURRENT_RELEASE_BEFORE_SWITCH" != "$CURRENT_RELEASE" ]; then
  printf 'reason=current_release_changed_before_switch\n' > "$RELEASE_DIR/PROMOTION_FAILED"
  echo "CURRENT_RELEASE 在 promotion 期间发生变化；新 release 未激活。" >&2
  exit 1
fi

# A production release is a read-only code/rules snapshot.  Runtime virtual
# environments, caches, logs and artifacts live outside the release tree.
find "$RELEASE_DIR" -type f -exec chmod 0444 {} +
find "$RELEASE_DIR" -type d -exec chmod 0555 {} +
# Managed macOS workspaces can restore owner mode bits on directories after a
# command finishes.  Preserve the same read-only contract with a deny ACL; the
# startup gate checks effective writability and still verifies every file hash.
if [ "$(uname -s)" = "Darwin" ]; then
  RELEASE_OWNER="$(id -un)"
  find "$RELEASE_DIR" -type d -exec chmod +a \
    "user:$RELEASE_OWNER deny add_file,add_subdirectory,delete_child,delete,writeattr,writeextattr,chown" {} \;
fi

POINTER_TMP="$PROD_DIR/.CURRENT_RELEASE.$$.tmp"
printf '%s\n' "$RELEASE_ID" > "$POINTER_TMP"
chmod 600 "$POINTER_TMP"
mv "$POINTER_TMP" "$CURRENT_POINTER"

echo "promotion 完成；未自动重启正式服务。"
echo "激活 release: $RELEASE_ID"
echo "下一步可手工执行: $PROJECT_ROOT/ops/pm2/prod/start.sh"
