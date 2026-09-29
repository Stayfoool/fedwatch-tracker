# FedWatch Tracker 部署说明

> 项目的整体方向、阶段和待办统一维护在根目录 `ROADMAP.md`。本文只记录部署架构、操作步骤和验证状态。

## 当前架构（2026-09-30 起）

采集、构建、发布全部在**阿里云轻量服务器**（Debian 12，公网 `8.215.88.73`）上运行；
Mac 只做开发，通过 GitHub 中转部署。站点为纯静态，由服务器 nginx 直接托管：

- 公开站点：`http://8.215.88.73/`（nginx `default_server:80` → `/var/www/fedwatch/current`）
- 代码仓库：<https://github.com/Stayfoool/fedwatch-tracker>（公开）
- 每日采集：systemd timer `fedwatch-daily.timer`，北京时间 **05:30 与 06:30** 双触发
  （夏令时 05:30 命中、冬令时 06:30 命中芝加哥前一日 16:30 收盘窗口；不在窗口的那次由
  `fetch_quikstrike.py` 以 exit 10 自行跳过）
- 数据回流：采集成功后服务器把新增快照/主 CSV **回推 GitHub**（读写 Deploy Key），
  Mac 端 `git pull` 即可同步；归因任务（Mac，07:30）写完 `data/events.csv` 后
  commit+push 并触发服务器重建
- 旧 Cloudflare Pages 站点 `https://fedwatch-tracker.pages.dev/` 停止更新（历史存档）

## 服务器布局

```text
/opt/fedwatch-tracker          # 仓库克隆（属主 fedwatch 系统用户）
├── run_daily.sh               # 每日总入口（systemd 调用）
├── publish_report.sh          # report/ → /var/www/fedwatch/releases/<ts> + current 软链原子切换
├── sync_data_git.sh           # 采集成功后把新数据回推 GitHub（ExecStartPost）
└── logs/                      # logs/fetch_YYYYMMDD.log
/var/www/fedwatch/
├── releases/<时间戳>/          # 每次发布的完整静态站点，保留最近 5 份
└── current -> releases/...    # nginx root
/etc/fedwatch/ssh/             # GitHub 读写 Deploy Key（fedwatch 私有，600）
/etc/systemd/system/fedwatch-daily.{service,timer}
/etc/nginx/conf.d/fedwatch.conf  # 接管裸 IP:80 default_server
/usr/local/sbin/fedwatch-deploy  # root 运行的部署器（Mac deploy_server.sh 触发）
/var/log/fedwatch/deploy.log     # 部署日志
```

## 关键决策

- 发布目录是构建产物 `report/`；nginx 端 `try_files $uri $uri/ $uri.html` 与
  Cloudflare Pages 的 clean URL（如 `/curves`）保持一致。
- 构建时 `FEDWATCH_SITE_URL=http://8.215.88.73`（systemd 单元与 fedwatch-deploy 内置），
  canonical / sitemap / OG 指向服务器地址。将来绑定域名：改该环境变量 + nginx
  `server_name` + DNS/备案，不能只改环境变量。
- 服务器以 `fedwatch` 系统用户运行（nginx 只读 releases；构建与采集不碰 root）。
- 抓取会话固定注入 `Accept-Language: zh-CN`：QuikStrike 按 Accept-Language 决定区域格式，
  zh-CN 会话的会议日期（`YYYY/M/D`）与 Data-as-of（中文月名）正是下游解析器支持且
  Mac 端实测过的格式；`EXTRACT_JS` 同时兼容 en-US 的 `M/D/YYYY` 作为兜底。
- 首次部署用 `scripts/server/bootstrap.sh`（幂等）：系统依赖、Node 22、`agent-browser
  install`（托管 Chromium）、仓库克隆、Deploy Key、nginx、systemd、首次构建发布。
- 服务器内存 1.6G，已加 2G swap 保障 headless Chromium。

## 日常流程

```bash
# Mac：开发 → 部署（要求工作区干净、已推送）
git push origin main && ./deploy_server.sh

# 服务器：手动重建并发布当前 origin/main
ssh root@8.215.88.73 /usr/local/sbin/fedwatch-deploy main

# 服务器：手动触发一次每日采集（不在窗口内会自动跳过）
ssh root@8.215.88.73 'systemctl start fedwatch-daily.service'
journalctl -u fedwatch-daily.service -n 50        # 查看运行输出
systemctl list-timers fedwatch-daily.timer        # 查看下次触发
```

