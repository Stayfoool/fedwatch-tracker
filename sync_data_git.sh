#!/usr/bin/env bash
# sync_data_git.sh — 服务器每日采集成功后，把新增数据回推 GitHub，Mac 端 git pull 即可同步。
# 只回推主存档与快照；events.csv / significant_changes.csv 由 Mac 端归因任务维护，避免双向冲突。
set -Eeuo pipefail
DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$DIR"

[[ -n $(git status --porcelain -- data/fedwatch_probabilities.csv data/snapshots) ]] || { echo "sync_data_git: 无新增数据"; exit 0; }

git add data/fedwatch_probabilities.csv data/snapshots
git -c user.name=fedwatch-server -c user.email=fedwatch-server@users.noreply.github.com \
  commit -q -m "Archive FedWatch snapshots $(date +%F)"

if ! git pull --rebase --quiet origin main; then
  git rebase --abort 2>/dev/null || true
  echo "sync_data_git: rebase 失败，本次不推送（明日自动重试）" >&2
  exit 1
fi
git push --quiet origin main
echo "sync_data_git: 已推送 $(git rev-parse --short HEAD)"
