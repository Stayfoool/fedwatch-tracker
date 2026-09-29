# FedWatch Tracker 公开站点发现性与可信度路线图

> 本文件是项目的 primary living plan。当前目标、SEO / AI 发现性方案、执行状态、风险和验收统一维护在这里；部署细节见 `docs/deployment.md`，事件归因细节见 `docs/event-attribution.md`。

## 当前目标

让公开站点不只“可以通过链接打开”，还能够：

1. 被 Google、Bing 等搜索引擎稳定抓取、收录和更新；
2. 对“FedWatch tracker”“美联储加息概率历史”“Fed rate probability history”等查询提供可排名、可引用的独立页面；
3. 通过清晰的数据来源、方法、项目边界、稳定 URL 和外部引用，逐步成为搜索及 AI 检索回答可引用的可信来源；
4. 保持每日数据自动更新与现有交互看板功能不受影响。

## 当前状态（2026-09-17）

- 生产站点：`https://fedwatch-tracker.pages.dev/`
- 当前已验证 deployment：`be2bb859`，unique URL：`https://be2bb859.fedwatch-tracker.pages.dev/`
- 托管：继续使用现有 Cloudflare Pages 项目 `fedwatch-tracker`，不新建项目，不因 SEO 迁移托管平台。
- 规范域名：目前仍为 `pages.dev`；独立域名尚未购买或绑定。
- 站点规模：18 个可索引 HTML 页面，另有 robots、sitemap、CSV、OG 图片、favicon、`llms.txt` 和真实 404。
- 项目身份：页面明确为独立项目、与 CME 和 Federal Reserve 无隶属关系；**维护者姓名、组织、邮箱和联系方式暂不公开**。
- 外部提交：Google Search Console URL-prefix property 已通过 HTML meta 验证；`sitemap.xml` 已提交，仍需复核状态并重试首页 Request Indexing。Bing Webmaster Tools 尚未完成验证与 sitemap 提交。2026-09-17 再次尝试时，自动化浏览器没有 Google 或 Microsoft 已登录会话；需站点所有者在浏览器完成登录（包括可能的 MFA）后继续。

## 2026-09-17 时间轴改美东交易日（已上线）

- [x] **采集挪到清晨**：LaunchAgent 改为北京 05:30 / 06:30 双触发，由
      `COLLECT_WINDOW = [16:02, 16:58) CT` 放行正确的那一次（夏令时 05:30、冬令时 06:30，
      折算到芝加哥都是前一日 16:30 CT）。**北京 06:00 整点两个时制都不成立**，已核实。
- [x] **横轴改美东交易日**：新增派生列 `us_trade_date` / `data_asof_ct` / `time_basis`，
      一个点 = 一个美东交易日的收盘定格；`snapshot_cn`（北京采集时刻）保留为排序与关联键不动。
- [x] **读页面自带时间戳**：`Data as of … CT` 只在默认视图存在、切到 Aggregated 后消失，
      因此必须在点击 Aggregated **之前**读取；旧代码读不到它的原因就在这里。
- [x] **周末去重采用 A 方案**：周六/周日/周一早上的三次采集读到同一份周五收盘，
      按「一个交易日一个点」去重、保留最早一次，每周固定 5 个点。
- [x] **过渡期处理**：旧的 5 个北京 10:00 盘中点标记为 `legacy_intraday`，
      不阻挡新口径采集；同一交易日出现两种口径时，收盘定格优先（展示层去重）。
- [x] CSV 已迁移（2815 行，自动备份），自检通过，站点已重建。

## 2026-09-15 采集口径确认

- [x] 公开网站不追踪 FedWatch 盘中变动；以北京时间每天 10:00 的成功抓取为准。
      （**已于 2026-09-17 改为清晨采集口径，见上节**）
- [x] 10:10、10:20 仅在前一次失败时重试，不构成额外的常规快照。
- [x] 清理 2026-09-15 诊断期间手动加入的 12:05 快照，线上恢复为 10:00 快照（9/16 加息概率 92.39%）。
- [ ] 评估是否将周日历史回填从自动主流程改为手动维护；它只补历史日期级数据，不应被理解为盘中更新。

## 关键决策