每日自动链路（北京时间）：

1. **05:30 / 06:30** `fedwatch-daily.service`：`run_daily.sh` → 抓取（失败 +10/+20 分钟重试，
   最多 3 次）→ `snapshot_pick.py` 自检 → `build_report.py` → `build_curves.py` →
   `analyze_changes.py --days 7` → `publish_report.sh` 发布 → `sync_data_git.sh`
   把新增数据 commit+push 回 GitHub。
2. **07:30** Mac 端 ZCode 归因自动化：`git pull --rebase` 同步数据 → 检测未归因日 →
   按 `docs/auto-attribution.md` 检索归因 → 本地构建 + JS 语法校验 →
   commit+push → `./deploy_server.sh` 触发服务器重建发布。

## 密钥

- GitHub Deploy Key：`/etc/fedwatch/ssh/id_ed25519_github_push`（仅 fedwatch 可读，600），
  对应仓库 Deploy Key `aliyun-fedwatch-server`（read-write）；只用于数据回推。
- 服务器 SSH：Mac `~/.ssh/config` 已有 `8.215.88.73` 别名（root + ed25519）。
- Cloudflare API token 仍只在 Mac 本机 `~/.config/cloudflare/fedwatch-pages.token`，
  仅 `deploy_pages.sh`（可选的 Pages 发布）使用；迁移后日常流程不再触碰 Cloudflare。

## 服务器初始化（已执行，存档备查）

```bash
scp scripts/server/bootstrap.sh root@8.215.88.73:/tmp/ && \
  ssh root@8.215.88.73 'bash /tmp/fedwatch-bootstrap.sh'
# 按输出把公钥添加为仓库 Deploy Key（允许写）：
#   ssh root@8.215.88.73 'cat /etc/fedwatch/ssh/id_ed25519_github_push.pub' > /tmp/dk.pub
#   gh repo deploy-key add /tmp/dk.pub --title aliyun-fedwatch-server --allow-write
# 再次运行 bootstrap 或 fedwatch-deploy 完成首次发布
```

bootstrap 会把 `va2t.conf` 里原来的 `default_server`（`return 444` 空吞块）备份后移除，
交给 `fedwatch.conf`；`va2t.giftern.cn` 的 80/443 域名站不受影响（已验证：301/401 如常）。

## 验证记录（2026-09-30 凌晨）

- [x] 服务器全链路：`fetch_quikstrike.py --dry --force --json` 在服务器返回 10 个会议、
      `current_target=375-400`、Data-as-of 中文格式（zh-CN 会话生效）
- [x] 窗口守卫：非窗口时段真实运行返回 rc=10（out_of_window）
- [x] `fedwatch-deploy`：checkout → py_compile → snapshot_pick 自检 → 构建（18 可索引页 +
      curves 页）→ publish 原子切换
- [x] 线上：首页 200、canonical=`http://8.215.88.73/`、`/curves` 200、history/en/about/methodology
      301、robots/sitemap/CSV 200、不存在路径 404、`_headers` 404
- [x] Deploy Key 回推：`git push --dry-run` 通过
- [x] timer 就绪：NEXT = 次日 05:30:00 CST
- [x] `va2t.giftern.cn`：HTTP 301 → HTTPS 401（basic auth），与迁移前一致
- [x] Mac LaunchAgent 已停用移除（`install_launchagent.sh` 保留，需要回切时重装即可）

## 已知问题与后续

- 首个自动采集日（迁移后第一个交易日）建议核对 `journalctl -u fedwatch-daily` 与
  GitHub 上的数据回流 commit。
- 服务器无独立域名前站点为 `http://` 明文 + 裸 IP；SEO 长期方案是绑定域名
  （需备案）后切 `FEDWATCH_SITE_URL` 并做 301。
- `pages.dev` 旧站冻结在 2026-09-29 数据；如需下线或 301 到新站，在 Cloudflare 控制台操作。
- Mac 端仍可随时 `zsh run_daily.sh` + `deploy_pages.sh` 双写 Pages（需重装 LaunchAgent），
  但注意两边数据以 GitHub 为准，先 `git pull`。
