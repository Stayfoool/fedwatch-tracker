#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_curves.py — 生成 report/curves.html：「方向与幅度」独立页。

图表本体（主图 A/B、口径、交互、动态基准）在 dual_charts.py 组件里，
本文件只是页面壳：会议读数卡片 + 双图组件 + 口径说明。
首页（build_report.py）嵌入的是同一个组件，两边永远一致。

为什么要有这两个口径（2026-09-29 设计讨论的结论）：

主看板折线画的是「最大概率区间」的概率。该口径有两个结构性缺陷：
① 区间标签切换日（如 350-375 → 375-400），折线把两个不同桶的概率接到
   一起冒充连续序列，产生假跳变；
② 线值方向与宏观方向脱钩（持稳桶下跌 = 加息预期上升，图上却是"下降"），
   曾经造成 2026-03-26 的「图上转鹰」事故。

主图 A（方向，P(加息)）与主图 B（幅度，预期变动 bp）自始至终是同一个量；
取点规则与主看板完全一致（snapshot_view.displayed_by_day）。
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import date as date_t

from build_report import BASE_DIR, build_xmeta, load, next_n_meetings
from dual_charts import dual_charts_block, meeting_color
from rate_baseline import FOMC_DECISION_DATES
from snapshot_pick import pick_latest_snapshot
from site_seo import canonical

OUT = os.path.join(BASE_DIR, "report", "curves.html")


def meeting_card(m: str, color: str, latest: dict, prev7: dict | None) -> str:
    rng_rel = ""
    try:
        dd = int(latest["rng"].split("-")[0]) - int(latest["base"].split("-")[0])
        rng_rel = "，" + ("持平" if dd == 0 else f"{dd:+d}bp")
    except (ValueError, KeyError):
        pass
    def d_hike():
        if not prev7:
            return ""
        d = latest["hike"] - prev7["hike"]
        if abs(d) < 0.05:
            return '<span style="margin-left:6px;font-size:12px;color:#9ca3af">7 日持平</span>'
        sign = "+" if d >= 0 else "−"
        return f'<span style="margin-left:6px;font-size:12px;color:{"#dc2626" if d>=0 else "#16a34a"}">{sign}{abs(d):.1f}pp / 7 日</span>'
    def d_bp():
        if not prev7:
            return ""
        d = latest["bp"] - prev7["bp"]
        sign = "+" if d >= 0 else "−"
        return f'（7 日前 {prev7["bp"]:+.1f}，{sign}{abs(d):.1f}）'
    return (
        f'<div style="background:linear-gradient(135deg,#ffffff 0%,#fafbff 100%);'
        f'border:1px solid {color}55;border-radius:12px;padding:18px 16px;'
        f'box-shadow:0 1px 2px rgba(15,23,42,0.04)">'
        f'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">'
        f'<div style="font-size:13px;color:{color};font-weight:700;letter-spacing:0.4px">{m[5:]} FOMC 会议</div>'
        f'<div style="font-size:11px;color:#9ca3af">{m}</div></div>'
        f'<div style="font-size:11.5px;color:#6b7280;margin-bottom:2px">P(加息)</div>'
        f'<div style="font-size:30px;font-weight:700;color:#111827;'
        f'font-variant-numeric:tabular-nums;letter-spacing:-0.5px">{latest["hike"]:.1f}%'
        f'<span style="font-size:15px;color:#dc2626">▲</span>{d_hike()}</div>'
        f'<div style="margin-top:8px;display:flex;gap:14px;font-size:11.5px;color:#6b7280">'
        f'<span>维持 <b style="color:#6b7280">{latest["hold"]:.1f}%</b></span>'
        f'<span>降息 <b style="color:#16a34a">{latest["cut"]:.1f}%</b></span></div>'
        f'<div style="margin-top:10px;padding-top:10px;border-top:1px dashed #e5e7eb;'
        f'font-size:12.5px;color:#374151">'
        f'预期变动 <b style="color:{color};font-size:15px">{latest["bp"]:+.1f}bp</b> {d_bp()}</div>'
        f'<div style="margin-top:6px;font-size:11.5px;color:#9ca3af">当前最大档：{latest["rng"]}（{latest["rpv"]:.1f}%{rng_rel}）· 基准 {latest["base"]}</div>'
        f'</div>'
    )


PAGE_CSS = """
body{margin:0;background:#f9fafb;color:#0b1220;
 font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",system-ui,sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:28px 22px 60px}
h1{font-size:24px;margin:0 0 4px;font-weight:700;letter-spacing:-0.4px}
.sub{color:#6b7280;font-size:13px;margin-bottom:8px}
.nav{font-size:13px;margin-bottom:22px}
.nav a{color:#1d4ed8;text-decoration:none;font-weight:600}
.nav a:hover{text-decoration:underline}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:20px 22px;margin-bottom:16px}
.card h2{font-size:15px;margin:0 0 4px;color:#0b1220;font-weight:600}
.note{font-size:12.5px;color:#6b7280;line-height:1.75}
"""


