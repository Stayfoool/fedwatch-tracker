# CME FedWatch 加息概率追踪

自动采集美联储各次 FOMC 会议的 **Aggregated（累计）** 加息概率并存档，
并在看板画出"下一三次 FOMC 会议的最大概率加息水平"时序折线图。
**历史已回填至 2025-09-11**（1 年，251 个交易日），非模拟数据。

> **当前快照（采集于 2026-09-15 10:00 北京时间，来自 CME QuikStrike Aggregated View）**
>
> | 会议 | 最大概率区间 | 该区间概率 | 对应"+"
> |---|---|---|---|
> | **9/16** | 3.75% – 4.00% | **92.39%** | +25bp |
> | **10/28** | 3.75% – 4.00% | **60.00%** | +25bp 累计 |
> | **12/9** | 4.00% – 4.25% | **91.30%** | +50bp 累计 |

每日收盘窗口北京时间 05:30/06:30（服务器定时）采集、累积起来，看板就会形成 3 条"市场对该会议加息预期的最大概率"折线图。

公开站点：<http://8.215.88.73/>（阿里云轻量服务器每日采集并直接托管）。公开代码仓库：<https://github.com/Stayfoool/fedwatch-tracker>；Mac 本地开发 → push GitHub → 服务器拉取部署（服务器采集的新数据每日回推 GitHub）。旧 Cloudflare Pages 站点已停止更新。项目已经从单一 JS 看板升级为可抓取的多页静态站点，包含会议历史页、方法页、数据下载页、中英文入口、robots、sitemap、结构化数据和真实 404。维护者姓名、组织、邮箱和联系方式目前不公开。

## 一、看板怎么看

主交互看板位于 `report/index.html`；`report/` 同时包含供搜索引擎、AI 检索和读者引用的静态内容页，包括概率变化与事件分析页。

打开 `report/index.html`，核心板块：

1. **3 张大卡片**——分别对应 9/16、10/28、12/9 三次 FOMC 会议，
   显示**当前最大概率目标利率水平**及该区间概率。
2. **主图 A / 主图 B 双图**（与独立页 `/curves` 共用同一组件）——
   **主图 A · 方向**画各会议**累计加息概率 P(加息)**（实线）+ P(降息)（虚线），
   **主图 B · 幅度**画预期变动 bp；两个口径全程同一定义、无「最大概率区间」切换跳变，
   目标区间切换处折线断开并加琥珀色标注。
   主图 A 上的**橙/蓝光环点** = 当日 P(加息) **同基准日环比 ≥8pp** 且已有归因
   （跨基准的机械跳变不算事件）；悬停浮窗底部显示**事件原因 + 新闻来源**，与第 5 条共享同一份 `data/events.csv`。
3. **折线图（可交互）**——同一组卡片对应的 3 条折线，**每个采集日一个数据点**
   （历史 251 天 + 每日新增）。点的 Y 轴位置 = 当日该会议「最大概率区间」的概率。
   - **点击图例**（`09-16 会议` / `10-28 会议` / `12-09 会议`）可切换该线的显示/隐藏，
     聚焦看一条或对比两条；关闭的线在图上和浮窗里都会同步消失。
   - **日期范围筛选**：两条滑块分别锁定「起始 / 结束」日期，可聚焦某个月或最近 60 天。
     X 轴刻度、十字线、浮窗、线尾标签都会按所选区间重算，右侧实时显示所选天数。
     点「重置」回到全区间（2025-09-11 → 今天）。
   - **鼠标悬停**：出现竖直十字线，浮窗显示该日每条可见线的
     **日期 + 最大概率目标区间（绝对值如 `3.75% - 4.00%`，以及 `+25bp` 相对标签）+ 该区间概率**。
   - **FOMC 会议日标注**：落在当前日期范围内的会议日显示为中性灰色**垂直虚线 + 顶部 `FOMC` 小标签**；
     它与重大变动光环分属不同视觉层，不使用菱形，也不会改变悬停浮窗内容。
   - **重大变动标注**：某会议 max 概率**日环比 ≥ 8pp** 的日子，数据点会变成
     橙色/蓝色**光环 + 实心点**（橙=图上线值上涨，蓝=图上线值下降；这不等同于累计加息概率的 hawkish/dovish 方向）；
     悬停这些点，浮窗底部会多出一段**事件说明 + 新闻来源链接**。
