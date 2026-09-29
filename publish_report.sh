#!/usr/bin/env bash
# publish_report.sh — 把构建产物 report/ 发布到 nginx 站点目录（阿里云服务器使用）。
# 通过 releases/<时间戳> + current 符号链接实现近原子切换，保留最近 5 个版本。
# 服务器 systemd 服务通过 FEDWATCH_DEPLOY_SCRIPT 指向本脚本替代 deploy_pages.sh。
set -Eeuo pipefail
DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
WEBROOT=${FEDWATCH_WEBROOT:-/var/www/fedwatch}

[[ -f "$DIR/report/index.html" ]] || { echo "publish_report: report/index.html 不存在；先运行 build_report.py" >&2; exit 1; }
[[ -d "$WEBROOT/releases" ]] || { echo "publish_report: 站点目录不存在：$WEBROOT/releases（先跑 scripts/server/bootstrap.sh）" >&2; exit 1; }

REL="$WEBROOT/releases/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$REL"
rsync -a --delete "$DIR/report/" "$REL/"
# run_daily.sh 以 umask 077 构建；nginx(www-data) 需要可读。
chmod -R a+rX "$REL"
ln -sfn "$REL" "$WEBROOT/current.new" && mv -T "$WEBROOT/current.new" "$WEBROOT/current"
ls -1dt "$WEBROOT/releases"/* | tail -n +6 | xargs -r rm -rf
echo "published: $REL -> $WEBROOT/current"