- Cloudflare Pages 本身不会天然导致“不易被搜索”；主要问题是站点新、原先只有单页 JS 图表、缺少 robots/sitemap/稳定内容页和外部引用。
- 不把“提交搜索引擎”误当作排名方案：提交只解决发现，排名仍取决于内容是否回答查询、站点可信度、外部引用和持续运营。
- 不依赖 `llms.txt` 作为核心方案；核心是标准 HTML、robots、sitemap、结构化数据、稳定 URL、可下载数据和权威引用。
- 不批量生成只有日期或数字不同的薄内容页。每个会议页都提供最新分布、逐日历史表、时间、来源和方法入口。
- 默认规范域名由 `FEDWATCH_SITE_URL` 控制；未设置时使用 `https://fedwatch-tracker.pages.dev`。未来绑定独立域名后，应同步更新 canonical、sitemap 和重定向。
- 品牌和免责声明明确本站是独立项目，不是 CME 或 Federal Reserve 官方站点。
- 暂不披露维护者信息，因此不生成 `Person` / `Organization` 作者实体、邮箱或联系页面；后续只有在明确授权后才调整。

## 实施阶段

### P0 — 技术可发现性（最高优先级）

- [x] 在 `report/` 生成真实 `robots.txt`，允许标准搜索抓取，明确允许 `OAI-SearchBot` 并声明 sitemap 地址。
- [x] 生成有效 `sitemap.xml`，包含 18 个规范 URL 与 `lastmod`；每日构建同步更新。
- [x] 生成真实 `404.html`，不存在路径返回 HTTP 404，不再 soft-404 / SPA fallback。
- [x] 首页及支持页面添加中英双语 title、meta description、canonical、robots meta、Open Graph、Twitter Card、favicon 和 hreflang。
- [x] 添加与页面可见内容一致的 `WebSite`、`Dataset`、`DataDownload` JSON-LD；按当前隐私要求不添加维护者实体。
- [x] 为 Google Search Console 添加 URL-prefix property，并通过首页 HTML meta 完成所有权验证；验证 meta 和备用 HTML 文件必须长期保留。
- [x] 向 Google Search Console 提交 `sitemap.xml`；2026-09-15 首次提交成功，但即时状态为 `Couldn't fetch`，线上复核为 HTTP 200、XML Content-Type 且 Googlebot 可访问，等待 Google 后续重读。
- [ ] 对首页完成 URL Inspection / Request Indexing；当前检查结果为“URL is unknown to Google”，2026-09-15 提交时遇到 Google 当日 quota exceeded，需在 2026-09-16 或之后重试。
- [ ] 配置 Bing Webmaster Tools 并提交 sitemap；绑定独立域名后再评估 Cloudflare Crawler Hints / IndexNow。
- [x] 在本地构建和部署验证中检查 robots 正文/Content-Type、sitemap XML、404 状态、canonical、结构化数据、站内链接和关键静态资源。

### P1 — 可索引的信息架构与内容

- [x] 建立中英查询入口，覆盖“美联储加息/降息概率”“历史 FedWatch 概率”“FOMC meeting probabilities”等主题。
- [x] 为 11 个未来 FOMC 会议生成稳定页面，例如 `/meetings/2026-10-28/`，包含最新概率、历史表、来源、更新时间和口径说明。
- [x] 建立 `/history/`、`/analysis/`、`/methodology/`、`/data/`、`/about/`、`/en/` 页面，并通过普通 `<a href>` 形成可抓取的站内结构。
- [x] 发布完整 CSV 和按会议拆分 CSV，在数据页说明字段、更新时间、来源和使用边界。
- [x] 把关键图表数据同时输出为语义化 HTML 表格和文字摘要，不再只依赖 JavaScript JSON。
- [x] 每日自动生成可引用的自然语言最新概率摘要。
- [x] 增加数据出处、独立站免责声明和项目边界说明。
- [ ] 维护者姓名、组织、邮箱和联系方式：按当前要求暂缓披露，不作为上线阻塞项。

### P2 — 品牌、外链与分发

- [ ] 购买并绑定短、可记忆、与官方品牌不混淆的独立域名；确定长期唯一站名和中英文一句话定位。
- [x] 建立公开 GitHub 仓库/项目页 `https://github.com/Stayfoool/fedwatch-tracker`，并让 README 与站点互相链接；公开前已清理密钥、个人路径和不宜披露的信息。
- [x] 将已有 `events.csv` 事件归因整理为可索引的 `/analysis/` 页面；明确“待核实”状态和非因果模型边界。
- [ ] 发布有独立分析价值的周报或事件复盘，而不是只展示原始概率；文章引用官方数据并回链对应会议历史页。
- [ ] 向相关财经社区、研究者、记者、newsletter 和中文金融内容平台做人工且相关的介绍，争取真实引用；禁止购买垃圾外链或批量目录提交。
- [ ] 提供方便引用的图表截图、CSV、嵌入说明和中性站点署名格式；暂不要求披露维护者身份。

### P3 — AI 检索可见性

