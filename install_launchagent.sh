#!/bin/zsh
# 安装/更新 LaunchAgent：每天北京 05:30 与 06:30 各触发一次
# （夏令时由 05:30 命中、冬令时由 06:30 命中，折算到芝加哥都是前一日 16:30 CT）。
set -eu
set -o pipefail

DIR=${0:A:h}
LABEL=com.workbuddy.fedwatch-tracker.daily
SOURCE="$DIR/launchd/$LABEL.plist"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

mkdir -p "$HOME/Library/LaunchAgents" "$DIR/logs"
# 模板里的 REPLACE_WITH_ABSOLUTE_REPO_PATH 在安装时替换为实际仓库路径，
# 仓库本身保持路径无关，换目录后重跑本脚本即可。
/usr/bin/sed "s|REPLACE_WITH_ABSOLUTE_REPO_PATH|$DIR|g" "$SOURCE" > "$DEST"
/usr/bin/plutil -lint "$DEST"

/bin/launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
/bin/launchctl bootstrap "$DOMAIN" "$DEST"
/bin/launchctl enable "$DOMAIN/$LABEL"

print -- "LaunchAgent 已安装：$DEST"
print -- "计划：每天本机时间 05:30 与 06:30 各触发一次 $DIR/run_daily.sh"
print -- "      （不在采集窗口内的那次由脚本自行跳过）"
/bin/launchctl print "$DOMAIN/$LABEL" | /usr/bin/grep -E 'path =|state =|last exit code|runs =|Hour|Minute' || true