4. **全会议热力图**——**全部 11 次未来会议**（2026/9/16 → 2027/12/8）的全区间概率表，
   直接来自同次 QuikStrike Aggregated 抓取，并保留采集时点和口径说明；不同时间打开官方工具时，盘中概率可能已变化。

> **重大变动的原因从哪来？** 见第五节「重大变动归因」。简言之：`analyze_changes.py`
> 自动找出 ≥8pp 的日子 → 把清单写进 `data/significant_changes.csv` →
> **ZCode 每日自动化（07:30）按 `docs/auto-attribution.md` 核对当日新闻，
> 把归因写进 `data/events.csv` 并重建发布** → 看板自动读它并渲染光环。
> 无需人工干预；人工仍可按同一手册补录或更正。

> 交互实现：折线数据以 JSON 内嵌在 `<script type="application/json" class="chart-data">`。
> Python 端只输出静态骨架（Y 网格、会议日标注层、3 个空 `g.series`、十字线、透明命中层），
> X 轴刻度与折线 path/circle/线尾标签全部由 JS 的 `paint(startIdx, endIdx)` 重画，
> 因此滑块改范围时坐标能整体重算。图例按钮切换 `g.series` 的 `hidden` 类；
> 悬停由一个透明 `.capture` rect 监听 `mousemove`，把像素坐标换算回区间内索引后填充 `.chart-tooltip`。
> 全部为原生 SVG + 原生 JS，无 CDN 依赖，双击 HTML 即可离线使用。

## 二、重大变动归因（≥8pp 的日子）

**主图 A**（P(加息) 口径）与**「最大概率区间」折线图**上的**光环点**共用同一套归因存档：

```
analyze_changes.py  ──►  data/significant_changes.csv  ──►  data/events.csv  ──►  两图光环 + 悬停弹窗
   （找 ≥8pp 的日子）        （待分析清单）                （原因存档）
```

两图判定口径不同、光环互不替代：旧图按「最大概率区间」的 max 概率日环比（`analyze_changes.py` 自动检测）；
主图 A 按 **P(加息) 同基准日环比 ≥8pp**（基准切换日的机械跳变排除，2026 年内即 9/16 一次）。
归因按美东交易日写入 `data/events.csv`，一份存档两处生效。

### 1. 自动检测

```bash
$PY analyze_changes.py              # 最近 30 天，阈值 8pp
$PY analyze_changes.py --days 7     # 最近 7 天
$PY analyze_changes.py --thr 10     # 阈值改成 10pp
$PY analyze_changes.py --all        # 全部历史
```

判定口径：某会议的 `max_range_pct` 相对**上一次采集**的变化绝对值 ≥ 阈值。
跨度过大（>5 天，说明有数据缺口）的比对会被跳过，避免把「假期 + 数据断档」误判成事件。

输出 `data/significant_changes.csv`（可当作待办清单）：

```csv
snapshot_cn,meeting_date,direction,delta_pp,prev_label,prev_pct,cur_label,cur_pct,annotated,event_summary
2026-09-04 10:00:00,2026-09-16,hawk,+8.86,350-375,50.57,375-400,59.43,1,8 月非农 +162k
```

`annotated=1` 表示 `data/events.csv` 里已有归因；退出码 `2` 表示存在未归因的变动日
（便于外层脚本判断是否需要提醒）。

### 2. 归因存档：`data/events.csv`

```csv
snapshot_cn,direction,summary,text,url
2026-09-04 10:00:00,hawk,8 月非农 +162k,8 月非农就业 +162k（BLS），高于 Waller 演讲里引用的月均 +60k 水平约一倍……,https://www.bls.gov/news.release/empsit.nr0.htm
2026-09-03 10:00:00,dove,Waller 鸽派表态,理事 Waller 在 Reuters NEXT 访谈中表态「倾向于维持当前利率」……,https://www.federalreserve.gov/newsevents/speech/waller20260903a.htm
```

| 列 | 说明 |
|---|---|
| `snapshot_cn` | 采集时刻，须与主 CSV 完全一致（`YYYY-MM-DD HH:MM:SS`）。**这是关联键** |
| `direction` | 按图上最大变动会议的 `max_range_pct`：`hawk`=线值上升、`dove`=线值下降、`neutral`=中性；累计加息概率另看正文 |
| `summary` | 一句话，弹窗标题行 |
| `text` | 详细说明，弹窗正文 |
| `url` | 新闻/官方来源链接，弹窗底部可点击 |

一个日期可写多行（多事件）。文件带不带 BOM 都能读（用 `utf-8-sig` 打开）。

