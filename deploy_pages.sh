#!/bin/zsh
# 安全地把 report/ 发布到现有 Cloudflare Pages 生产项目。
# Token 只从仓库外的 600 权限文件读取，绝不回显或写入日志。

set -u
set -o pipefail
umask 077

DIR=${0:A:h}
REPORT_DIR="$DIR/report"
INDEX_FILE="$REPORT_DIR/index.html"
TOKEN_FILE=${CLOUDFLARE_TOKEN_FILE:-$HOME/.config/cloudflare/fedwatch-pages.token}
PAGES_CACHE="$DIR/.wrangler/cache/pages.json"
WRANGLER="$DIR/node_modules/.bin/wrangler"
EXPECTED_WRANGLER_VERSION="4.131.1"
PROJECT_NAME="fedwatch-tracker"
PRODUCTION_BRANCH="main"
PRODUCTION_URL="https://fedwatch-tracker.pages.dev/"

fail() {
  print -u2 -- "ERROR: $*"
  exit 1
}

[[ -f "$INDEX_FILE" ]] || fail "找不到部署产物：$INDEX_FILE"
[[ -s "$INDEX_FILE" ]] || fail "部署产物为空：$INDEX_FILE"
[[ -f "$TOKEN_FILE" && ! -L "$TOKEN_FILE" ]] || fail "Token 文件不存在、不是普通文件或为符号链接：$TOKEN_FILE"

TOKEN_MODE=$(stat -f '%Lp' "$TOKEN_FILE" 2>/dev/null) || fail "无法读取 Token 文件权限"
TOKEN_OWNER=$(stat -f '%Su' "$TOKEN_FILE" 2>/dev/null) || fail "无法读取 Token 文件所有者"
[[ "$TOKEN_MODE" = "600" ]] || fail "Token 文件权限必须为 600，当前为 $TOKEN_MODE"
[[ "$TOKEN_OWNER" = "$USER" ]] || fail "Token 文件所有者必须是当前用户 $USER，当前为 $TOKEN_OWNER"

TOKEN=$(cat "$TOKEN_FILE")
[[ "$TOKEN" = cfut_* ]] || { unset TOKEN; fail "Token 文件内容格式不正确"; }
[[ "$TOKEN" != *[[:space:]]* ]] || { unset TOKEN; fail "Token 中含有空白字符"; }

[[ -x "$WRANGLER" ]] || { unset TOKEN; fail "缺少固定版本 Wrangler；请先在项目目录运行 npm install"; }
ACTUAL_WRANGLER_VERSION=$($WRANGLER --version 2>/dev/null | tail -1 | tr -d '[:space:]')
[[ "$ACTUAL_WRANGLER_VERSION" = "$EXPECTED_WRANGLER_VERSION" ]] || {
  unset TOKEN
  fail "Wrangler 版本不匹配：期望 $EXPECTED_WRANGLER_VERSION，实际 $ACTUAL_WRANGLER_VERSION"
}

