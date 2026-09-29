#!/usr/bin/env bash
# bootstrap.sh — 阿里云轻量服务器（Debian 12）初始化脚本，root 运行，可重复执行。
# 职责：系统依赖、Node 22、fedwatch 用户、仓库克隆、agent-browser、部署密钥、
#       nginx 站点（接管裸 IP:80）、systemd 定时器、首次构建发布。
# 用法：scp 到服务器后 `bash bootstrap.sh`；日常部署不要跑它，用 /usr/local/sbin/fedwatch-deploy。
set -Eeuo pipefail

APP=/opt/fedwatch-tracker
RUN_AS=fedwatch
WEBROOT=/var/www/fedwatch
KEY_DIR=/etc/fedwatch/ssh
KEY_FILE=$KEY_DIR/id_ed25519_github_push
REPO_URL=https://github.com/Stayfoool/fedwatch-tracker.git
SSH_URL=git@github.com:Stayfoool/fedwatch-tracker.git
NODE_MAJOR=22

log() { echo "[bootstrap] $*"; }

[[ "$(id -u)" == 0 ]] || { echo "必须以 root 运行" >&2; exit 1; }

# ---------- 1. 系统依赖 ----------
log "安装系统依赖"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq zsh rsync git curl ca-certificates gnupg \
  libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
  libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 \
  libasound2 libpango-1.0-0 libcairo2 libxshmfence1 fonts-liberation >/dev/null

# 小内存机器抓取时要起 headless Chrome，缺 swap 就补一个
if ! swapon --show --noheadings | grep -q .; then
  log "无 swap，创建 2G /swapfile"
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap -q /swapfile && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# ---------- 2. Node.js ----------
NODE_OK=0
if command -v node >/dev/null; then
  NODE_OK=$(node -p 'process.versions.node.split(".")[0]' )
  [[ "$NODE_OK" -ge 20 ]] && NODE_OK=1 || NODE_OK=0
else
  NODE_OK=0
fi
if [[ "$NODE_OK" != 1 ]]; then
  log "安装 Node.js $NODE_MAJOR（NodeSource）"
  curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash - >/dev/null
  apt-get install -y -qq nodejs >/dev/null
fi
log "node $(node --version)"

# ---------- 3. 用户与目录 ----------
id -u "$RUN_AS" >/dev/null 2>&1 || useradd -r -m -s /usr/sbin/nologin "$RUN_AS"
mkdir -p "$APP" "$WEBROOT/releases" /var/log/fedwatch "$KEY_DIR"
chown "$RUN_AS:$RUN_AS" "$APP" "$WEBROOT" "$WEBROOT/releases" /var/log/fedwatch
chmod 700 "$KEY_DIR"

# ---------- 4. 仓库 ----------
if [[ ! -d "$APP/.git" ]]; then
  log "克隆仓库 $REPO_URL"
  runuser -u "$RUN_AS" -- git clone --quiet "$REPO_URL" "$APP"
else
  runuser -u "$RUN_AS" -- git -C "$APP" fetch --quiet origin || true
fi

# ---------- 5. npm 依赖 + agent-browser（含托管 Chrome） ----------
cd "$APP"
LOCK_SHA=$(sha256sum "$APP/package-lock.json" | cut -d' ' -f1)
if [[ ! -d "$APP/node_modules" || "$LOCK_SHA" != "$(cat "$APP/.node-modules.sha" 2>/dev/null || echo none)" ]]; then
  log "npm ci（agent-browser + wrangler 固定版本）"
  runuser -u "$RUN_AS" -- bash -c "cd '$APP' && npm ci --no-fund --no-audit --silent"
  echo "$LOCK_SHA" > "$APP/.node-modules.sha"
  chown "$RUN_AS:$RUN_AS" "$APP/.node-modules.sha"