### 3. 归因来源（本次回溯所用）

| 日期 | 概率变动 | 事件 | 来源 |
|---|---|---|---|
| 2026-03-18 | +17.94pp | 3 月 FOMC 持稳、关注中东不确定性 | federalreserve.gov 声明 |
| 2026-03-19 | **+33.63pp** | FOMC 次日消化，持稳桶大升、降息桶压缩 | federalreserve.gov 声明 |
| 2026-04-17 | **−22.62pp** | 霍尔木兹重开预期、油价回落 | Reuters |
| 2026-04-29 | +14.71pp | 4 月 FOMC 持稳、三人反对 easing bias | federalreserve.gov 声明 |
| 2026-06-05 | −15.51pp | 5 月非农 +172k、失业率 4.3% | Reuters / BLS |
| 2026-06-17 | **−25.30pp** | 9/16 最大概率桶下降；未见权威媒体明确单日归因（累计加息概率反而上升） | federalreserve.gov 声明 |
| 2026-08-04 | −8.78pp | JOLTS 偏冷 + Williams 鸽派 | bls.gov jolts |
| 2026-08-31 | +8.43pp | 8/28 Warsh 在 Jackson Hole 强调通胀仍高、偏鹰 | federalreserve.gov 讲话全文 |
| 2026-09-03 | **−12.64pp** | Waller 在 Reuters NEXT 表示「倾向于维持」 | federalreserve.gov 讲话全文 |
| 2026-09-04 | +8.86pp | 8 月非农 +162k（远超月均 +60k） | bls.gov empsit |
| 2026-09-10 | +11.15pp | 8 月 PPI +0.4%，商品端 +1.1% | bls.gov ppi |
| 2026-09-11 | **+52~63pp** | 9/11 公布 8 月 CPI +0.4%（headline） | bls.gov cpi |
| 2026-09-14 | **−11.00pp** | CPI 极端定价后周末回吐远端加息尾部 | bls.gov cpi |

完整 41 个 ≥8pp 日（含 7 天「待核实」）见 `data/events.csv` 与 `docs/event-attribution.md`。看板光环的 hawk/dove 与最大变动会议的 `max_range_pct` 涨跌一致；正文另说明累计加息概率是否相反。

可用的权威源：
- **Fed 讲话全文**：`federalreserve.gov/newsevents/speeches-testimony.htm`（列出每场讲话 + 逐篇全文，含 Waller / Warsh / Bowman 等）
- **Fed 新闻稿**：`federalreserve.gov/newsevents/pressreleases.htm`
- **BLS 数据**：`bls.gov` 首页即列出最近发布的 CPI / PPI / 非农（附新闻稿链接与 RSS）
- 注意：Fed 的 RSS 端点（`/feeds/*.xml`）已失效，用 HTML 页抓取。

### 4. 每日流程里的位置

`run_daily.sh` 每天采集成功后会追加跑 `analyze_changes.py --days 7`，
发现未归因的变动日就在日志里提示。**归因的写入由 ZCode 每日自动化完成**：
每天北京时间 07:30（两次采集窗口之后）检查一次，有待归因日就按
`docs/auto-attribution.md` 核对新闻 → 追加 `data/events.csv` → 重建 → 校验 → 发布；
没有待归因日则立即退出。这一步失败不影响次日重试（检测窗口 7 天，不会漏）。

## 三、历史数据从哪来 / 能回溯多久

> **FedWatch 官网自身提供 1 年历史下载，不需要自算。**

CME FedWatch Tool 的用户指南明确写明：工具左侧 "**Downloads**" 面板提供
"**All upcoming meetings**" 与逐会议 CSV 下载，含**所有会议日期的原始历史概率，
时间跨度约 1 年**。

实测结果（2026-09-12 拉取）：

| 项 | 值 |
|---|---|
| 文件名 | `User/Export/FedWatch/AllMeetings.aspx` |
| 交易日数 | **251 天** |
| 起止 | **2025-09-11 → 2026-09-10** |
| 覆盖会议 | 11 场（全部未来 FOMC） |
| 每股每会议分布 | 63 个 25bp 区间桶 |
| 单次下载大小 | 约 648 KB |

历史回填脚本 `backfill_history.py`（仅在需要修复历史缺口时手动运行）会：
1. 在 agent-browser 会话内 fetch 该 CSV；
2. 解析为 (日期 × 会议) → `max_range_label` / `max_range_pct`；
3. 追加到 `data/fedwatch_probabilities.csv`（按 `(snapshot_cn, meeting_date)` 幂等去重）；
4. 重建 `report/index.html`。