[[ -f "$PAGES_CACHE" ]] || { unset TOKEN; fail "找不到 Pages 项目配置：$PAGES_CACHE"; }
ACCOUNT_ID=$(/usr/bin/python3 - "$PAGES_CACHE" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    print(json.load(f).get("account_id", ""))
PY
)
[[ ${#ACCOUNT_ID} -eq 32 && "$ACCOUNT_ID" != *[^0-9a-f]* ]] || { unset TOKEN; fail "Pages account_id 配置无效"; }

print -- "=== Cloudflare Pages 自动部署 $(date '+%F %T %Z')"
print -- "项目：$PROJECT_NAME；目录：$REPORT_DIR；分支：$PRODUCTION_BRANCH"

WRANGLER_LOG_SANITIZE=true \
CLOUDFLARE_API_TOKEN="$TOKEN" \
CLOUDFLARE_ACCOUNT_ID="$ACCOUNT_ID" \
  "$WRANGLER" pages deploy "$REPORT_DIR" \
    --project-name="$PROJECT_NAME" \
    --branch="$PRODUCTION_BRANCH" \
    --commit-dirty=true
DEPLOY_RC=$?
unset TOKEN

[[ $DEPLOY_RC -eq 0 ]] || fail "Wrangler 部署失败（退出码 $DEPLOY_RC）"

# 等待 production alias 完成传播，并验证线上正文与本地产物逐字节一致。
LOCAL_SHA=$(/usr/bin/shasum -a 256 "$INDEX_FILE" | /usr/bin/awk '{print $1}')
VERIFY_FILE=$(mktemp -t fedwatch-pages-verify.XXXXXX)
trap '/bin/rm -f "$VERIFY_FILE"' EXIT INT TERM

for attempt in {1..12}; do
  CACHE_BUSTER=$(date +%s)-$attempt
  if /usr/bin/curl -fsS --connect-timeout 10 --max-time 30 \
      -H 'Cache-Control: no-cache' \
      "${PRODUCTION_URL}?deploy_verify=${CACHE_BUSTER}" \
      -o "$VERIFY_FILE"; then
    REMOTE_SHA=$(/usr/bin/shasum -a 256 "$VERIFY_FILE" | /usr/bin/awk '{print $1}')
    if [[ "$REMOTE_SHA" = "$LOCAL_SHA" ]]; then
      AUX_OK=1
      for asset in robots.txt sitemap.xml history/ data/fedwatch-probabilities.csv; do
        LOCAL_ASSET="$REPORT_DIR/$asset"
        [[ -d "$LOCAL_ASSET" ]] && LOCAL_ASSET="${LOCAL_ASSET%/}/index.html"
        REMOTE_ASSET=$(mktemp -t fedwatch-pages-asset.XXXXXX)
        if ! /usr/bin/curl -fsS --connect-timeout 10 --max-time 30 \
            -H 'Cache-Control: no-cache' "${PRODUCTION_URL}${asset}?deploy_verify=${CACHE_BUSTER}" \
            -o "$REMOTE_ASSET"; then
          AUX_OK=0
        elif [[ "$(/usr/bin/shasum -a 256 "$LOCAL_ASSET" | /usr/bin/awk '{print $1}')" != \
                "$(/usr/bin/shasum -a 256 "$REMOTE_ASSET" | /usr/bin/awk '{print $1}')" ]]; then
          AUX_OK=0
        fi
        /bin/rm -f "$REMOTE_ASSET"
      done

      ROBOTS_TYPE=$(/usr/bin/curl -sS -o /dev/null -w '%{content_type}' "${PRODUCTION_URL}robots.txt?deploy_verify=${CACHE_BUSTER}")
      SITEMAP_TYPE=$(/usr/bin/curl -sS -o /dev/null -w '%{content_type}' "${PRODUCTION_URL}sitemap.xml?deploy_verify=${CACHE_BUSTER}")
      NOT_FOUND_STATUS=$(/usr/bin/curl -sS -o /dev/null -w '%{http_code}' "${PRODUCTION_URL}definitely-not-a-real-page-${CACHE_BUSTER}")
      [[ "$ROBOTS_TYPE" = text/plain* ]] || AUX_OK=0
      [[ "$SITEMAP_TYPE" = application/xml* || "$SITEMAP_TYPE" = text/xml* ]] || AUX_OK=0
      [[ "$NOT_FOUND_STATUS" = "404" ]] || AUX_OK=0

      if [[ $AUX_OK -eq 1 ]]; then
        print -- "VERIFIED: production 首页、robots、sitemap、历史页与数据下载均和本地一致"
        print -- "VERIFIED: robots=text/plain，sitemap=XML，不存在路径返回 404"
        print -- "生产地址：$PRODUCTION_URL"
        exit 0
      fi
    fi
  fi
  if [[ $attempt -lt 12 ]]; then
    print -- "生产别名尚未同步，5 秒后再次验证（$attempt/12）"
    sleep 5
  fi
done

fail "部署命令已成功，但 60 秒内 production 内容未与本地产物一致"