- [x] robots 不误封用于搜索的抓取器；当前允许通用抓取并明确允许 `OAI-SearchBot`。
- [x] 重要页面首屏文本直接说明页面回答的问题，并包含日期、口径、来源和可验证数据。
- [x] 建立稳定 URL、canonical、可抓取 HTML、结构化数据和 `llms.txt` 补充说明。
- [ ] 用固定问题每月测试 Google、Bing/Copilot、ChatGPT Search、豆包等是否能发现或引用本站，记录查询、日期、答案和引用来源。
- [ ] 对未引用本站的情况区分原因：未收录、排名过低、页面不能回答问题、缺少外部权威性或数据不够新。

## 验收指标

### 2 周内（技术发现）

- [ ] Search Console 显示首页和 sitemap 已发现，首页 URL Inspection 不存在抓取或规范化阻碍。
- [x] `robots.txt` 返回 `text/plain`，`sitemap.xml` 返回 XML，不存在路径返回真实 404。
- [ ] 品牌精确查询能找到规范首页（处理时间不保证，以搜索引擎实际收录为准）。

### 1–2 个月（内容收录）

- [ ] 首页、methodology、data 和主要 meeting pages 被收录。
- [ ] Search Console 开始出现非品牌查询 impressions，例如“美联储加息概率历史”和英文同类词。
- [ ] 至少获得若干来自真实相关页面的自然外链或引用。

### 3–6 个月（排名与引用）

- [ ] 对一组低竞争长尾查询稳定进入可见结果页。
- [ ] 数据页或分析页被外部文章、社区或 AI 搜索答案实际引用。
- [ ] 建立月度 Search Console、Bing 和 AI 可见性记录，不用单次搜索结果判断成败。

## 风险与验证缺口

- 搜索引擎提交、sitemap 和结构化数据都不保证收录或排名；站龄、内容独特性和外部引用需要时间积累。
- `FedWatch` 是 CME 的产品名称；页面应避免造成官方隶属误解，并审慎处理数据再发布、商标和授权表述。
- [x] 已移除页面和 README 中“与官网 100% 等价”的绝对措辞，改为可审计的来源、采集时点和盘中变化说明。
- AI 产品的检索和引用策略可能变化，也可能不提供站点提交入口；目标是成为可索引、可验证、被外部引用的优质来源，而不是针对单一模型做技巧优化。
- Search Console、Bing、独立域名和第三方内容发布涉及外部账号或付费决策，不能仅靠代码仓库自动完成。
- OG PNG 当前在 macOS 使用 `sips` 从 SVG 生成；若构建迁移到非 macOS 环境，需要替换为跨平台图片生成方式。

## 已完成里程碑（2026-09-15）

- [x] 每日抓取、报告构建和 Cloudflare Pages Direct Upload 自动化。
- [x] 新增 `site_seo.py`，`build_report.py` 同时构建交互首页与多页 SEO 静态站点，并在发布前执行站点验证。
- [x] 部署脚本验证首页、robots、sitemap、历史页、CSV、Content-Type 和真实 404。
- [x] 生产 deployment `be2bb859` 已验证与本地产物一致。
- [x] 桌面和移动端本地浏览器 QA 通过，控制台无 error / warning。
- [x] 已创建 Public GitHub 仓库 `https://github.com/Stayfoool/fedwatch-tracker`；已设置站点 Homepage、MIT License 和主题标签（FedWatch / FOMC / Federal Reserve / interest rates 等）。初始提交已使用非个人提交身份，未上传 token、环境变量、日志、构建产物或维护者联系方式。

## 立即后续

1. [ ] 2026-09-16 或之后重新检查 Search Console 的 sitemap 状态；若仍为 `Couldn't fetch`，查看详细错误并继续排查 Google 抓取侧问题。
2. [ ] 2026-09-16 或之后再次对首页执行 Request Indexing；2026-09-15 因 Google 当日配额已满未能提交。
3. [ ] 使用站点所有者的 Microsoft 账号完成 Bing Webmaster Tools 验证或从 Search Console 导入。2026-09-17 已打开 Bing 账户选择窗口，但当前浏览器没有已登录会话；待所有者完成账户登录/MFA 后，导入 Search Console 属性（优先）或以 sitemap/HTML 验证，并提交 `https://fedwatch-tracker.pages.dev/sitemap.xml`。
4. [ ] 决定是否购买独立域名；若购买，更新 `FEDWATCH_SITE_URL`、Cloudflare custom domain、301、canonical、sitemap 和两个站长平台属性。
5. [ ] 在不披露维护者身份的前提下，准备第一批可被外部引用的周报/事件复盘和公开项目页。
6. [ ] 轮换此前在聊天中暴露过的 Cloudflare API token。