因为幂等，可以重复跑；但它不是每日采集流程的一部分。本站现有历史已完成初始化，
日常只追加每个收盘窗口（服务器定时）的实际快照；如将来确有历史缺口，再手动运行该脚本。

**注意**：想要早于 1 年的历史，FedWatch 不再提供。那条路只能回到
"自算"（用自己的算法从 ZQ 结算价推导），但 FedWatch 有未公开细节，
本项目已证明自算不可靠，故不做。

## 四、Aggregated 与 Conditional 的区别

FedWatch 提供两种口径，本项目**默认采用 Aggregated（累计）**：

| 口径 | 含义 | CSV 字段 |
|---|---|---|
| **Aggregated** | 相对**当前目标区间**已定价的累计加息/降息总幅度 | `agg_p_hike_pct` / `agg_p_hold_pct` / `agg_p_cut_pct` / `aggregated_ranges` |
| Conditional | 只看**某一次会议**相对上一合约月隐含利率的增量 | （本项目不采集） |

媒体引用 FedWatch 时说的"市场认为 X 月加息概率 XX%"指的就是 Aggregated。
例如：当前 9/16 加息概率 87.29% 意思是市场定价 9 月 16 日会议结束后
联邦基金目标区间高于当前 350-375 bps 的概率。

## 五、什么是 QuikStrike / 为什么用浏览器自动化

你给的 <https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html>
**页面本体不含任何数据**，真实内容通过 iframe 加载自 CME 合作方 QuikStrike：

```
https://cmegroup-tools.quikstrike.net/User/QuikStrikeTools.aspx?viewitemid=IntegratedFedWatchTool
```

**QuikStrike** 是 CME Group 收购并沿用的一组**网页分析工具套件**（期权 Greeks、
波动率曲面、FedWatch 都属于它），以 iframe 形式嵌在 cmegroup.com 各分析页面。
它是纯前端 ASP.NET 应用，概率图表在浏览器里用 JS 渲染。

### 第一版做法（已弃用）
最初项目用 CME 免费 ZQ 结算价接口 + FRED EFFR 按官方方法论**自行计算**。
虽然底层输入与官网一致，但 FedWatch 内部有未公开的细节（如月末会议的 M 处理），
导致长链（如 12/9）误差大到离谱（算出 49% vs 实际 99%）。

### 当前做法（采用）
**直接用浏览器自动化打开 QuikStrike 页面，点击 Aggregated tab，从 DOM 表格抽取数据。**
- 直接读取 QuikStrike Aggregated 表格（不再自行推导概率；仍需以本站记录的采集时点比较）
- 同时拿到 ZQ 价格和 QuikStrike 自带的 "Data as of" 时间戳
- 包含 11 次未来会议的完整视图（2026/9/16 起至 2027/12/8）

QuikStrike 拒绝 referer 非 cmegroup.com 的请求，
脚本通过 `agent-browser --headers '{"Referer":"..."}'` 注入。

## 六、更新时点

| 渠道 | 更新频率 | 可用性 |
|---|---|---|
| FedWatch 网页 / QuikStrike 工具 | **盘中随期货价格实时变动** | 需要浏览器（agent-browser + Chromium） |
| FedWatch End-of-Day 官方 API | 工作日 01:45 UTC（北京时间 09:45） | 付费 OAuth |
| FedWatch Intraday API | 每分钟 | 付费 |

**结论**：QuikStrike 页面本身随盘中期货价实时变动，但本站不追踪盘中变化。
本站公开数据以**美东收盘后的休市间隙定时抓取**为准：阿里云服务器每天北京时间
**05:30 与 06:30** 双触发（夏令时由 05:30 命中、冬令时由 06:30 命中，折算到芝加哥都是
前一日 16:30 CT）；不在窗口内的那次自动跳过，落在窗口内的那次失败则 +10/+20 分钟重试，
最多 3 次。周末/节假日时 QuikStrike 显示的是上一个交易日的收盘数据，本站按最近工作日归档。

任务由阿里云轻量服务器（Debian 12）上的 systemd 定时器 `fedwatch-daily.timer` 触发：
采集和报告重建成功后，`run_daily.sh` 调用 `publish_pages.sh`，通过 wrangler 把 `report/`
直发 Cloudflare Pages——这是**唯一发布渠道**。随后 `sync_data_git.sh` 把新增数据回推
GitHub（备份）。公开站点：**`https://fedwatch-tracker.pages.dev/`**。
nginx 镜像站已于 2026-10-02 退役（裸 IP:80 恢复 444 空吞）。发布只在服务器上发生，
Mac 不持有任何发布凭据。部署细节见 `docs/deployment.md`。

