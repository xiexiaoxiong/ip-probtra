#!/bin/bash
set -Eeuo pipefail

COZE_WORKSPACE_PATH="${COZE_WORKSPACE_PATH:-$(pwd)}"

PORT=5000
DEPLOY_RUN_PORT="${DEPLOY_RUN_PORT:-$PORT}"
NODE_ENV="${NODE_ENV:-production}"
COZE_PROJECT_ENV="${COZE_PROJECT_ENV:-PROD}"


start_service() {
    cd "${COZE_WORKSPACE_PATH}"
    echo "Starting HTTP service on port ${DEPLOY_RUN_PORT} for deploy (NODE_ENV=${NODE_ENV}, COZE_PROJECT_ENV=${COZE_PROJECT_ENV})..."
    NODE_ENV="${NODE_ENV}" COZE_PROJECT_ENV="${COZE_PROJECT_ENV}" PORT=${DEPLOY_RUN_PORT} node dist/server.js
}

echo "Starting HTTP service on port ${DEPLOY_RUN_PORT} for deploy..."
start_service