fi
log "agent-browser install（下载托管 Chromium，首次约 1-2 分钟）"
runuser -u "$RUN_AS" -- bash -c "cd '$APP' && export PATH='$APP/node_modules/.bin:\$PATH' && agent-browser install --with-deps 2>/dev/null || agent-browser install" \
  || log "警告：agent-browser install 失败，稍后用 doctor 排查"

# ---------- 6. GitHub 部署密钥（回推每日数据） ----------
if [[ ! -f "$KEY_FILE" ]]; then
  log "生成部署密钥 $KEY_FILE"
  runuser -u "$RUN_AS" -- ssh-keygen -q -t ed25519 -N '' -C 'fedwatch-server-deploy' -f "$KEY_FILE"
fi
chown -R "$RUN_AS:$RUN_AS" "$KEY_DIR"
chmod 600 "$KEY_FILE"
if [[ ! -f "$KEY_DIR/known_hosts" ]] || ! grep -q github.com "$KEY_DIR/known_hosts"; then
  ssh-keyscan -t ed25519,rsa github.com 2>/dev/null >> "$KEY_DIR/known_hosts"
fi
echo
echo "==================== 把下面这行公钥添加为仓库 Deploy Key（允许写）===================="
cat "$KEY_FILE.pub"
echo "====================================================================================="
echo

# 配置仓库走 SSH + 专用密钥（密钥已生成但尚未在 GitHub 侧授权时，push 会失败，属预期）
runuser -u "$RUN_AS" -- git -C "$APP" remote set-url origin "$SSH_URL"
runuser -u "$RUN_AS" -- git -C "$APP" config core.sshCommand \
  "ssh -i $KEY_FILE -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$KEY_DIR/known_hosts"

# ---------- 7. nginx：fedwatch 接管裸 IP:80 ----------
log "安装 nginx 站点配置"
cp "$APP/scripts/nginx/fedwatch.conf" /etc/nginx/conf.d/fedwatch.conf
# va2t.conf 的 default_server（return 444 空吞块）与 fedwatch 冲突：备份后移除该块
python3 - <<'PY'
import re, shutil, pathlib
p = pathlib.Path('/etc/nginx/conf.d/va2t.conf')
s = p.read_text()
pat = re.compile(r'server\s*\{[^{}]*?listen\s+80\s+default_server;.*?return\s+444;\s*\}\n', re.S)
if pat.search(s):
    shutil.copy(p, str(p) + '.backup-fedwatch-' + __import__('time').strftime('%Y%m%d%H%M%S'))
    p.write_text(pat.sub('', s, count=1))
    print('[bootstrap] va2t.conf: 已移除 80 端口空吞 default_server 块（已备份）')
else:
    print('[bootstrap] va2t.conf: 未发现空吞块，无需修改')
PY
if nginx -t 2>/dev/null; then
  systemctl reload nginx
else
  echo "nginx -t 失败，回滚 va2t.conf" >&2
  B=$(ls -1t /etc/nginx/conf.d/va2t.conf.backup-fedwatch-* 2>/dev/null | head -1 || true)
  [[ -n "$B" ]] && cp "$B" /etc/nginx/conf.d/va2t.conf
  nginx -t && systemctl reload nginx
  exit 1
fi

# ---------- 8. systemd 定时任务 ----------
log "安装 systemd 服务与定时器"
cp "$APP/scripts/systemd/fedwatch-daily.service" /etc/systemd/system/
cp "$APP/scripts/systemd/fedwatch-daily.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --quiet --now fedwatch-daily.timer

# ---------- 9. 部署器 + 首次发布 ----------
install -o root -g root -m 0755 "$APP/scripts/server/fedwatch-deploy" /usr/local/sbin/fedwatch-deploy
log "首次构建与发布（build_report → build_curves → publish）"
/usr/local/sbin/fedwatch-deploy main

log "完成。定时任务：$(systemctl list-timers fedwatch-daily.timer --no-pager | sed -n 2p)"
log "站点：http://8.215.88.73/   （nginx root → $WEBROOT/current）"