## 七、采集流程

```
agent-browser open <QuikStrike URL> --headers '{"Referer":"...","Accept-Language":"zh-CN,..."}'
        │  ← 拿到 QuikStrike 注入 session ID 的真实 URL（区域格式由 Accept-Language 决定）
        ▼  (sleep 8s 等 ASP.NET 异步加载完毕)
eval: 查找 <a>text==='Aggregated' → click()
        │
        ▼  (sleep 5s 等 UpdatePanel 渲染出 Aggregated 概率表)
eval: 提取 Aggregated 表所有 (meeting_date, range, probability)
        │
        ▼
JSON snapshot  →  data/snapshots/YYYY-MM-DD.json
CSV 行(11 个会议 × 17 列)  →  data/fedwatch_probabilities.csv
```

## 八、文件说明

```
fedwatch-tracker/
├── fetch_quikstrike.py                  # 浏览器自动化抓取（每日主入口）
├── backfill_history.py                  # 手动从 QuikStrike Downloads 回填 1 年历史（幂等）
├── analyze_changes.py                   # 检测 ≥8pp 的重大变动日 → data/significant_changes.csv
├── build_report.py                      # 生成交互首页并调用 SEO 静态站点构建
├── site_seo.py                          # 生成内容页、metadata、sitemap、robots、CSV 等
├── run_daily.sh                         # 每日采集→构建→发布总入口（服务器；渠道由环境变量切换）
├── deploy_server.sh                     # Mac 端部署触发：让阿里云服务器从 GitHub 拉取重建
├── publish_pages.sh                     # 服务器端：report/ 直发 Cloudflare Pages（对外门面）
├── sync_data_git.sh                     # 服务器端：采集成功后把新数据回推 GitHub
├── install_launchagent.sh               # （已退役）Mac 本地采集回切方案：安装 05:30/06:30 LaunchAgent
├── launchd/com.workbuddy.fedwatch-tracker.daily.plist
├── scripts/
│   ├── nginx/fedwatch.conf              # 服务器 nginx 站点（裸 IP:80 default_server）
│   ├── systemd/fedwatch-daily.{service,timer}
│   └── server/{bootstrap.sh,fedwatch-deploy}  # 服务器初始化与部署器
├── package.json / package-lock.json      # 固定 agent-browser 与 Wrangler 版本
├── data/
│   ├── fedwatch_probabilities.csv       # 主存档（长表，按 snapshot_cn+meeting 去重）
│   ├── events.csv                       # 重大变动日的归因（原因 + 来源链接），看板据此画光环
│   ├── significant_changes.csv          # analyze_changes.py 产出的待分析清单
│   ├── history/all_meetings_*.csv       # QuikStrike 下载的原始历史 CSV（含 251 天 × 11 会议）
│   └── snapshots/YYYY-MM-DD.json        # 每日完整快照（11 个会议 + 13 个 ZQ 价格）
└── report/                              # 完整 Cloudflare Pages 部署目录
    ├── index.html                       # 主交互看板
    ├── history/ methodology/ data/      # 可索引支持页
    ├── about/ en/ meetings/             # 项目边界、英文入口、会议历史页
    ├── robots.txt sitemap.xml 404.html  # 抓取、发现和真实 404
    └── assets/ data/                    # OG 图片与可下载 CSV
```

**当前数据规模（2026-09-15）**：CSV 中有 `255` 个快照 × `11` 个未来会议 = `2,805` 行，
存档日期从 `2025-09-11` 到 `2026-09-15`。周末实时抓取按最近工作日归档，实际抓取时刻保留在每日 JSON 中。
QuikStrike 会在远期会议尚未进入可计算窗口时写入
全 0 占位行；占位行不是实际的 0% 概率，折线图已明确跳过。

