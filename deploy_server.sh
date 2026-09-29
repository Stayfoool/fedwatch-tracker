#!/usr/bin/env bash
# deploy_server.sh — Mac 端部署触发器：阿里云服务器自行从 GitHub 拉取并发布。
# 用法：./deploy_server.sh [ref]   （默认 main；要求工作区干净、ref 已推送）
set -Eeuo pipefail
cd "$(dirname "$0")"

HOST=${FEDWATCH_HOST:-8.215.88.73}
REMOTE_USER=${FEDWATCH_USER:-root}
REMOTE_DEPLOY=${REMOTE_DEPLOY:-/usr/local/sbin/fedwatch-deploy}
REF=${1:-main}

fail() { echo "deploy_server: $*" >&2; exit 1; }
[[ -z "$(git status --porcelain)" ]] || fail "工作区不干净；先 commit 并 push 再部署"
[[ "$REF" =~ ^[A-Za-z0-9._/-]+$ ]] || fail "非法 ref: $REF"

git fetch --quiet origin main
if [[ "$REF" == "main" || "$REF" == "origin/main" ]]; then
  COMMIT=$(git rev-parse origin/main^{commit})
else
  COMMIT=$(git rev-parse "$REF^{commit}")
fi
[[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]] || fail "无法解析 $REF 的 commit"
git merge-base --is-ancestor "$COMMIT" origin/main^{commit} || fail "$REF 不在已推送的 origin/main 历史中"

echo "触发阿里云部署：$HOST <- commit $COMMIT"
ssh -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=120 \
  "$REMOTE_USER@$HOST" "$REMOTE_DEPLOY" main "$COMMIT"
