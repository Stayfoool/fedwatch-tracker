#!/bin/zsh
# run_daily.sh — FedWatch 每日采集、报告生成和生产发布入口（LaunchAgent 调用）
#
# 时间线（北京时间）：
#   launchd 挂两个触发器：05:30 与 06:30
#     夏令时(CDT) → 只有 05:30 那次落在窗口内（= 前一日 16:30 CT）
#     冬令时(CST) → 只有 06:30 那次落在窗口内（= 前一日 16:30 CT）
#   窗口 = 美东收盘后的休市间隙 [16:02, 16:58) CT，由 fetch_quikstrike.py 自行判定，
#   不在窗口内的那次直接跳过（exit 10），不做任何采集。
#   落在窗口内的那次若失败，则 +10、+20 分钟重试（仍在窗口内）；成功后重建报告并发布。
#
# 退出码约定（fetch_quikstrike.py）：
#   0 成功 / 10 不在采集窗口 / 11 该美东交易日已有收盘读数 / 12 数据未变
#   10、11、12 都属于「正常跳过」，不重试、不告警。
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
# 发布渠道：默认 Cloudflare Pages（Mac）；阿里云服务器的 systemd 服务通过
# FEDWATCH_DEPLOY_SCRIPT 指向 publish_report.sh，把 report/ 发布到本地 nginx 站点。
DEPLOY_SCRIPT=${FEDWATCH_DEPLOY_SCRIPT:-$DIR/deploy_pages.sh}

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
  # 10/11/12 = 正常跳过（不在窗口 / 重复交易日 / 数据未变）：不重试、不告警
  if [[ $RC -eq 10 || $RC -eq 11 || $RC -eq 12 ]]; then
    log "SKIP 本次无需采集 (rc=$RC)，不重试、不发布 $(date '+%F %T %Z')"
    exit 0
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
# 快照选取自检：会议开完后抓取表会少一个会议，选错会展示已结束的会议（详见 snapshot_pick.py）
if ! "$PY" snapshot_pick.py 2>&1 | tee -a "$LOG"; then
  log "FAILED 快照选取自检未通过；为避免发布错误数据，本次不部署"
  exit 1
fi
if ! "$PY" build_report.py 2>&1 | tee -a "$LOG"; then
  log "FAILED 报告重建失败；为避免发布旧数据，本次不部署"
  exit 1
fi

# 「方向与幅度」双图页（report/curves.html）：与主看板同数据源，失败不阻断部署。
if ! "$PY" build_curves.py 2>&1 | tee -a "$LOG"; then
  log "--- 双图页 curves.html 生成失败（不阻断部署）"
fi

# 发现尚未标注原因的 ≥8pp 变动日在日志中提示；失败不阻断报告发布。
# 口径：焦点会议（最近一场未开完的 FOMC）。退出码 2 = 有未归因日（需补 events.csv），
# 不是脚本出错；只有 1 才是真异常。
log "=== 重大变动检测 $(date '+%F %T %Z')"
"$PY" analyze_changes.py --days 7 2>&1 | tee -a "$LOG"
AC_RC=${pipestatus[1]}
if [[ $AC_RC -eq 2 ]]; then
  log "--- 有变动日尚未归因，请补 data/events.csv（不阻断部署）"
elif [[ $AC_RC -ne 0 ]]; then
  log "--- 重大变动检测异常退出 rc=$AC_RC（不阻断部署）"
fi

deploy_attempt=1
while [[ $deploy_attempt -le $DEPLOY_MAX ]]; do
  log "=== 第 $deploy_attempt/$DEPLOY_MAX 次发布尝试 $(date '+%F %T %Z')"
  if "$DEPLOY_SCRIPT" 2>&1 | tee -a "$LOG"; then
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
