#!/bin/zsh
# 安装/更新每天 10:00 运行的用户级 LaunchAgent。
set -eu
set -o pipefail

DIR=${0:A:h}
LABEL=com.workbuddy.fedwatch-tracker.daily
SOURCE="$DIR/launchd/$LABEL.plist"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

mkdir -p "$HOME/Library/LaunchAgents" "$DIR/logs"
/usr/bin/plutil -lint "$SOURCE"
/usr/bin/install -m 644 "$SOURCE" "$DEST"

/bin/launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
/bin/launchctl bootstrap "$DOMAIN" "$DEST"
/bin/launchctl enable "$DOMAIN/$LABEL"

print -- "LaunchAgent 已安装：$DEST"
print -- "计划：每天本机时间 10:00 运行 $DIR/run_daily.sh"
/bin/launchctl print "$DOMAIN/$LABEL" | /usr/bin/grep -E 'path =|state =|last exit code|runs =|Hour|Minute' || true
