# FedWatch Tracker 部署说明

> 项目的整体方向、阶段和待办统一维护在根目录 `ROADMAP.md`。本文只记录部署架构、操作步骤和验证状态。

## 当前目标

在每日采集和站点构建成功后，将完整 `report/` 静态目录安全发布到现有 Cloudflare Pages 生产站点 `https://fedwatch-tracker.pages.dev/`；不新建 Pages 项目。

## 关键决策

- 部署目录是 `report/`，不是项目根目录。
- 站点是预生成静态站点，无服务端运行时；首页保留原生 SVG/JavaScript 交互。
- 使用已有 Pages 项目 `fedwatch-tracker` 的 Direct Upload / production `main` 分支覆盖。
- API token 不写入项目、脚本、文档或日志；仅从项目外权限为 `600` 的文件读取。
- 使用固定版本的官方 Wrangler CLI，避免每日运行时漂移到未经验证的新版本。
- macOS LaunchAgent 每天北京时间 **05:30 与 06:30** 各触发一次 `run_daily.sh`
  （夏令时由 05:30 命中、冬令时由 06:30 命中，折算到芝加哥都是前一日 16:30 CT 的收盘后休市间隙）；
  不在采集窗口内的那次由 `fetch_quikstrike.py` 以 exit 10 跳过，不重试、不发布；发布失败返回非零并写入日志。
- `FEDWATCH_SITE_URL` 控制构建时的 canonical 基址；未设置时为 `https://fedwatch-tracker.pages.dev`。
- 维护者身份、组织、邮箱和联系方式当前不写入页面或结构化数据。

## 架构摘要

1. `fetch_quikstrike.py` 抓取并归档概率数据。
2. `build_report.py` 构建交互首页，并调用 `site_seo.py` 生成多页静态站点。
3. 构建器校验 metadata、canonical、结构化数据、内部链接、sitemap、CSV 和图片资源。
4. `deploy_pages.sh` 将整个 `report/` 上传到现有 Cloudflare Pages 项目。
5. 部署后脚本对线上首页、robots、sitemap、历史页、CSV、Content-Type 和 404 状态做生产验证。

当前 `report/` 的主要产物：

```text
report/
├── index.html
├── history/index.html
├── methodology/index.html
├── data/index.html
├── about/index.html
├── en/index.html
├── meetings/<YYYY-MM-DD>/index.html
├── data/fedwatch-probabilities.csv
├── data/meetings/<YYYY-MM-DD>.csv
├── robots.txt
├── sitemap.xml
├── llms.txt
├── google368ccf4bdf1ec30d.html
├── 404.html
├── _headers
├── favicon.svg
└── assets/og-image.{svg,png}
```

## 部署状态（2026-09-15）

- [x] 固定并安装官方 Wrangler CLI `4.131.1`
- [x] 固定兼容现有抓取流程的官方 `agent-browser@0.27.0`
- [x] 安全部署脚本检查 token 文件类型、所有者和权限
- [x] `run_daily.sh` 接入 Pages 发布，并对暂时性发布失败重试
- [x] 安装并加载 macOS LaunchAgent（每日 05:30 / 06:30 双触发）
- [x] 从单页看板升级为多页 SEO 静态站点
- [x] 生成 robots、sitemap、真实 404、canonical、结构化数据和 CSV 下载
- [x] 构建失败阻止发布旧的或不完整的站点
- [x] 实际发布并验证生产 deployment `be2bb859`

## 当前线上结果

- 生产 URL：`https://fedwatch-tracker.pages.dev/`
- 已验证 deployment：`be2bb859`
- Unique URL：`https://be2bb859.fedwatch-tracker.pages.dev/`
- 首页标题：`FedWatch Tracker：美联储加息与降息概率历史 | Fed Rate Probability History`
- sitemap 中有 18 个可索引页面
- `robots.txt`：纯文本，允许抓取并声明 sitemap
- `sitemap.xml`：有效 XML
- 不存在路径：HTTP 404
- 首页、robots、sitemap、历史页和完整 CSV：线上与本地产物一致

## 已实施流程

