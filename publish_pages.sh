#!/usr/bin/env bash
# publish_pages.sh — 服务器端 Cloudflare Pages 发布（pages.dev 对外门面）。
# 2026-10-02 起 pages.dev 的构建与发布全部由服务器完成：
#   采集(服务器) → 构建 report/ → 本脚本直发 Cloudflare；
#   Mac 不再参与发布（deploy_pages.sh 保留作手动备用）。
# 依赖：node_modules/.bin/wrangler（npm ci 自动安装）、token 文件（600）。
# 由 run_daily.sh 在 FEDWATCH_PAGES_PUBLISH=1 时调用；也可手动执行。
set -Eeuo pipefail
DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$DIR"

TOKEN_FILE=${CLOUDFLARE_TOKEN_FILE:-$HOME/.config/cloudflare/fedwatch-pages.token}
ACCOUNT_ID=${CLOUDFLARE_ACCOUNT_ID:-187c1cfdd0c27418c93db3208513f556}
PROJECT=${CLOUDFLARE_PAGES_PROJECT:-fedwatch-tracker}
PROD_URL=${FEDWATCH_PAGES_URL:-https://fedwatch-tracker.pages.dev/}
WRANGLER="$DIR/node_modules/.bin/wrangler"

fail() { echo "publish_pages: $*" >&2; exit 1; }

[[ -f "$TOKEN_FILE" ]] || fail "token 文件不存在：$TOKEN_FILE"
TOKEN=$(cat "$TOKEN_FILE") || fail "无法读取 token"
[[ "$TOKEN" = cfut_* ]] || { unset TOKEN; fail "token 内容格式不正确"; }
[[ -x "$WRANGLER" ]] || { unset TOKEN; fail "缺少 wrangler：先 npm ci"; }
[[ -f "$DIR/report/index.html" ]] || { unset TOKEN; fail "report/index.html 不存在；先运行 build_report.py"; }

WRANGLER_LOG_SANITIZE=true \
CLOUDFLARE_API_TOKEN="$TOKEN" \
CLOUDFLARE_ACCOUNT_ID="$ACCOUNT_ID" \
  "$WRANGLER" pages deploy report \
    --project-name="$PROJECT" \
    --branch=main \
    --commit-dirty=true
DEPLOY_RC=$?
unset TOKEN
[[ $DEPLOY_RC -eq 0 ]] || fail "wrangler 部署失败（退出码 $DEPLOY_RC）"

# 部署后验证：生产别名应返回 200
sleep 3
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$PROD_URL")
[[ "$CODE" == 200 ]] || fail "部署后线上验证失败 http=$CODE"
echo "publish_pages: 已发布并验证 $PROD_URL"
