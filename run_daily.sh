#!/bin/zsh
# run_daily.sh — FedWatch 每日采集、报告生成和生产发布入口（LaunchAgent 调用）
#
# 时间线（北京时间）：
#   10:00 第 1 次尝试；抓取失败则 10:10、10:20 各重试 1 次
#   抓取成功后重建报告，并把 report/ 自动发布到 Cloudflare Pages production
#
# 流程：
#   1. fetch_quikstrike.py 抓取 QuikStrike Aggregated View
#   2. 追加写入 data/fedwatch_probabilities.csv（自动去重）
#   3. 重建 report/index.html
#   4. 检测重大变动（非阻断）
#   5. deploy_pages.sh 发布并验证 fedwatch-tracker.pages.dev
#
# 注意：历史回填已从自动流程移除。现有历史数据保留；如确需修复历史缺口，手动运行 backfill_history.py。

set -u
set -o pipefail
umask 077

DIR=${0:A:h}
PY=${PYTHON_BIN:-python3}
LOG="$DIR/logs/fetch_$(date +%Y%m%d).log"
MAX=3             # 抓取总尝试次数
SLEEP=600         # 抓取重试间隔：10 分钟
DEPLOY_MAX=3      # 发布总尝试次数
DEPLOY_SLEEP=60   # 发布重试间隔：1 分钟

# 使用项目内固定版本的 agent-browser，避免依赖交互式 shell 的 PATH。
export PATH="$DIR/node_modules/.bin:$PATH"

mkdir -p "$(dirname "$LOG")"
cd "$DIR" || exit 1

log() {
  print -- "$*" | tee -a "$LOG"
}

FETCH_OK=0
attempt=1
while [[ $attempt -le $MAX ]]; do
  log "=== 第 $attempt/$MAX 次抓取尝试 $(date '+%F %T %Z')"
  OUT=$("$PY" fetch_quikstrike.py 2>&1)
  RC=$?
  print -- "$OUT" | tee -a "$LOG"
  if [[ $RC -eq 0 ]]; then
    FETCH_OK=1
    break
  fi
  log "--- 抓取失败 (rc=$RC)"
  attempt=$((attempt + 1))
  if [[ $attempt -le $MAX ]]; then
    log "--- ${SLEEP}s 后重试"
    sleep $SLEEP
  fi
done

if [[ $FETCH_OK -ne 1 ]]; then
  log "FAILED 已尝试 $MAX 次仍无法抓取 $(date '+%F %T %Z')"
  exit 1
fi

log "=== 重建报告 $(date '+%F %T %Z')"
if ! "$PY" build_report.py 2>&1 | tee -a "$LOG"; then
  log "FAILED 报告重建失败；为避免发布旧数据，本次不部署"
  exit 1
fi

# 发现尚未标注原因的 ≥8pp 变动日在日志中提示；失败不阻断报告发布。
log "=== 重大变动检测 $(date '+%F %T %Z')"
if ! "$PY" analyze_changes.py --days 7 2>&1 | tee -a "$LOG"; then
  log "--- 重大变动检测失败（不阻断部署）"
fi

deploy_attempt=1
while [[ $deploy_attempt -le $DEPLOY_MAX ]]; do
  log "=== 第 $deploy_attempt/$DEPLOY_MAX 次发布尝试 $(date '+%F %T %Z')"
  if "$DIR/deploy_pages.sh" 2>&1 | tee -a "$LOG"; then
    log "OK 数据、报告与公开网站均已更新 $(date '+%F %T %Z')"
    exit 0
  fi
  log "--- Cloudflare Pages 发布或验证失败"
  deploy_attempt=$((deploy_attempt + 1))
  if [[ $deploy_attempt -le $DEPLOY_MAX ]]; then
    log "--- ${DEPLOY_SLEEP}s 后仅重试发布（不重复抓取）"
    sleep $DEPLOY_SLEEP
  fi
done

log "FAILED 报告已在本地生成，但 Pages 发布连续失败 $DEPLOY_MAX 次 $(date '+%F %T %Z')"
exit 1
