# 自动归因 Runbook（ZCode 每日自动化执行手册）

> 每日自动归因（07:30 北京时间）按本手册工作；人工补录遵循同一套规则。
> 归因口径与历史沿革见 `docs/event-attribution.md`（家规），冲突时以家规为准。

## 方法：媒体直查

大涨日当天，权威媒体通常已经把归因写好（"X 之后交易员将加息押注推高至 Y%"）。
要做的只是：**一次定向检索 → 挑一篇日期对得上的白名单稿 → 抄结论、挂链接。**
不需要先查经济日历、不需要四段式长文、不需要穷举排除项。

> **口径边界**：本手册的自动检测（`analyze_changes.py`）基于「最大概率区间」口径，
> 服务于主看板旧图的光环。**主图 A**（P(加息)，`dual_charts.py`）的光环按
> 「同基准日环比 ≥8pp」自行判定（跨基准切换日排除），暂不在每日自动检测里——
> 其未归因大变动日需人工比对补录，规则与本手册相同（2026 年内已全部补齐，见
> `logs/attribution_20260929.log`）。

## 流程

### 0. 触发与退出

1. `cd /Users/yw/ai/fedwatch-tracker`
2. `python3 analyze_changes.py --days 7`，看退出码：
   - `0`＝无待归因日 → **立即结束**，不改任何文件、不部署（近零 token）。
   - `1`＝数据异常 → 在 `logs/attribution_YYYYMMDD.log` 记一行后结束，不要猜原因。
   - `2`＝有待归因日 → 继续。
3. 待归因清单 = `data/significant_changes.csv` 里 `annotated=0` 的行（本次运行刚重写）。
   美东交易日读 `us_trade_date` 列，图上数字（`delta_pp` / `bucket_switch` /
   `chart_delta_pp` / `prev_label` / `cur_label` 等）直接抄，**不要重算**。

### 1. 检索（每个待归因日一次）

- 查询式：`Fed rate hike odds [D 的英文日期]`；可按需加 `Treasury yields`、
  具体数据名（`CPI` / `payrolls` / 官员姓氏）。
- 只从白名单挑稿：reuters.com / cnbc.com / marketwatch.com / bloomberg.com /
  wsj.com / ft.com / investing.com；官方一手（federalreserve.gov / bls.gov /
  各地区联储官网）作为补充链接。
- 付费墙读不动就换 Reuters/CNBC 的同题材稿；**链接本身**挂付费墙的没问题。

### 2. 两条硬规则（不可省）

1. **日期对齐**：所选文章的发布日期必须等于该美东交易日 D（或次日的 recap）。
   邻近日期的稿宁可不用——家规里那份「不要再用的错误来源」清单，
   全是拿错日期的稿凑数造成的。
2. **方向对账**：文章叙事方向必须与图上方向一致。对不上（如油价涨但图走鸽）→
   `direction` 仍按图上，`summary` 加前缀 `待核实：`，正文一句说明矛盾。
   找不到日期对得上的稿 → 写「无当日触发」（可给一句最合理候选），
   **禁止编造因果、禁止无来源的"机构普遍认为"**。

### 3. 写入 `data/events.csv`

- **只追加，绝不修改/删除已有行**；`utf-8-sig` 读写。
- 列：`snapshot_cn,direction,summary,text,url`。
- `snapshot_cn`：抄清单里该行的 `snapshot_cn`（通常是当天早晨收盘定格）。
- `direction`：图上最大桶涨 = `hawk`，跌 = `dove`（家规，与宏观鹰鸽无关）。
- `summary`：一句话，含变动最大的会议与 pp 数。
- `text`：1–3 句——发生了什么（谁说的/什么数据/什么事件）、与图上读数的关系；
  末尾加一行【口径】：`图上 X 会议 label prev%→cur%（±pp，是否换桶）`，数字抄清单。
- `url`：1–3 条，`|` 分隔——首选那篇白名单稿 + 可选一条官方一手。

### 4. 构建校验与发布

依次执行，**任一步失败立即停止并记日志，绝不部署校验不过的页面**：

```bash
python3 build_report.py                    # 重建 report/（含 18 个 SEO 页）
python3 -c "import re;h=open('report/index.html',encoding='utf-8').read();\
open('/tmp/p.js','w').write('\n;\n'.join(re.findall(r'<script>(.*?)</script>',h,re.S)))"
node --check /tmp/p.js                     # 图表 JS 语法校验（家规：改模板必查）
python3 analyze_changes.py --days 7        # 必须退出码 0（全部已标注）
./deploy_server.sh                         # 触发服务器重建；双路发布（nginx + pages.dev）
```

### 5. 收尾

在 `logs/attribution_YYYYMMDD.log` 追加：处理日期、每条 summary、验证与部署结果。
不操作 git（提交由人工决定）。

## 成本

- 无变动日：一次本地检测即退出，近零 token。
- 有变动日：每事件一次检索 + 一短段，约几千 token；焦点口径历史频率约每月 2–3 次。
- 一次检出 >5 天（如补采缺口后）：只处理 `|delta_pp|` 最大的 3 天，
  其余次日自动重试（检测窗口 7 天，不会漏）。