1. LaunchAgent 每天本机时间 05:30 与 06:30 各调用一次 `run_daily.sh`；只有落在
   芝加哥时间 `[16:02, 16:58)` 休市间隙内的那次会真正采集，另一次以 exit 10 跳过。
2. 抓取最多尝试 3 次（间隔 10 分钟，仍在窗口内），成功后重建完整站点；构建或验证失败不发布。
   抓取返回 10/11/12（不在窗口 / 该交易日已有收盘读数 / 数据未变）视为正常跳过，不重试也不发布。
3. 重大变动检测为非阻断步骤。
4. Pages 发布最多尝试 3 次；每次由固定版本 Wrangler 上传 production `main`。
5. `deploy_pages.sh` 等待 production alias 传播，然后验证：
   - 首页 SHA-256 与本地一致；
   - robots、sitemap、history 页面和 CSV 与本地一致；
   - robots Content-Type 为 `text/plain`；
   - sitemap Content-Type 为 XML；
   - 随机不存在路径返回 404。

## 手动构建与部署

```bash
PY=python3
DIR=/path/to/fedwatch-tracker

$PY $DIR/build_report.py
zsh $DIR/deploy_pages.sh
```

完整日常流程：

```bash
zsh $DIR/run_daily.sh
```

如将来绑定独立域名，构建前设置无末尾斜杠的规范地址：

```bash
FEDWATCH_SITE_URL=https://example.com $PY $DIR/build_report.py
```

同时必须完成 Cloudflare custom domain、旧域名 301、Search Console/Bing 新属性、canonical 和 sitemap 的线上复核；不能只修改环境变量。

## Google Search Console 所有权验证

- 主要验证方式：生产首页 `<head>` 中长期保留 `google-site-verification` meta。
- 备用验证文件：`static/google368ccf4bdf1ec30d.html`；`site_seo.py` 在每次构建时复制到 `report/` 并校验内容，避免后续部署误删。
- 验证文件和 meta 只证明对站点部署内容的控制权，不包含维护者姓名、邮箱或联系方式。
- 不要在日常重构中删除验证 meta 或验证文件；删除后 Google 可能撤销已验证状态。

## 密钥

- API token、upload JWT **不写入项目**；account ID 只存在被忽略的本地 Wrangler cache。
- 本机 token 文件：`~/.config/cloudflare/fedwatch-pages.token`，父目录权限 `700`、文件权限 `600`。
- `deploy_pages.sh` 强制检查 token 文件不是符号链接、所有者为当前用户且权限精确为 `600`。
- token 仅传给单个 Wrangler 子进程，不写进 `docs/`、`.wrangler/`、命令回显或日志。

## 验证记录（2026-09-15）

- [x] `python3 -m py_compile site_seo.py build_report.py`
- [x] `python3 build_report.py` 生成 18 个可索引页面并通过本地站点验证
- [x] 桌面首页、历史索引页和移动端历史索引页浏览器 QA 通过
- [x] 浏览器控制台 0 error / 0 warning
- [x] Wrangler 创建 deployment `be2bb859`
- [x] production alias 与本地产物一致
- [x] robots=`text/plain`、sitemap=XML、随机不存在路径=404

## 风险与后续

- 电脑关机或长期未登录时，用户级 LaunchAgent 无法运行；恢复登录后需检查日志。
- QuikStrike 抓取依赖浏览器和网络，失败时不会发布旧报告。
- `pages.dev` 可被搜索引擎收录，但独立域名更适合长期品牌和外链积累；域名购买与绑定尚未执行。
- Google Search Console URL-prefix property 已于 2026-09-15 通过 HTML meta 验证；`sitemap.xml` 已提交但即时状态为 `Couldn't fetch`，线上独立检查为 HTTP 200、XML Content-Type 且 Googlebot 可访问，需等待重读。
- 首页 Request Indexing 于 2026-09-15 因 Google 当日配额已满未提交成功，需在次日或之后重试；Bing Webmaster Tools 尚未配置。
- 需要轮换此前在聊天中暴露过的 Cloudflare API token。
- 后续更新继续部署 `report/` 到现有项目 `fedwatch-tracker`，不要新建 Pages 项目。
