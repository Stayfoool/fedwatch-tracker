#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dual_charts.py — 「方向与幅度」双图组件（可嵌入任意页面）。

主图 A · 方向：累计加息概率 P(加息)（各会议实线）+ P(降息) 虚线。
主图 B · 幅度：预期变动 bp（概率分布期望落点 − 现行目标中点）。

两个口径自始至终是同一个量，不存在「最大概率区间」切换跳变；基准（现行
目标区间）由 rate_baseline 按 FOMC 决议动态推导，切换处折线断开并加标注。

组件自带 CSS/JS，容器类名用 dualchart-container（不是 chart-container），
内层选择器全部以 .dualchart-container 前缀作用域隔离 —— 因此可以和
build_report 主看板自己的 .chart-container 图表共存于同一页面互不干扰。

使用：
    from dual_charts import dual_charts_block
    html_block, ctx = dual_charts_block(rows, meetings, xmeta, fomc_dates)
    # html_block：两张卡片（主图 A / 主图 B）的完整 HTML（含 <style>/<script>）
    # ctx：{"points", "era_info", "cur_base", "cur_target_disp", "bp_ticks"} 供页面头部/卡片复用
"""
from __future__ import annotations

import csv
import json
import os
from collections import defaultdict

from rate_baseline import (
    bucket_stats,
    derive_target_eras,
    discover_range_columns,
    era_target_for,
)
from snapshot_view import displayed_by_day, us_day_of

CHART_W, CHART_H = 952, 380
PAD_L, PAD_R, PAD_T, PAD_B = 50, 190, 32, 60

# 主图 A 的重大变动阈值：P(加息) 相对上一个同基准点日环比 ≥8pp
# （与 analyze_changes 的项目惯例一致）。跨基准（FOMC 加减息落地）不比对。
SIG_THR_PP = 8.0

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_events_by_day(rows) -> dict[str, list[dict]]:
    """读 data/events.csv → {us_trade_date: [{direction, summary, text, url}, ...]}。

    与主看板同一绑定规则（snapshot_view）：归因按美东交易日挂，无论哪个快照
    胜出成为图上那个点都能对上。存档从两处消费——旧图光环（build_report）
    与主图 A 光环（本组件），一份归因两处生效。
    """
    path = os.path.join(BASE_DIR, "data", "events.csv")
    if not os.path.exists(path):
        return {}
    snap_day = {}
    for r in rows:
        s = (r.get("snapshot_cn") or "").strip()
        if s and s not in snap_day:
            snap_day[s] = us_day_of(r)
    by_day = defaultdict(list)
    with open(path, encoding="utf-8-sig", newline="") as f:
        for e in csv.DictReader(f):
            s = (e.get("snapshot_cn") or "").strip()
            if not s:
                continue
            by_day[snap_day.get(s) or s[:10]].append({
                "direction": (e.get("direction") or "").strip(),
                "summary": (e.get("summary") or "").strip(),
                "text": (e.get("text") or "").strip(),
                "url": (e.get("url") or "").strip(),
            })
    return dict(by_day)


# 给不同会议分色（与主看板一致的调色板）
def meeting_color(md: str, idx: int) -> str:
    palette = [
        "#1d4ed8",  # indigo
        "#c2410c",  # orange-brown
        "#7c3aed",  # purple
        "#0f766e",  # teal
        "#be185d",  # pink
    ]
    return palette[idx % len(palette)]


def build_series(rows, meetings):
    """→ ({meeting: [点]}, {eras, fallback, regimes})。

    每个点的 hike/hold/cut/exp_bp 都按**该点所在时代的目标区间**计算，
    点上带 base 字段；基准切换日交给前端画断线与标注。
    """
    range_cols = discover_range_columns(rows)
    winners = displayed_by_day(rows)
    latest_us_day = max(us_day_of(r) for r in rows
                        if winners.get(us_day_of(r)) == (r.get("snapshot_cn") or "").strip())
    eras = derive_target_eras(rows, range_cols, latest_us_day)
    fallback = next(((r.get("current_target") or "").strip()
                     for r in rows if (r.get("current_target") or "").strip()), "350-375")
    d = defaultdict(list)
    for r in rows:
        meeting = (r.get("meeting_date") or "").strip()
        snap = (r.get("snapshot_cn") or "").strip()
        if not meeting or meeting not in meetings:
            continue
        if winners.get(us_day_of(r)) != snap:
            continue
        target = era_target_for(us_day_of(r), eras, fallback)
        st = bucket_stats(r, range_cols, target)
        if st is None:
            continue
        hike, hold, cut, exp, rng, rpv = st
        d[meeting].append({
            "snap": snap, "hike": hike, "hold": hold, "cut": cut,
            "bp": exp, "rng": rng, "rpv": rpv, "base": target,
        })
    for v in d.values():
        v.sort(key=lambda p: p["snap"])
        prev_pt = None
        for p in v:
            # 重大变动标记：同基准下 P(加息) 日环比 ≥ SIG_THR_PP 才算事件；
            # 跨基准（如 2026-09-16 加息落地 350-375→375-400）的 -60pp 是
            # 量的重定义而非市场重定价，图中已由琥珀色断线标注，不进事件层。
            p["sig"] = 0
            if prev_pt is not None and prev_pt["base"] == p["base"]:
                dh = p["hike"] - prev_pt["hike"]
                if abs(dh) >= SIG_THR_PP:
                    p["sig"] = 1 if dh > 0 else -1
            prev_pt = p
    regimes, prev = [], fallback
    for day, label in eras:
        regimes.append({"day": day, "from": prev, "to": label})
        prev = label
    return ({m: d[m] for m in meetings if d.get(m)},
            {"eras": eras, "fallback": fallback, "regimes": regimes})


def chart_payload(points_by_meeting, meetings, kind: str, lo: float, hi: float,
                  yticks: list[float], xmeta: dict, fomc_dates, cur_target: str,
                  regimes, events_by_day=None) -> str:
    """生成一个图表的 SVG 骨架 + 内嵌 JSON（JS paint() 负责画线）。"""
    pw = CHART_W - PAD_L - PAD_R
    ph = CHART_H - PAD_T - PAD_B
    series = []
    for idx, m in enumerate(meetings):
        pts = points_by_meeting.get(m, [])
        if not pts:
            continue
        color = meeting_color(m, idx)
        base = [{"x": p["snap"], "v": p["hike" if kind == "hike" else "bp"],
                 "hold": p["hold"], "cut": p["cut"], "hike": p["hike"], "bp": p["bp"],
                 "rng": p["rng"], "rpv": p["rpv"], "base": p["base"],
                 "sig": p.get("sig", 0)} for p in pts]
        series.append({"md": m, "short": m[5:], "color": color, "kind": kind, "points": base})
        if kind == "hike":
            series.append({"md": m, "short": m[5:], "color": color, "kind": "cut",
                           "points": [{"x": p["snap"], "v": p["cut"], "base": p["base"]}
                                      for p in pts]})
    allx = sorted({pt["x"] for s in series for pt in s["points"]})
    # 主图 A 事件层：{x: [{summary, text, url}]}，只挂「当日 ≥8pp 且已有归因」的点。
    # 主图 B（幅度）不重复挂——事件口径是概率方向，不是 bp 幅度。
    events_out: dict[str, list] = {}
    if kind == "hike" and events_by_day:
        seen_x = set()
        for s in series:
            if s["kind"] != "hike":
                continue
            for pt in s["points"]:
                if not pt.get("sig") or pt["x"] in seen_x:
                    continue
                seen_x.add(pt["x"])
                day = (xmeta.get(pt["x"]) or {}).get("d") or pt["x"][:10]
                evs = events_by_day.get(day)
                if evs:
                    events_out[pt["x"]] = list(evs)
    data = {
        "allx": allx, "n": len(allx), "xmeta": xmeta,
        "lo": lo, "hi": hi,
        "pad_l": PAD_L, "pad_t": PAD_T, "pw": pw, "ph": ph,
        "viewBox_w": CHART_W,
        "fomc_dates": sorted(fomc_dates),
        "regimes": regimes,
        "pagekind": kind, "cur_target": cur_target,
        "events": events_out,
        "series": series,
    }
    parts = [
        f'<svg viewBox="0 0 {CHART_W} {CHART_H}" width="100%" class="linechart" '
        f'style="max-width:{CHART_W}px;font-family:-apple-system,system-ui,sans-serif" '
        f'data-pad-l="{PAD_L}" data-pw="{pw}" data-pad-t="{PAD_T}" data-ph="{ph}" '
        f'data-n-x="{len(allx)}" data-lo="{lo}" data-hi="{hi}">'
    ]
    # Y 网格；chart B（幅度）的 0 线加粗强调 —— 它是“维持当前目标”的分界
    for gv in yticks:
        y = PAD_T + ph * (1 - (gv - lo) / (hi - lo))
        emphasized = kind == "bp" and gv == 0
        stroke = "#94a3b8" if emphasized else "#e5e7eb"
        width = "1.4" if emphasized else "1"
        parts.append(
            f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{PAD_L+pw}" y2="{y:.1f}" '
            f'stroke="{stroke}" stroke-width="{width}"'
            + (' stroke-dasharray="1 0"' if emphasized else "")
            + '/>'
            f'<text class="ylabel" x="{PAD_L-8}" y="{y+4:.1f}" font-size="11" '
            f'fill="{"#475569" if emphasized else "#9ca3af"}" text-anchor="end" '
            f'font-weight="{700 if emphasized else 400}">'
            + (("0" if gv == 0 else f"{gv:+.0f}") if kind == "bp" else f"{gv:.0f}%")
            + '</text>'
        )
    parts.append('<g class="meeting-layer" aria-hidden="true"></g>')
    parts.append('<g class="regime-layer" aria-hidden="true"></g>')
    for s in series:
        parts.append(
            f'<g class="series" data-md="{s["md"]}" data-kind="{s["kind"]}" '
            f'data-color="{s["color"]}"></g>'
        )
    parts.append(
        f'<line class="crosshair" x1="0" y1="{PAD_T}" x2="0" y2="{PAD_T+ph}" '
        f'stroke="#9ca3af" stroke-width="1" stroke-dasharray="3 3" '
        f'style="display:none;pointer-events:none"/>'
        f'<rect class="capture" x="{PAD_L}" y1="{PAD_T}" y="{PAD_T}" width="{pw}" height="{ph}" '
        f'fill="transparent" pointer-events="all" style="cursor:crosshair"/>'
        '</svg>'
        f'<script type="application/json" class="chart-data">{json.dumps(data, ensure_ascii=False)}</script>'
    )
    return "".join(parts)


def legend_html(meetings) -> str:
    parts = []
    for idx, m in enumerate(meetings):
        color = meeting_color(m, idx)
        parts.append(
            f'<button type="button" class="legend-item" data-md="{m}" '
            f'style="display:inline-flex;align-items:center;gap:6px;'
            f'background:{color}22;color:{color};padding:5px 11px;border-radius:14px;'
            f'font-size:12px;font-weight:600;margin:0 6px 8px 0;border:1px solid {color}55;'
            f'cursor:pointer;font-family:inherit;transition:opacity .15s">'
            f'<span style="width:9px;height:9px;background:{color};border-radius:50%"></span>'
            f'{m[5:]} 会议</button>'
        )
    parts.append(
        '<button type="button" class="legend-item legend-cut" data-cut="1" '
        'style="display:inline-flex;align-items:center;gap:6px;background:#f3f4f6;'
        'color:#6b7280;padding:5px 11px;border-radius:14px;font-size:12px;font-weight:600;'
        'margin:0 6px 8px 0;border:1px solid #d1d5db;cursor:pointer;font-family:inherit">'
        '<span style="width:16px;height:0;border-top:2px dashed #94a3b8"></span>降息 P(cut) 虚线</button>'
    )
    parts.append(
        '<span class="chart-key chart-key-meeting">'
        '<span class="chart-key-line" aria-hidden="true"></span>FOMC</span>'
    )
    return "".join(parts)


def controls_html() -> str:
    return (
        '<div class="chart-controls">'
        '<div class="range-row"><span class="range-label">起始</span>'
        '<input type="range" class="range-start" min="0" max="0" value="0" step="1" aria-label="起始日期">'
        '<span class="range-val range-start-val">—</span></div>'
        '<div class="range-row"><span class="range-label">结束</span>'
        '<input type="range" class="range-end" min="0" max="0" value="0" step="1" aria-label="结束日期">'
        '<span class="range-val range-end-val">—</span></div>'
        '<span class="range-meta">共 <b class="range-count">—</b> 天</span>'
        '<button type="button" class="reset-range">重置</button>'
        '</div>'
    )


CSS = """
.dualchart-container{position:relative;margin-top:4px}
.dualchart-container .chart-legend{margin-bottom:10px;display:flex;align-items:center;flex-wrap:wrap;gap:2px 0}
.dualchart-container .chart-key{display:inline-flex;align-items:center;gap:6px;margin:0 6px 8px 2px;padding:5px 10px;
 color:#64748b;font-size:12px;font-weight:600;white-space:nowrap}
.dualchart-container .chart-key-line{display:inline-block;width:16px;height:0;border-top:2px dashed #94a3b8}
.dualchart-container .chart-controls{display:flex;align-items:center;flex-wrap:wrap;gap:14px 18px;
  margin:6px 0 14px;padding:10px 14px;background:#f9fafb;
  border:1px solid #e5e7eb;border-radius:10px;font-size:12px;color:#6b7280}
.dualchart-container .chart-controls .range-row{display:inline-flex;align-items:center;gap:8px}
.dualchart-container .chart-controls .range-label{color:#6b7280;font-size:12px;font-weight:600}
.dualchart-container .chart-controls .range-val{min-width:84px;text-align:center;background:#fff;
  border:1px solid #e5e7eb;border-radius:6px;padding:3px 8px;font-size:12px;
  color:#0b1220;font-weight:600;font-variant-numeric:tabular-nums}
.dualchart-container .chart-controls .range-meta{margin-left:auto;font-size:12px;color:#6b7280}
.dualchart-container .chart-controls .range-meta b{color:#0b1220;font-variant-numeric:tabular-nums;font-weight:700}
.dualchart-container .chart-controls input[type=range]{
  -webkit-appearance:none;appearance:none;height:6px;width:240px;
  background:#e5e7eb;border-radius:4px;outline:none;cursor:pointer}
.dualchart-container .chart-controls input[type=range]::-webkit-slider-thumb{
  -webkit-appearance:none;appearance:none;width:15px;height:15px;
  background:#374151;border-radius:50%;cursor:pointer;
  border:2px solid #fff;box-shadow:0 0 0 1px #374151}
.dualchart-container .chart-controls input[type=range]::-moz-range-thumb{
  width:15px;height:15px;background:#374151;border-radius:50%;
  cursor:pointer;border:2px solid #fff;box-shadow:0 0 0 1px #374151}
.dualchart-container .chart-controls .reset-range{margin-left:0;padding:5px 14px;border-radius:7px;
  border:1px solid #d1d5db;background:#fff;color:#374151;font-size:12px;
  cursor:pointer;font-weight:600;font-family:inherit;transition:background .15s}
.dualchart-container .chart-controls .reset-range:hover{background:#f3f4f6}
.dualchart-container .legend-item{font-family:inherit}
.dualchart-container .legend-item:hover{filter:brightness(0.97)}
.dualchart-container .legend-item.off{opacity:.35;background:#f3f4f6 !important;color:#6b7280 !important;
  border-color:#d1d5db !important}
.dualchart-container .legend-item.off span{background:#9ca3af !important}
.dualchart-container .linechart .series.hidden{display:none}
.dualchart-container .chart-tooltip{position:absolute;background:rgba(15,23,42,.96);color:#fff;
  padding:9px 11px;border-radius:8px;font-size:11.5px;pointer-events:auto;
  display:none;z-index:10;line-height:1.45;width:240px;
  box-sizing:border-box;overflow-y:auto;overflow-x:hidden;overscroll-behavior:contain;
  box-shadow:0 6px 18px rgba(0,0,0,.18);font-variant-numeric:tabular-nums}
.dualchart-container .chart-tooltip.pinned{border:1.5px solid #f59e0c;
  box-shadow:0 8px 24px rgba(245,158,11,.3),0 6px 18px rgba(0,0,0,.2)}
.dualchart-container .chart-tooltip .tip-date{font-weight:700;font-size:12px;margin-bottom:7px;
  color:#e5e7eb;border-bottom:1px solid rgba(255,255,255,.12);padding-bottom:5px}
.dualchart-container .chart-tooltip .tip-row{margin-top:6px}
.dualchart-container .chart-tooltip .tip-row-top{display:flex;align-items:center;gap:6px}
.dualchart-container .chart-tooltip .tip-row-top .tip-nm{flex:1;color:#cbd5e1;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.dualchart-container .chart-tooltip .tip-row-top .tip-pv{font-weight:700;font-size:12.5px}
.dualchart-container .chart-tooltip .tip-row-bot{display:flex;align-items:center;gap:6px;margin-top:2px;
  padding-left:14px;flex-wrap:wrap}
.dualchart-container .chart-tooltip .tip-row-bot .tip-rg{color:#f1f5f9;font-weight:600}
.dualchart-container .chart-tooltip .tip-row-bot .tip-bp{padding:0 5px;border-radius:4px;font-size:10.5px;
  font-weight:600}
.dualchart-container .chart-tooltip .tip-empty{color:#9ca3af;font-style:italic;margin-top:4px}
.dualchart-container .chart-tooltip .tip-events-head{font-weight:700;margin-top:9px;padding-top:7px;
  border-top:1px solid rgba(255,255,255,.18);color:#f59e0b;font-size:11.5px}
.dualchart-container .chart-tooltip .tip-event{margin-top:6px;padding:6px 8px;background:rgba(255,255,255,.04);
  border-radius:6px}
.dualchart-container .chart-tooltip .tip-event-head{font-weight:700;font-size:11.5px;line-height:1.45}
.dualchart-container .chart-tooltip .tip-event-body{color:#cbd5e1;font-size:11px;line-height:1.45;margin-top:3px;
  white-space:pre-line}
.dualchart-container .chart-tooltip:not(.pinned) .tip-event-head{display:-webkit-box;
  -webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.dualchart-container .chart-tooltip:not(.pinned) .tip-event-body{display:-webkit-box;
  -webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;white-space:normal}
.dualchart-container .chart-tooltip:not(.pinned) .tip-event + .tip-event{display:none}
.dualchart-container .chart-tooltip .tip-pin-hint{display:none;color:#9ca3af;font-size:10px;margin-top:5px}
.dualchart-container .chart-tooltip:not(.pinned) .tip-pin-hint{display:block}
.dualchart-container .chart-tooltip:not(.pinned){padding:7px 10px}
.dualchart-container .chart-tooltip:not(.pinned) .tip-date{margin-bottom:4px;padding-bottom:4px}
.dualchart-container .chart-tooltip:not(.pinned) .tip-row{margin-top:5px}
.dualchart-container .chart-tooltip:not(.pinned) .tip-events-head{margin-top:6px;padding-top:5px}
.dualchart-container .chart-tooltip:not(.pinned) .tip-event{margin-top:5px;padding:5px 7px}
.dualchart-container .chart-tooltip:not(.pinned) .tip-pin-hint{margin-top:4px}
.dualchart-container .chart-tooltip .tip-src{color:#93c5fd;text-decoration:none;font-size:10.5px;white-space:nowrap}
.dualchart-container .chart-tooltip .tip-src:hover{text-decoration:underline}
.dualchart-container .event-halo{cursor:pointer}
.dualchart-container .meeting-layer{pointer-events:none}
.dualchart-container .meeting-guide{stroke:#94a3b8;stroke-width:1.25;stroke-dasharray:3 4;opacity:.72}
.dualchart-container .meeting-label rect{fill:#fff;stroke:#cbd5e1;stroke-width:1}
.dualchart-container .meeting-label text{fill:#475569;font-size:10.5px;font-weight:700;letter-spacing:.2px}
"""


# JS 与主看板同一套骨架（tick/FOMC/滑块/十字线/浮窗/click-to-pin），
# 差异：① 虚线降息序列；② 线尾标签放右侧留白；③ 基准切换断线与标注；
# ④ 只绑定 .dualchart-container，与主看板自己的 .chart-container 互不干扰。
JS = r"""
(function(){
  function escHtml(s){
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function bpsLabel(rng, curTarget){
    var m = /^[0-9]+/.exec(rng);
    if (!m) return rng;
    var base = parseInt((curTarget || '350-375').split('-')[0], 10);
    var delta = (parseInt(m[0], 10) - base) / 25;
    if (delta === 0) return '维持';
    if (delta > 0) return '+' + (delta * 25) + 'bp';
    return (delta * 25) + 'bp';
  }

  function initOne(container){
    var svg = container.querySelector('svg.linechart');
    var dataScript = container.querySelector('script.chart-data');
    var legend = container.querySelector('.chart-legend');
    var tip = container.querySelector('.chart-tooltip');
    var startSlider = container.querySelector('.range-start');
    var endSlider = container.querySelector('.range-end');
    var startVal = container.querySelector('.range-start-val');
    var endVal = container.querySelector('.range-end-val');
    var countVal = container.querySelector('.range-count');
    var resetBtn = container.querySelector('.reset-range');
    if (!svg || !dataScript) return;

    var data = JSON.parse(dataScript.textContent);
    var series = data.series;                    // 主序列（hike 或 bp）在前，cut 虚线随后
    var mainSeries = series.filter(function(s){ return s.kind !== 'cut'; });
    var allx = data.allx, n = allx.length;
    var xIndex = {};
    allx.forEach(function(x, i){ xIndex[x] = i; });
    series.forEach(function(s){
      s.pointsByX = {};
      s.points.forEach(function(pt){ s.pointsByX[pt.x] = pt; });
    });
    var pad_l = +svg.dataset.padL, pw = +svg.dataset.pw;
    var pad_t = +svg.dataset.padT, ph = +svg.dataset.ph;
    var lo = +svg.dataset.lo, hi = +svg.dataset.hi;
    var VBW = data.viewBox_w;
    var kind = data.pagekind;
    var xmeta = data.xmeta || {};
    function xDate(snap){ var m = xmeta[snap]; return (m && m.d) || snap.substring(0, 10); }
    function xNote(snap){ var m = xmeta[snap]; return (m && m.t) || snap.substring(11, 16); }
    function xTip(snap){
      var d = xDate(snap), t = xNote(snap);
      if (t === '结算') return d + '（结算值）';
      if (t === '盘中') return d + '（盘中读数）';
      return d + ' 收盘后 ' + t;
    }

    var currentStart = 0, currentEnd = n - 1;
    var active = {};        // 会议显示开关（同时控制该会议的实线与虚线）
    var cutOn = true;       // 降息虚线总开关
    mainSeries.forEach(function(s){ active[s.md] = true; });

    function seriesVisible(s){
      if (s.kind === 'cut') return cutOn && active[s.md];
      return active[s.md];
    }

    function paint(startIdx, endIdx){
      startIdx = Math.max(0, Math.min(n - 2, startIdx | 0));
      endIdx = Math.max(startIdx + 1, Math.min(n - 1, endIdx | 0));
      currentStart = startIdx; currentEnd = endIdx;
      var filteredN = endIdx - startIdx + 1;
      function px(iRel){ return pad_l + (pw * iRel / Math.max(1, filteredN - 1)); }
      function py(v){ return pad_t + ph * (1 - (v - lo) / (hi - lo)); }

      svg.querySelectorAll('text.xtick').forEach(function(el){ el.remove(); });
      var nTicks = Math.min(6, filteredN);
      for (var k = 0; k < nTicks; k++){
        var rel = nTicks === 1 ? 0 : (k * (filteredN - 1) / (nTicks - 1));
        var i = startIdx + Math.round(rel);
        var xpos = px(i - startIdx);
        var anchor = 'middle', tx = xpos;
        if (k === 0 && xpos < 34){ anchor = 'start'; tx = 2; }
        if (k === nTicks - 1 && xpos > VBW - 34){ anchor = 'end'; tx = VBW - 2; }
        var date = allx[i];
        svg.insertAdjacentHTML('beforeend',
          '<text class="xtick" x="' + tx.toFixed(1) + '" y="' + (pad_t + ph + 22) + '" '
          + 'font-size="11" fill="#6b7280" text-anchor="' + anchor + '">'
          + xDate(date).substring(5, 10).replace('-', '/') + '</text>'
          + '<text class="xtick" x="' + tx.toFixed(1) + '" y="' + (pad_t + ph + 38) + '" '
          + 'font-size="9.5" fill="#9ca3af" text-anchor="' + anchor + '">'
          + xNote(date) + '</text>'
        );
      }

      var meetingLayer = svg.querySelector('.meeting-layer');
      if (meetingLayer){
        meetingLayer.innerHTML = '';
        (data.fomc_dates || []).forEach(function(meetingDate){
          var meetingIdx = -1;
          for (var mi = startIdx; mi <= endIdx; mi++){
            if (xDate(allx[mi]) === meetingDate){ meetingIdx = mi; break; }
          }
          if (meetingIdx < 0) return;
          var meetingX = px(meetingIdx - startIdx);
          meetingLayer.insertAdjacentHTML('beforeend',
            '<line class="meeting-guide" x1="' + meetingX.toFixed(1) + '" y1="' + pad_t
            + '" x2="' + meetingX.toFixed(1) + '" y2="' + (pad_t + ph) + '"/>'
            + '<g class="meeting-label" transform="translate(' + meetingX.toFixed(1) + ',0)">'
            + '<rect x="-20" y="' + (pad_t - 25) + '" width="40" height="18" rx="4"/>'
            + '<text x="0" y="' + (pad_t - 12) + '" text-anchor="middle">FOMC</text></g>'
          );
        });
      }

      // 基准切换（FOMC 加减息落地）：琥珀色虚线 + 标注牌。折线在此处断开
      // （切换前后不是同一个量：旧基准的 P(加息) ≠ 新基准的 P(加息)）。
      var regimeLayer = svg.querySelector('.regime-layer');
      if (regimeLayer){
        regimeLayer.innerHTML = '';
        (data.regimes || []).forEach(function(rg){
          var gi = -1;
          for (var ri = startIdx; ri <= endIdx; ri++){
            if (xDate(allx[ri]) >= rg.day){ gi = ri; break; }
          }
          if (gi < 0) return;
          var gx = px(gi - startIdx);
          var delta = 0;
          try { delta = parseInt(rg.to.split('-')[0], 10) - parseInt(rg.from.split('-')[0], 10); } catch (e) {}
          var tag = (delta > 0 ? '+' : '') + delta + 'bp';
          // 标注牌默认画在竖线右侧；靠近右缘时会撞线尾标签带（pad_r 区），
          // 09/16 切换日全区间显示时就压在 12-09/01-27 的标签盒下面，翻到左侧。
          // 文字必须收进盒内——第二行曾超宽溢出、尾部钻进线尾标签底下读不到。
          var flip = (gx + 168) > (pad_l + pw);
          var bx = flip ? -168 : 4, tx = flip ? -158 : 10;
          regimeLayer.insertAdjacentHTML('beforeend',
            '<line x1="' + gx.toFixed(1) + '" y1="' + pad_t + '" x2="' + gx.toFixed(1)
            + '" y2="' + (pad_t + ph) + '" stroke="#d97706" stroke-width="1.6" '
            + 'stroke-dasharray="2 3" opacity="0.9"/>'
            + '<g transform="translate(' + gx.toFixed(1) + ',' + (pad_t + 6) + ')" pointer-events="none">'
            + '<rect x="' + bx + '" y="0" rx="4" width="164" height="46" fill="#fffbeb" stroke="#d97706"/>'
            + '<text x="' + tx + '" y="13" font-size="10" font-weight="700" fill="#92400e">'
            + '目标切换 ' + rg.day.substring(5).replace('-', '/') + '</text>'
            + '<text x="' + tx + '" y="25" font-size="9.5" fill="#92400e">'
            + rg.from + ' → ' + rg.to + '（' + tag + '）</text>'
            + '<text x="' + tx + '" y="37" font-size="9" fill="#92400e">折线断开，基准随之切换</text>'
            + '</g>'
          );
        });
      }

      // 目标切换标注牌提到数据层之上：翻到左侧时落在 85-100% 密集线区，
      // 压在折线/数据点下面会读不出来（regime-layer 初始在 series 之前）。
      if (regimeLayer && regimeLayer.parentNode === svg) svg.appendChild(regimeLayer);

      var endLabels = [];
      series.forEach(function(s){
        var g = svg.querySelector('g.series[data-md="' + s.md + '"][data-kind="' + s.kind + '"]');
        if (!g) return;
        g.innerHTML = '';
        if (!seriesVisible(s)){ g.classList.add('hidden'); return; }
        g.classList.remove('hidden');
        var slice = s.points.filter(function(pt){
          var idx = xIndex[pt.x];
          return idx !== undefined && idx >= startIdx && idx <= endIdx;
        });
        if (slice.length === 0) return;
        var isCut = s.kind === 'cut';
        var pathD = '';
        var prevBase = null;
        slice.forEach(function(pt, j){
          var xRel = xIndex[pt.x] - startIdx;
          var cmd = (j === 0 || pt.base !== prevBase) ? 'M' : 'L';
          pathD += (pathD ? ' ' : '') + cmd + px(xRel).toFixed(1) + ',' + py(pt.v).toFixed(1);
          prevBase = pt.base;
        });
        g.insertAdjacentHTML('beforeend',
          '<path d="' + pathD + '" fill="none" stroke="' + s.color + '" '
          + (isCut ? 'stroke-width="1.6" stroke-dasharray="5 4" opacity="0.8"'
                   : 'stroke-width="2.5"')
          + ' stroke-linejoin="round" stroke-linecap="round"/>'
        );
        slice.forEach(function(pt){
          var cxStr = px(xIndex[pt.x] - startIdx).toFixed(1);
          var cyStr = py(pt.v).toFixed(1);
          if (isCut){
            g.insertAdjacentHTML('beforeend',
              '<circle cx="' + cxStr + '" cy="' + cyStr + '" r="2.2" '
              + 'fill="#fff" stroke="' + s.color + '" stroke-width="1.2" opacity="0.7"/>');
          } else if (pt.sig && (data.events || {})[pt.x]){
            // 重大变动日（P(加息) 同基准日环比 ≥8pp 且已有归因）：光环 + 实心点。
            // 橙 = 当日线上涨（更鹰）、蓝 = 下跌（更鸽），颜色跟本图线值方向走。
            // 尚未归因的大变动日不画光环——光环是对读者「悬停有解释」的承诺。
            var halo = pt.sig > 0 ? '#fb923c' : '#60a7fa';
            g.insertAdjacentHTML('beforeend',
              '<circle cx="' + cxStr + '" cy="' + cyStr + '" r="6.5" '
              + 'fill="none" stroke="' + halo + '" stroke-width="2" opacity="0.45" class="event-halo"/>'
              + '<circle cx="' + cxStr + '" cy="' + cyStr + '" r="3.5" '
              + 'fill="' + halo + '" stroke="#fff" stroke-width="2" class="event-dot"/>');
          } else {
            g.insertAdjacentHTML('beforeend',
              '<circle cx="' + cxStr + '" cy="' + cyStr + '" r="3.5" '
              + 'fill="#fff" stroke="' + s.color + '" stroke-width="2"/>');
          }
        });
        if (!isCut){
          var last = slice[slice.length - 1];
          endLabels.push({
            g: g, color: s.color, short: s.short, kind: s.kind,
            x: px(xIndex[last.x] - startIdx), y: py(last.v),
            v: last.v, rng: last.rng, rpv: last.rpv
          });
        }
      });

      // 线尾标签放右侧留白带（pad_r=190 专用），纵向避让：
      // 先做顶部钳制再向下推开——顺序反了的话，钳制会压缩已算好的间距
      // （主图 A 三线同收 100% 时曾因此只剩 28px 间距 < 盒高 34px，标签互相压字）。
      endLabels.sort(function(a, b){ return a.y - b.y; });
      var GAP = 44;
      var yTop = pad_t + 6, yBot = pad_t + ph + 4;
      for (var lc = 0; lc < endLabels.length; lc++){
        if (endLabels[lc].y < yTop) endLabels[lc].y = yTop;
      }
      endLabels.sort(function(a, b){ return a.y - b.y; });
      for (var li = 1; li < endLabels.length; li++){
        if (endLabels[li].y - endLabels[li - 1].y < GAP)
          endLabels[li].y = endLabels[li - 1].y + GAP;
      }
      if (endLabels.length){
        var over = endLabels[endLabels.length - 1].y - yBot;
        if (over > 0){ for (var lj = 0; lj < endLabels.length; lj++) endLabels[lj].y -= over; }
        var under = yTop - endLabels[0].y;
        if (under > 0){ for (var lu = 0; lu < endLabels.length; lu++) endLabels[lu].y += under; }
      }
      var labelLayer = svg.querySelector('.end-label-layer');
      if (!labelLayer){
        labelLayer = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        labelLayer.setAttribute('class', 'end-label-layer');
        labelLayer.setAttribute('pointer-events', 'none');
        svg.appendChild(labelLayer);
      }
      labelLayer.innerHTML = '';
      for (var lk = 0; lk < endLabels.length; lk++){
        var L = endLabels[lk];
        var sub = (kind === 'hike')
          ? 'P(加息) ' + L.v.toFixed(1) + '%'
          : (L.v >= 0 ? '+' : '') + L.v.toFixed(1) + 'bp';
        labelLayer.insertAdjacentHTML('beforeend',
          '<g transform="translate(' + (pad_l + pw + 10) + ',' + (L.y - 12).toFixed(1) + ')">'
          + '<rect x="0" y="-12" rx="4" width="172" height="34" fill="#ffffff" fill-opacity="0.96" '
          + 'stroke="' + L.color + '" stroke-opacity="0.55"/>'
          + '<text x="8" y="2" font-size="11.5" font-weight="700" fill="' + L.color + '">'
          + L.short + ' 会议</text>'
          + '<text x="8" y="18" font-size="11" fill="#374151" font-weight="600">' + sub + '</text>'
          + '</g>'
        );
      }

      if (startVal) startVal.textContent = xDate(allx[startIdx]);
      if (endVal) endVal.textContent = xDate(allx[endIdx]);
      if (countVal) countVal.textContent = filteredN;
    }

    if (startSlider){
      startSlider.min = 0; startSlider.max = n - 1; startSlider.value = 0;
      startSlider.addEventListener('input', function(){
        var s = +startSlider.value, e = +endSlider.value;
        if (s >= e){ s = e - 1; startSlider.value = s; }
        paint(s, e); hideTip();
      });
    }
    if (endSlider){
      endSlider.min = 0; endSlider.max = n - 1; endSlider.value = n - 1;
      endSlider.addEventListener('input', function(){
        var s = +startSlider.value, e = +endSlider.value;
        if (e <= s){ e = s + 1; endSlider.value = e; }
        paint(s, e); hideTip();
      });
    }
    if (resetBtn){
      resetBtn.addEventListener('click', function(){
        startSlider.value = 0; endSlider.value = n - 1;
        paint(0, n - 1); hideTip();
      });
    }

    // 图例：会议 chip 切换该会议全部线；降息 chip 只切虚线
    legend.querySelectorAll('.legend-item').forEach(function(btn){
      btn.addEventListener('click', function(){
        if (btn.dataset.cut){
          cutOn = !cutOn;
          btn.classList.toggle('off', !cutOn);
        } else {
          var md = btn.dataset.md;
          active[md] = !active[md];
          btn.classList.toggle('off', !active[md]);
        }
        paint(currentStart, currentEnd);
        hideTip();
      });
    });

    // ---- hover 浮窗（停靠绘图区右下角）+ click-to-pin ----
    var cross = svg.querySelector('.crosshair');
    var capture = svg.querySelector('.capture');
    var tipHovered = false;
    var hideTimer = null;
    var pinned = false;

    function setPinned(on){
      pinned = on;
      tip.classList.toggle('pinned', on);
      if (tip.style.display === 'block') positionTip();
    }
    function togglePin(){
      if (tip.style.display !== 'block') return;
      setPinned(!pinned);
      if (pinned){ cancelHide(); cross.style.display = ''; }
      else if (!tipHovered){ scheduleHide(); }
    }
    function tipRows(snap){
      var parts = [];
      mainSeries.forEach(function(s){
        if (!active[s.md]) return;
        var pt = s.pointsByX[snap];
        if (!pt) return;
        var head, bot;
        if (kind === 'hike'){
          head = pt.hike.toFixed(2) + '%';
          bot = '<span class="tip-rg">维持 ' + pt.hold.toFixed(1) + '% · 降息 ' + pt.cut.toFixed(1) + '%</span>'
              + '<span class="tip-bp" style="background:' + s.color + '33;color:' + s.color + '">'
              + '预期 ' + (pt.bp >= 0 ? '+' : '') + pt.bp.toFixed(1) + 'bp</span>';
        } else {
          head = (pt.v >= 0 ? '+' : '') + pt.v.toFixed(1) + 'bp';
          bot = '<span class="tip-rg">P(加息) ' + pt.hike.toFixed(1) + '%</span>'
              + '<span class="tip-bp" style="background:' + s.color + '33;color:' + s.color + '">'
              + '最大档 ' + pt.rng + ' ' + bpsLabel(pt.rng, pt.base) + '</span>';
        }
        parts.push(
          '<div class="tip-row">'
          + '<div class="tip-row-top">'
          + '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;flex:none;background:' + s.color + '"></span>'
          + '<span class="tip-nm">' + s.short + ' 会议</span>'
          + '<span class="tip-pv" style="color:' + s.color + '">' + head + '</span>'
          + '</div>'
          + '<div class="tip-row-bot">' + bot + '</div>'
          + '</div>'
        );
      });
      return parts;
    }

    function eventSectionHtml(evs){
      var out = ['<div class="tip-events-head">⚠ 当日 P(加息) 大幅变动（同基准日环比 ≥8pp）</div>'];
      evs.forEach(function(ev){
        var links = String(ev.url || '').split('|').filter(Boolean).map(function(u, i){
          return '<a class="tip-src" href="' + escHtml(u) + '" target="_blank" rel="noopener">查看来源'
                 + (i > 0 ? ' ' + (i + 1) : '') + '</a>';
        }).join(' ');
        out.push(
          '<div class="tip-event">'
          + '<div class="tip-event-head">' + escHtml(ev.summary || '') + '</div>'
          + (ev.text ? '<div class="tip-event-body">' + escHtml(ev.text) + '</div>' : '')
          + links
          + '</div>'
        );
      });
      out.push('<div class="tip-pin-hint">📄 点击图表钉住浮窗，查看全文'
               + (evs.length > 1 ? '与全部 ' + evs.length + ' 条归因' : '') + '</div>');
      return out.join('');
    }

    capture.addEventListener('mousemove', function(e){
      if (pinned) return;
      cancelHide();
      var rect = svg.getBoundingClientRect();
      var ratio = VBW / rect.width;
      var sx = (e.clientX - rect.left) * ratio;
      var filteredN = currentEnd - currentStart + 1;
      var iRel = Math.round((sx - pad_l) / pw * Math.max(1, filteredN - 1));
      var i = currentStart + iRel;
      if (i < currentStart || i > currentEnd){ hideTip(); return; }
      var xp = pad_l + pw * (i - currentStart) / Math.max(1, filteredN - 1);
      cross.setAttribute('x1', xp); cross.setAttribute('x2', xp);
      cross.style.display = '';
      var snap = allx[i];
      // 基准挂在日期行：同一天各会议基准相同，放每行会把徽章挤成两行
      var baseTxt = '';
      if (kind === 'hike'){
        for (var si = 0; si < mainSeries.length; si++){
          var ppt = mainSeries[si].pointsByX[snap];
          if (ppt){ baseTxt = ppt.base; break; }
        }
      }
      var parts = ['<div class="tip-date">📅 ' + xTip(snap)
                   + (baseTxt ? ' · 基准 ' + baseTxt : '') + '</div>'].concat(tipRows(snap));
      if (parts.length === 1) parts.push('<div class="tip-empty">所有会议已关闭，请点击图例开启</div>');
      var evsHere = (data.events || {})[snap];
      if (evsHere && evsHere.length) parts.push(eventSectionHtml(evsHere));
      tip.innerHTML = parts.join('');
      tip.style.display = 'block';
      positionTip();
    });

    function positionTip(){
      var hr = container.getBoundingClientRect();
      var sr = svg.getBoundingClientRect();
      var svgLeft = sr.left - hr.left, svgTop = sr.top - hr.top;
      var scale = sr.width / VBW;
      var tipW = Math.min(320, Math.round(hr.width * 0.82));
      tip.style.width = tipW + 'px';
      var plotBottomCss = svgTop + (pad_t + ph) * scale;
      // 悬停十字线在中线右侧时浮窗停靠左侧、反之右侧——别盖住要解释的那个点
      //（光环密集带常在右半段，事件日悬停时正好落在浮窗上）。
      var crossX = parseFloat(cross.getAttribute('x1') || '0') || 0;
      var dockLeft = crossX > (pad_l + pw / 2);
      tip.style.left = Math.round(dockLeft
        ? svgLeft + (pad_l + 8) * scale
        : svgLeft + (pad_l + pw) * scale - tipW - 6 * scale) + 'px';
      tip.style.top = 'auto';
      tip.style.bottom = Math.round(hr.height - plotBottomCss + 6 * scale) + 'px';
      // 高度上限：悬停态从锚点（绘图区底 + 6px）向上最多长到 SVG 顶再留
      // 12px 顶留白余量——不碰图例/滑块；多条归因悬停时只显示第一条（其余
      // 钉住后展开）。钉住态（读者主动点击）展开全文，上限跟视口走，
      // 向上溢出容器只盖图例，z-index 在上面。
      tip.style.maxHeight = (pinned
        ? Math.round(Math.max(ph * scale - 12, Math.min((window.innerHeight || 800) * 0.8, 660)))
        : Math.round((pad_t + ph) * scale + 12)) + 'px';
    }
    function doHide(){ hideTimer = null; tip.style.display = 'none'; cross.style.display = 'none'; }
    function cancelHide(){ if (hideTimer){ clearTimeout(hideTimer); hideTimer = null; } }
    function hideTip(){ cancelHide(); tipHovered = false; setPinned(false); doHide(); }
    function scheduleHide(){
      if (pinned) return;
      if (hideTimer) clearTimeout(hideTimer);
      hideTimer = setTimeout(function(){
        if (tipHovered){ hideTimer = null; return; }
        doHide();
      }, 400);
    }
    capture.addEventListener('mousemove', cancelHide);
    capture.addEventListener('click', togglePin);
    container.addEventListener('mouseleave', scheduleHide);
    tip.addEventListener('mouseenter', function(){ tipHovered = true; cancelHide(); });
    tip.addEventListener('mouseleave', function(){ tipHovered = false; scheduleHide(); });

    paint(0, n - 1);
  }

  function boot(){
    document.querySelectorAll('.dualchart-container').forEach(initOne);
  }
  if (document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', boot);
  } else { boot(); }
})();
"""


def _chart_card(h2: str, sub: str, meetings, chart: str) -> str:
    return (
        f'<div class="card">'
        f'<h2>{h2}</h2>'
        f'<div style="font-size:12.5px;color:#6b7280;margin:0 0 12px">{sub}</div>'
        f'<div class="dualchart-container">'
        f'<div class="chart-legend">{legend_html(meetings)}</div>'
        f'{controls_html()}'
        f'{chart}'
        f'<div class="chart-tooltip"></div>'
        f'</div></div>'
    )


def dual_charts_block(rows, meetings, xmeta, fomc_dates):
    """→ (两张图卡片的完整 HTML, ctx)。ctx 供页面头部/卡片/说明复用同一份数据。"""
    points, era_info = build_series(rows, meetings)
    if not points:
        return "", {"points": points, "era_info": era_info}
    fallback = era_info["fallback"]
    latest_base = next((pts[-1]["base"] for pts in points.values() if pts), fallback)
    last_regime = era_info["regimes"][-1] if era_info["regimes"] else None
    cur_target_disp = (f"{latest_base}（{last_regime['day']} 决议后）"
                       if last_regime and last_regime["to"] == latest_base else latest_base)
    if era_info["regimes"]:
        print("目标区间时代: fallback=%s" % fallback,
              "| 切换:", "; ".join(f"{r['day']} {r['from']}→{r['to']}" for r in era_info["regimes"]))

    bps = [p["bp"] for pts in points.values() for p in pts]
    lo_bp = min(0.0, min(bps))
    hi_bp = max(0.0, max(bps))
    lo_bp = int(lo_bp // 25) * 25
    hi_bp = int(-(-hi_bp // 25)) * 25
    if hi_bp == lo_bp:
        hi_bp = lo_bp + 25
    bp_ticks = [float(t) for t in range(int(lo_bp), int(hi_bp) + 1, 25)]

    chart_a = chart_payload(points, meetings, "hike", 0.0, 100.0,
                            list(range(0, 101, 20)), xmeta, fomc_dates, latest_base,
                            era_info["regimes"], events_by_day=load_events_by_day(rows))
    chart_b = chart_payload(points, meetings, "bp", float(lo_bp), float(hi_bp),
                            bp_ticks, xmeta, fomc_dates, latest_base,
                            era_info["regimes"])

    html = (
        f"<style>{CSS}</style>\n"
        + _chart_card(
            "主图 A · 方向：累计加息概率 P(加息)",
            "实线 = P(加息)（落在现行目标区间上方任意档位的累计概率）；虚线 = P(降息)。"
            "两条线定义恒定，不随「最大概率区间」切换；目标切换处折线断开并标注。"
            "橙/蓝光环 = 当日 P(加息) 同基准日环比 ≥8pp 且已有归因，悬停查看原因与来源。",
            meetings, chart_a)
        + _chart_card(
            "主图 B · 幅度：预期变动 bp",
            "每条线 = 该会议概率分布的期望落点 − 现行目标中点。P(加息) 饱和到 100% 后，"
            "这一图仍能反映「加多少」的重定价；0 线 = 维持当前目标。",
            meetings, chart_b)
        + f"\n<script>{JS}</script>"
    )
    ctx = {
        "points": points, "era_info": era_info, "cur_base": latest_base,
        "cur_target_disp": cur_target_disp, "bp_ticks": bp_ticks,
    }
    return html, ctx