def main():
    rows = load()
    if not rows:
        print("暂无数据")
        return
    by_snap = defaultdict(list)
    for r in rows:
        by_snap[r["snapshot_cn"]].append(r)
    latest_cn = pick_latest_snapshot(by_snap)
    latest = by_snap[latest_cn]
    meetings = next_n_meetings(latest)
    xmeta = build_xmeta(rows)
    fomc = sorted(set(FOMC_DECISION_DATES) | {r["meeting_date"] for r in rows})

    dual_html, ctx = dual_charts_block(rows, meetings, xmeta, fomc)
    if not dual_html:
        print("WARN 双图组件无数据，仅生成页面骨架")
    points = ctx["points"]

    # 卡片：最新点 + 7 个交易日前对比
    cards = []
    for idx, m in enumerate(meetings):
        pts = points.get(m) or []
        if not pts:
            continue
        latest_p = pts[-1]
        prev7 = pts[-8] if len(pts) >= 8 else None
        cards.append(meeting_card(m, meeting_color(m, idx), latest_p, prev7))
    cards_html = ('<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:14px;'
                  'margin-bottom:6px">' + "".join(cards) + "</div>")

    latest_us = xmeta.get(latest_cn, {}).get("d", latest_cn[:10])
    latest_note = xmeta.get(latest_cn, {}).get("t", "")
    latest_desc = (f"美东 {latest_us} 收盘后（{latest_note}）" if latest_note not in ("", "盘中")
                   else f"美东 {latest_us}（{latest_note}）")
    first_us = xmeta.get(min(r["snapshot_cn"] for r in rows), {}).get("d", "")
    n_days = len({p["snap"] for pts in points.values() for p in pts})
    today_str = date_t.today().isoformat()
    cur_target_disp = ctx.get("cur_target_disp") or (latest[0].get("current_target") or "350-375")

    page = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CME FedWatch · 加息预期：方向与幅度</title>
<meta name="description" content="未来三次 FOMC 会议的累计加息概率（方向）与预期利率变动 bp（幅度）走势，口径全程一致、无区间切换跳变。">
<link rel="canonical" href="{canonical('/curves')}">
<style>{PAGE_CSS}</style></head><body><div class="wrap">
<h1>加息预期 · 方向与幅度</h1>
<div class="sub">数据来源 CME QuikStrike FedWatch 工具 · 横轴＝<b>美东交易日</b> · 最新数据点 {latest_desc} · 当前目标区间 {cur_target_disp} bps</div>
<div class="nav"><a href="index.html">← 返回主看板</a></div>

<div class="card">
  <h2>三次会议当前读数</h2>
  <div style="font-size:12.5px;color:#6b7280;margin:0 0 12px">P(加息) = 落在当前目标区间上方任意档位的概率之和；预期变动 = 概率分布的期望落点相对当前目标的 bp 变化。</div>
  {cards_html}
</div>

{dual_html}

<div class="card">
  <h2>口径说明</h2>
  <div class="note">
  · <b>为什么是这两个口径</b>：主看板折线画的是「最大概率区间」的概率，区间标签切换日
    （如 350-375 → 375-400）会把两个不同档的概率接到一起，产生假跳变；且持稳桶下跌
    （=加息预期上升）在图上表现为"下降"，线值方向与宏观方向脱钩。本页两个口径
    <b>自始至终是同一个量</b>，不存在切换。<br>
  · <b>P(加息)</b>：该会议所有高于当前目标区间的档位概率之和；<b>P(降息)</b>：所有低于
    当前目标区间的档位概率之和；两者互斥，与 P(维持) 凑成 100%。全部由每档概率列
    （range_*）对当前目标区间求和得出，不依赖 QuikStrike 的 Agg 标签。<br>
  · <b>当前目标区间怎么定</b>：取最近一次<b>已开完</b> FOMC 决议后的现行目标，从该会议
    决议日最终读数的众数桶自动推导（例：2026-09-16 加息 25bp → 375-400）。CSV 的
    <code>current_target</code> 列自 9/16 后未更新（仍写 350-375），本页不采用。
    目标切换处折线<b>断开并加琥珀色标注</b>——切换前后不是同一个量：旧基准的 P(加息)
    把已兑现的加息也计在内（这正是 9/11 起旧图虚钉 100% 的原因）。<br>
  · <b>预期变动 bp</b>：Σ(档位概率 × 档位中点 − 当前目标中点)。例：P(+25bp)=100% 时
    预期变动 = +25bp；若其中 40% 移到 +50bp，则升至 +35bp——近端概率饱和后仍继续反映幅度重定价。<br>
  · <b>降息虚线</b>：当前处于加息周期，虚线长期贴 0；将来若转向降息，虚线自己会起来
    （2026 年 2 月它曾到 30–40%），无需改动任何口径。<br>
  · <b>取点规则</b>：与主看板完全一致——一个美东交易日一个点（收盘定格 &gt; 结算导出 &gt; 盘中读数），
    两页日期可直接对照；FOMC 竖虚线为会议决议日。<br>
  · <b>光环与归因</b>：主图 A 上橙/蓝光环点 = 当日该会议 P(加息) 同基准日环比 ≥8pp
    （目标切换日的机械跳变不算事件，那里只有琥珀色断线）；悬停浮窗底部显示
    原因与新闻来源，归因存档在 <code>data/events.csv</code>，与主看板共用。<br>
  · <b>交互</b>：图例点按切换会议；「降息 P(cut) 虚线」按钮开关虚线；双滑块筛选日期；
    悬停出浮窗（右下角停靠），点一下可钉住。<br>
  · <b>数据范围</b>：美东 {first_us} 起，共 {n_days} 个交易日。生成于 {today_str}，
    由 <code>build_curves.py</code> 每日随主看板一起重建（图表组件 <code>dual_charts.py</code>
    与首页共用，两边永远一致）。
  </div>
</div>
</body></html>"""

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"双图页已生成: {OUT}  (最新 {latest_cn}, {len(meetings)} 条会议曲线, "
          f"{n_days} 个交易日)")


if __name__ == "__main__":
    main()