| 会议日期 | CSV 行数 | 有效概率交易日 | 首个有效日期 | 最新日期 |
|---|---:|---:|---|---|
| 2026-09-16 | 255 | 255 | 2025-09-11 | 2026-09-15 |
| 2026-10-28 | 255 | 250 | 2025-09-18 | 2026-09-15 |
| 2026-12-09 | 255 | 220 | 2025-10-30 | 2026-09-15 |
| 2027-01-27 | 255 | 191 | 2025-12-11 | 2026-09-15 |
| 2027-03-17 | 255 | 159 | 2026-01-29 | 2026-09-15 |
| 2027-04-28 | 255 | 125 | 2026-03-19 | 2026-09-15 |
| 2027-06-09 | 255 | 96 | 2026-04-30 | 2026-09-15 |
| 2027-07-28 | 255 | 62 | 2026-06-18 | 2026-09-15 |
| 2027-09-15 | 255 | 34 | 2026-07-30 | 2026-09-15 |
| 2027-10-27 | 255 | 4 | 2026-09-11 | 2026-09-15 |
| 2027-12-08 | 255 | 4 | 2026-09-11 | 2026-09-15 |

CSV 列（**17 列**）：`snapshot_cn, snapshot_quikstrike, meeting_date, current_target,
agg_p_hike_pct, agg_p_hold_pct, agg_p_cut_pct,
max_range_label, max_range_pct,
aggregated_ranges,
range_325_350_pct, range_350_375_pct, range_375_400_pct, range_400_425_pct,
range_425_450_pct, range_450_475_pct, range_475_500_pct`

`max_range_label` / `max_range_pct` 是核心列：
对每个 (snapshot, meeting) 行计算分布中概率最大的区间标签（如 `375-400`）与概率值。
折线图直接消费这两列，因此历史回填与每日实时采集共用同一个数据结构。

**历史行与实时行的区别**：历史行 `agg_p_hike_pct/hold/cut` 为空（历史当时的
"当前目标区间"随时间变化，QuikStrike 历史 CSV 只给绝对利率分布桶，不提供这一列）。
折线图只用 `max_range_*`，不受影响；顶部卡片与热力图只取最新快照，不受影响。

退出码：`0` 成功（含"数据已是最新、无需写入"的防抖命中）；`3` 未取到数据/浏览器异常，
供 `run_daily.sh` 判断是否需要重试。

## 九、手动运行

```bash
PY=python3
DIR=/path/to/fedwatch-tracker

$PY $DIR/fetch_quikstrike.py            # 采集（默认会落盘）
$PY $DIR/fetch_quikstrike.py --dry      # 只抽不写
$PY $DIR/fetch_quikstrike.py --json     # 以 JSON 输出结果
$PY $DIR/backfill_history.py            # 需要时手动回填 1 年历史（幂等）
$PY $DIR/analyze_changes.py             # 检测 ≥8pp 的重大变动日（最近 30 天）
$PY $DIR/analyze_changes.py --days 7    # 只看最近 7 天
$PY $DIR/build_report.py                # 重建并验证完整 report/ 静态站点
./deploy_server.sh                        # 推送后一键部署：服务器拉取、构建、发布 pages.dev
# （网站发布只在服务器上发生：nginx 镜像 + Cloudflare Pages 对外门面；
#   Mac 已退出发布链路，deploy_pages.sh 已删除。）
# Cloudflare Token 只存在于服务器 /home/fedwatch/.config/cloudflare/fedwatch-pages.token
# （600）；GitHub 侧另有服务器专用读写 Deploy Key（/etc/fedwatch/ssh/，仅 fedwatch 可读），
# 只用于每日数据回推。
定时任务日志位于 `logs/fetch_YYYYMMDD.log`；服务器端采集输出见
`journalctl -u fedwatch-daily.service`，部署日志见 `/var/log/fedwatch/deploy.log`。

## 十、依赖

- Node.js 22 与 npm
- 项目内固定版本 `agent-browser@0.27.0`（官方 npm 包；该版本与现有 QuikStrike Referer 流程兼容）
- agent-browser 托管 Chromium（服务器上由 `agent-browser install` 下载；本机也可用 Chrome）
- Python 3.11+（脚本全部为标准库，无需 pip 安装；服务器为 Debian 12 自带 3.11）
- 网络可达 `cmegroup-tools.quikstrike.net`（不可达 `www.cmegroup.com` 也无影响）
- （可选）`wrangler@4.131.1`：仅在手动发布 Cloudflare Pages 时使用

服务器端（阿里云轻量，Debian 12）：nginx 1.22（静态托管 + 裸 IP:80 default_server）、
systemd timer（05:30/06:30 Asia/Shanghai 双触发）、git（读写 Deploy Key 回推数据）、
2G swap（保障 headless Chromium）。初始化由 `scripts/server/bootstrap.sh` 幂等完成，
架构与操作详见 `docs/deployment.md`。
