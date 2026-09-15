#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_report.py — 读取 data/fedwatch_probabilities.csv，
生成自包含 HTML 看板 report/index.html。

核心图表：未来三次 FOMC 会议「最大概率加息水平」随时间变化折线图。

数据列：
  snapshot_cn         — 北京时间采集时刻（主键之一）
  snapshot_quikstrike — QuikStrike 页面 "Data as of" 时间戳
  meeting_date        — FOMC 会议日期
  current_target      — 当前目标区间，如 "350-375"
  agg_p_hike_pct      — 累计加息概率（Agg 口径）
  agg_p_hold_pct      — 累计维持概率
  agg_p_cut_pct       — 累计降息概率
  max_range_label     — 该会议最大概率利率区间，如 "375-400"
  max_range_pct       — 该区间概率
  aggregated_ranges   — JSON，所有区间的概率
  range_XXX_YYY_pct   — 各区间单列
"""

from __future__ import annotations

import csv
import json
import os
from collections import defaultdict
from datetime import date as date_t

from site_seo import build_static_site, validate_site

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE_DIR, "data", "fedwatch_probabilities.csv")
OUT = os.path.join(BASE_DIR, "report", "index.html")

# 默认关注接下来 3 次会议；会自动按 meeting_date 升序取最新快照里最早的 3 个
DEFAULT_NEXT_N = 3

# FOMC 决议日（两日会议的第二天），用于在折线图中标注历史与未来会议。
# 来源：Federal Reserve FOMC meeting calendars
# https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
FOMC_DECISION_DATES = [
    # 2025
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    # 2026
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
    # 2027
    "2027-01-27", "2027-03-17", "2027-04-28", "2027-06-09",
    "2027-07-28", "2027-09-15", "2027-10-27", "2027-12-08",
]

# 把 bps 区间格式化成 "3.75% - 4.00%"
def pretty_range(rng: str) -> str:
    if not rng or "-" not in rng:
        return rng or "-"
    lo, hi = rng.split("-")
    def pct(x): return f"{int(x)/100:.2f}%"
    return f"{pct(lo)} - {pct(hi)}"


# 给不同会议分色（足够区分）；颜色用于折线
def meeting_color(md: str, idx: int) -> str:
    palette = [
        "#1d4ed8",  # indigo
        "#c2410c",  # orange-brown
        "#7c3aed",  # purple
        "#0f766e",  # teal
        "#be185d",  # pink
    ]
    return palette[idx % len(palette)]


def load():
    if not os.path.exists(CSV_PATH):
        return []
    with open(CSV_PATH, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_events() -> dict:
    """读 data/events.csv → {snapshot_cn: [{direction, summary, text, url}, ...]}"""
    p = os.path.join(BASE_DIR, "data", "events.csv")
    if not os.path.exists(p):
        return {}
    out = {}
    with open(p, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            snap = (row.get("snapshot_cn") or "").strip()
            if not snap:
                continue
            out.setdefault(snap, []).append({
                "direction": (row.get("direction") or "hawk").strip(),
                "summary": (row.get("summary") or "").strip(),
                "text": (row.get("text") or "").strip(),
                "url": (row.get("url") or "").strip(),
            })
    return out


def next_n_meetings(latest_rows, n: int = DEFAULT_NEXT_N) -> list[str]:
    """从最新快照里取最早的 n 个会议日期。"""
    meetings = sorted({r["meeting_date"] for r in latest_rows})
    return meetings[:n]


def series_max_pct(rows, meetings):
    """{meeting_date: [(snapshot_cn, max_range_label, max_range_pct), ...]}.

    QuikStrike 的 AllMeetings 历史文件会为尚未进入可计算窗口的远期会议
    写入全 0 占位行；这些行没有 max_range_label，不是实际的 0% 概率，
    因此必须跳过，折线应从该会议首次出现有效概率的日期开始。
    """
    d = defaultdict(list)
    for r in rows:
        meeting = (r.get("meeting_date") or "").strip()
        snapshot = (r.get("snapshot_cn") or "").strip()
        label = (r.get("max_range_label") or "").strip()
        raw_pct = (r.get("max_range_pct") or "").strip()
        if not meeting or not snapshot or not label or not raw_pct:
            continue
        try:
            pct = float(raw_pct)
        except ValueError:
            continue
        if pct <= 0:
            continue
        d[meeting].append((snapshot, label, pct))
    for v in d.values():
        v.sort()
    return {m: d[m] for m in meetings}


def bps_axis_label(rng: str) -> str:
    """如 '375-400' -> '+25bp' 或 '基准' 等简短标签。"""
    try:
        lo = int(rng.split("-")[0])
    except Exception:
        return rng
    delta = (lo - 350) // 25
    if delta == 0:
        return "维持"
    if delta > 0:
        return f"+{delta*25}bp"
    return f"{delta*25}bp"


def line_chart_max_pct(series_map, events_by_snap=None, width=920, height=380, today_label=None):
    """3 条会议曲线 + 数据点；可点击图例、可双滑块筛选日期范围、hover 显示 tooltip。

    X 轴刻度、折线 path/circle/end-label 都由 JS paint() 重画，
    Python 这边只输出静态骨架（Y 网格 + 3 个空 g.series + 十字线 + 命中层）。

    events_by_snap: 由 load_events() 得到的 {snapshot_cn: [{direction, summary, text, url}, ...]}
                    —— paint() 据此给「重大变动日」数据点加光环、tooltip 增加事件信号。
    """
    # pad_r 是绘图区右侧留白：既放各曲线末端标签，也作为 hover 浮窗的停靠区。
    # 浮窗停靠在这里 → 与折线图零重叠，绝不会盖住任何数据点。
    pad_l, pad_r, pad_t, pad_b = 60, 250, 32, 60
    allx = sorted({x for v in series_map.values() for x, _, _ in v})
    if not allx:
        return f'<svg width="{width}" height="60"></svg>'
    lo, hi = 0.0, 100.0
    pw, ph = width - pad_l - pad_r, height - pad_t - pad_b

    chart_data = {
        "allx": allx,
        "n": len(allx),
        "lo": lo,
        "hi": hi,
        "pad_l": pad_l,
        "pad_t": pad_t,
        "pw": pw,
        "ph": ph,
        "viewBox_w": width,
        "ranges": [
            r.split("-") for r in ("325-350","350-375","375-400","400-425","425-450","450-475","475-500")
        ],
        "events_by_snap": events_by_snap or {},
        "fomc_dates": list(FOMC_DECISION_DATES),
        "series": [
            {
                "md": m,
                "short": m[5:],
                "color": meeting_color(m, idx),
                "points": [
                    {"x": x, "rng": rng, "v": float(v)}
                    for (x, rng, v) in pts
                ],
            }
            for idx, (m, pts) in enumerate(series_map.items()) if pts
        ],
    }

    parts = [
        f'<svg viewBox="0 0 {width} {height}" width="100%" class="linechart" '
        f'style="max-width:{width}px;font-family:-apple-system,system-ui,sans-serif" '
        f'data-pad-l="{pad_l}" data-pw="{pw}" data-pad-t="{pad_t}" data-ph="{ph}" '
        f'data-n-x="{len(allx)}" data-lo="{lo}" data-hi="{hi}">'
    ]
    # Y 网格 + Y 轴标签（永久不变）
    for gv in range(0, 101, 20):
        y = pad_t + ph * (1 - (gv - lo) / (hi - lo))
        parts.append(
            f'<line class="ygrid" x1="{pad_l}" y1="{y:.1f}" x2="{pad_l+pw}" y2="{y:.1f}" '
            f'stroke="#e5e7eb" stroke-width="1"/>'
            f'<text class="ylabel" x="{pad_l-8}" y="{y+4:.1f}" font-size="11" fill="#9ca3af" '
            f'text-anchor="end">{gv}%</text>'
        )
    # FOMC 会议日标注层：由 JS paint() 按日期筛选重画，放在折线之前避免遮挡曲线。
    parts.append('<g class="meeting-layer" aria-hidden="true"></g>')
    # 3 个会议系列占位 <g> —— 其内 path/circle/end-label 由 JS paint() 重画以响应日期筛选
    for idx, (m, pts) in enumerate(series_map.items()):
        if not pts:
            continue
        color = meeting_color(m, idx)
        parts.append(f'<g class="series" data-md="{m}" data-color="{color}"></g>')

    # 十字线（hover 命中时显示）
    parts.append(
        f'<line class="crosshair" x1="0" y1="{pad_t}" x2="0" y2="{pad_t+ph}" '
        f'stroke="#9ca3af" stroke-width="1" stroke-dasharray="3 3" '
        f'style="display:none;pointer-events:none"/>'
    )
    # 透明命中层（接收 mousemove）
    parts.append(
        f'<rect class="capture" x="{pad_l}" y="{pad_t}" width="{pw}" height="{ph}" '
        f'fill="transparent" pointer-events="all" style="cursor:crosshair"/>'
    )
    parts.append('</svg>')
    parts.append(
        f'<script type="application/json" class="chart-data">'
        f'{json.dumps(chart_data, ensure_ascii=False)}</script>'
    )
    return "".join(parts)


def legend_html(meetings):
    """可点击的图例按钮：切换对应会议折线的显示/隐藏。"""
    parts = []
    for idx, m in enumerate(meetings):
        color = meeting_color(m, idx)
        md = m[5:]  # 09-16
        parts.append(
            f'<button type="button" class="legend-item" data-md="{m}" '
            f'style="display:inline-flex;align-items:center;gap:6px;'
            f'background:{color}22;color:{color};padding:5px 11px;border-radius:14px;'
            f'font-size:12px;font-weight:600;margin:0 6px 8px 0;border:1px solid {color}55;'
            f'cursor:pointer;font-family:inherit;transition:opacity .15s">'
            f'<span style="width:9px;height:9px;background:{color};border-radius:50%"></span>'
            f'{md} 会议</button>'
        )
    parts.append(
        '<span class="chart-key chart-key-meeting" aria-label="FOMC 会议日标记">'
        '<span class="chart-key-line" aria-hidden="true"></span>FOMC'
        '</span>'
    )
    return "".join(parts)


def hero_card(md: str, max_rng: str, max_pct: float, hike: float, hold: float, cut: float, color: str):
    """3 个大卡片之一：当前最大概率区间 + 数值 + 累计加减息概率。"""
    delta_text = bps_axis_label(max_rng)
    pretty = pretty_range(max_rng)
    return (
        f'<div style="background:linear-gradient(135deg,#ffffff 0%,#fafbff 100%);'
        f'border:1px solid {color}55;border-radius:12px;padding:18px 16px;'
        f'box-shadow:0 1px 2px rgba(15,23,42,0.04)">'
        f'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">'
        f'<div style="font-size:13px;color:{color};font-weight:700;letter-spacing:0.4px">'
        f'{md[5:]} FOMC 会议</div>'
        f'<div style="font-size:11px;color:#9ca3af">{md}</div>'
        f'</div>'
        f'<div style="font-size:28px;font-weight:700;color:#111827;'
        f'font-variant-numeric:tabular-nums;letter-spacing:-0.5px">'
        f'{pretty}</div>'
        f'<div style="margin-top:4px;font-size:13px;color:#6b7280">'
        f'<b style="color:{color}">+{max_pct:.2f}%</b> 市场预期达成该水平'
        f'<span style="margin-left:6px;background:{color}1a;color:{color};'
        f'padding:1px 6px;border-radius:6px;font-size:11px;font-weight:600">{delta_text}</span>'
        f'</div>'
        f'<div style="margin-top:12px;display:flex;gap:14px;font-size:11.5px;color:#6b7280">'
        f'<span>累计加息 <b style="color:#dc2626">{hike:.2f}%</b></span>'
        f'<span>维持 <b style="color:#6b7280">{hold:.2f}%</b></span>'
        f'<span>降息 <b style="color:#16a34a">{cut:.2f}%</b></span>'
        f'</div>'
        f'</div>')


def heatmap_table(latest_rows):
    """Aggregated 全会议网格表（区间 × 会议）。"""
    if not latest_rows:
        return ""
    ranges = []
    seen = set()
    for r in latest_rows:
        d = json.loads(r.get("aggregated_ranges") or "{}")
        for k in d.keys():
            if k not in seen:
                ranges.append(k)
                seen.add(k)
    ranges = sorted(ranges, key=lambda x: int(x.split("-")[0]))

    rows_html = []
    for r in sorted(latest_rows, key=lambda r: r["meeting_date"]):
        d = json.loads(r.get("aggregated_ranges") or "{}")
        cells = []
        for rng in ranges:
            v = d.get(rng, 0.0)
            base = 350
            try:
                lo = int(rng.split("-")[0])
            except Exception:
                lo = 350
            color = "#dc2626" if lo > base else ("#16a34a" if lo < base else "#6b7280")
            opacity = max(0.04, min(1, v / 100))
            cells.append(
                f'<td style="background:{color};opacity:{opacity:.2f};'
                f'text-align:right;color:#0b1220">{v:.2f}</td>')
        star_mark = "★" if r.get("max_range_label") else ""
        rows_html.append(
            f'<tr><td>{star_mark}<b>{r["meeting_date"]}</b></td>' + "".join(cells) + "</tr>")
    headers = '<th>会议</th>' + "".join(
        f'<th style="text-align:right">{r} bps</th>' for r in ranges)
    return (f'<table><thead><tr>{headers}</tr></thead>'
            f'<tbody>{"".join(rows_html)}</tbody></table>')


def main():
    rows = load()
    if not rows:
        print("暂无数据")
        return

    # 取「最完整采集日」的最新一次快照（避免半截采集覆盖完整采集）
    by_snap = defaultdict(list)
    for r in rows:
        by_snap[r["snapshot_cn"]].append(r)
    # 按 (会议数, snapshot_cn) 双排序选最完整且最新的
    latest_cn = max(by_snap.keys(),
                    key=lambda s: (len(by_snap[s]), s))
    latest = by_snap[latest_cn]
    cur_target = latest[0].get("current_target", "350-375")
    meetings = next_n_meetings(latest)
    series = series_max_pct(rows, meetings)

    # 3 个 hero 数据
    hero_data = []
    for idx, m in enumerate(meetings):
        rec = next((r for r in latest if r["meeting_date"] == m), None)
        if not rec:
            continue
        hero_data.append({
            "md": m,
            "color": meeting_color(m, idx),
            "max_rng": rec.get("max_range_label", ""),
            "max_pct": float(rec.get("max_range_pct") or 0),
            "hike": float(rec.get("agg_p_hike_pct") or 0),
            "hold": float(rec.get("agg_p_hold_pct") or 0),
            "cut": float(rec.get("agg_p_cut_pct") or 0),
        })

    n_snap = len({r["snapshot_cn"] for r in rows})
    first_snap = min(r["snapshot_cn"] for r in rows)
    n_total_rows = len(rows)

    hero_html = "".join(
        hero_card(h["md"], h["max_rng"], h["max_pct"],
                  h["hike"], h["hold"], h["cut"], h["color"])
        for h in hero_data)
    hero_grid = (
        '<div style="display:grid;grid-template-columns:repeat(3,1fr);'
        'gap:14px;margin-bottom:18px">'
        + hero_html + '</div>')

    chart = line_chart_max_pct(series, events_by_snap=load_events(),
                                width=920, height=380, today_label=latest_cn)
    legend = legend_html(meetings)
    grid = heatmap_table(latest)

    today_str = date_t.today().isoformat()

    html = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CME FedWatch · 利率预期追踪</title>
<style>
body{{margin:0;background:#f9fafb;color:#0b1220;
 font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",system-ui,sans-serif}}
.wrap{{max-width:1040px;margin:0 auto;padding:28px 22px 60px}}
h1{{font-size:24px;margin:0 0 4px;font-weight:700;letter-spacing:-0.4px}}
.sub{{color:#6b7280;font-size:13px;margin-bottom:22px}}
.card{{background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:20px 22px;margin-bottom:16px}}
.card h2{{font-size:15px;margin:0 0 12px;color:#0b1220;font-weight:600}}
.kv{{display:flex;flex-wrap:wrap;gap:30px;margin-bottom:8px}}
.kv div{{font-size:12px;color:#6b7280}}
.kv b{{display:block;font-size:18px;color:#0b1220;margin-top:2px;font-variant-numeric:tabular-nums;font-weight:600}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{padding:7px 10px;border-bottom:1px solid #f1f3f5;text-align:left;
 font-variant-numeric:tabular-nums;white-space:nowrap}}
th{{color:#6b7280;font-weight:500;font-size:12px;background:#fafafa}}
tr:last-child td{{border-bottom:none}}
.note{{font-size:12.5px;color:#6b7280;line-height:1.75}}
.chart-container{{position:relative;margin-top:4px}}
.chart-legend{{margin-bottom:10px;display:flex;align-items:center;flex-wrap:wrap;gap:2px 0}}
.chart-key{{display:inline-flex;align-items:center;gap:6px;margin:0 6px 8px 2px;padding:5px 10px;color:#64748b;font-size:12px;font-weight:600;white-space:nowrap}}
.chart-key-line{{display:inline-block;width:16px;height:0;border-top:2px dashed #94a3b8}}
.chart-controls{{display:flex;align-items:center;flex-wrap:wrap;gap:14px 18px;
  margin:6px 0 14px;padding:10px 14px;background:#f9fafb;
  border:1px solid #e5e7eb;border-radius:10px;font-size:12px;color:#6b7280}}
.chart-controls .range-row{{display:inline-flex;align-items:center;gap:8px}}
.chart-controls .range-label{{color:#6b7280;font-size:12px;font-weight:600}}
.chart-controls .range-val{{min-width:84px;text-align:center;background:#fff;
  border:1px solid #e5e7eb;border-radius:6px;padding:3px 8px;font-size:12px;
  color:#0b1220;font-weight:600;font-variant-numeric:tabular-nums}}
.chart-controls .range-meta{{margin-left:auto;font-size:12px;color:#6b7280}}
.chart-controls .range-meta b{{color:#0b1220;font-variant-numeric:tabular-nums;font-weight:700}}
.chart-controls input[type=range]{{
  -webkit-appearance:none;appearance:none;height:6px;width:240px;
  background:#e5e7eb;border-radius:4px;outline:none;cursor:pointer}}
.chart-controls input[type=range]::-webkit-slider-thumb{{
  -webkit-appearance:none;appearance:none;width:15px;height:15px;
  background:#374151;border-radius:50%;cursor:pointer;
  border:2px solid #fff;box-shadow:0 0 0 1px #374151}}
.chart-controls input[type=range]::-moz-range-thumb{{
  width:15px;height:15px;background:#374151;border-radius:50%;
  cursor:pointer;border:2px solid #fff;box-shadow:0 0 0 1px #374151}}
.chart-controls .reset-range{{margin-left:0;padding:5px 14px;border-radius:7px;
  border:1px solid #d1d5db;background:#fff;color:#374151;font-size:12px;
  cursor:pointer;font-weight:600;font-family:inherit;transition:background .15s}}
.chart-controls .reset-range:hover{{background:#f3f4f6}}
.legend-item{{font-family:inherit}}
.legend-item:hover{{filter:brightness(0.97)}}
.legend-item.off{{opacity:.35;background:#f3f4f6 !important;color:#6b7280 !important;
  border-color:#d1d5db !important}}
.legend-item.off span{{background:#9ca3af !important}}
.linechart .series.hidden{{display:none}}
.chart-tooltip{{position:absolute;background:rgba(15,23,42,.96);color:#fff;
  padding:9px 11px;border-radius:8px;font-size:11.5px;pointer-events:auto;
  display:none;z-index:10;line-height:1.45;width:210px;
  box-sizing:border-box;overflow-y:auto;overflow-x:hidden;overscroll-behavior:contain;
  box-shadow:0 6px 18px rgba(0,0,0,.18);font-variant-numeric:tabular-nums}}
.chart-tooltip .tip-date{{font-weight:700;font-size:12px;margin-bottom:7px;
  color:#e5e7eb;border-bottom:1px solid rgba(255,255,255,.12);padding-bottom:5px}}
.chart-tooltip .tip-row{{margin-top:6px}}
.chart-tooltip .tip-row-top{{display:flex;align-items:center;gap:6px}}
.chart-tooltip .tip-row-top .tip-nm{{flex:1;color:#cbd5e1;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}}
.chart-tooltip .tip-row-top .tip-pv{{font-weight:700;font-size:12.5px}}
.chart-tooltip .tip-row-bot{{display:flex;align-items:center;gap:6px;margin-top:2px;
  padding-left:14px}}
.chart-tooltip .tip-row-bot .tip-rg{{color:#f1f5f9;font-weight:600}}
.chart-tooltip .tip-row-bot .tip-bp{{padding:0 5px;border-radius:4px;font-size:10.5px;
  font-weight:600}}
.chart-tooltip .tip-empty{{color:#9ca3af;font-style:italic;margin-top:4px}}
.chart-tooltip .tip-events-head{{font-weight:700;margin-top:9px;padding-top:7px;
  border-top:1px solid rgba(255,255,255,.18);color:#f59e0b;font-size:11.5px}}
.chart-tooltip .tip-events-head.hawk{{color:#fb923c}}
.chart-tooltip .tip-events-head.dove{{color:#60a5fa}}
.chart-tooltip .tip-event{{margin-top:6px;padding:6px 8px;background:rgba(255,255,255,.04);
  border-radius:6px}}
.chart-tooltip .tip-event-head{{font-weight:700;font-size:11.5px;line-height:1.45}}
.chart-tooltip .tip-event-body{{color:#cbd5e1;font-size:11px;line-height:1.45;margin-top:3px}}
.chart-tooltip .tip-src{{color:#93c5fd;text-decoration:none;font-size:10.5px;white-space:nowrap}}
.chart-tooltip .tip-src:hover{{text-decoration:underline}}
/* click-to-pin：固定后浮窗加琥珀色描边，并显示提示条；链接加粗放大更好点 */
.chart-tooltip .tip-pin{{display:none;align-items:center;gap:5px;margin:-1px 0 8px;
  padding:4px 7px;border-radius:5px;background:rgba(251,191,36,.15);
  color:#fcd34d;font-size:10.5px;font-weight:700;line-height:1.35}}
.chart-tooltip.pinned .tip-pin{{display:flex}}
.chart-tooltip.pinned{{border:1.5px solid #f59e0b;
  box-shadow:0 8px 24px rgba(245,158,11,.3),0 6px 18px rgba(0,0,0,.2)}}
.chart-tooltip.pinned .tip-src{{font-size:11.5px;font-weight:700;text-decoration:underline}}
.chart-tooltip .tip-pin .tip-pin-key{{margin-left:auto;font-weight:500;opacity:.8;
  white-space:nowrap}}
.meeting-layer{{pointer-events:none}}
.meeting-guide{{stroke:#94a3b8;stroke-width:1.25;stroke-dasharray:3 4;opacity:.72}}
.meeting-label rect{{fill:#fff;stroke:#cbd5e1;stroke-width:1}}
.meeting-label text{{fill:#475569;font-size:10.5px;font-weight:700;letter-spacing:.2px}}
.event-halo{{cursor:pointer}}
.linechart.pinned .capture{{cursor:default !important}}
</style></head><body><div class="wrap">
<h1>CME FedWatch · 美联储加息概率追踪</h1>
<div class="sub">数据来源 CME QuikStrike FedWatch 工具（Aggregated View） · 最近快照 {latest_cn}（北京时间；官网盘中实时值会随期货价格变动）</div>

<div class="card">
  <h2>下一三次 FOMC 会议 · 最大概率加息水平</h2>
  {hero_grid}
  <div class="note" style="margin-top:6px">
  卡片显示截至 <b>{latest_cn}</b>，每个会议<b>当前市场预期概率最大的目标利率区间</b>，
  以及累计加息/维持/降息概率分布。
  </div>
</div>

<div class="card">
  <h2>「最大概率区间」概率走势（{len(meetings)} 条会议曲线）</h2>
  <div class="chart-container">
    <div class="chart-legend">{legend}</div>
    <div class="chart-controls">
      <div class="range-row">
        <span class="range-label">起始</span>
        <input type="range" class="range-start" min="0" max="0" value="0" step="1" aria-label="起始日期">
        <span class="range-val range-start-val">—</span>
      </div>
      <div class="range-row">
        <span class="range-label">结束</span>
        <input type="range" class="range-end" min="0" max="0" value="0" step="1" aria-label="结束日期">
        <span class="range-val range-end-val">—</span>
      </div>
      <span class="range-meta">共 <b class="range-count">—</b> 天</span>
      <button type="button" class="reset-range">重置</button>
    </div>
    {chart}
    <div class="chart-tooltip"></div>
  </div>
  <div class="note" style="margin-top:10px">
  · <b>图例可点</b>：单击会议标签切换该线显示/隐藏，可聚焦看 1 条或对比 2 条。<br>
  · <b>鼠标悬停</b>：十字线落在最近日期，浮窗显示该日每条可见线的
    <b>日期 + 最大概率目标区间（绝对值 + <code>+25bp</code> 相对标签）+ 该区间概率</b>。<br>
  · <b>日期筛选</b>：用上面两个滑块锁定起始/结束日期，可聚焦过去某个月或最近 60 天；
    X 轴刻度、十字线、浮窗都会自动对齐到所选区间。<br>
  · <b>FOMC 会议日</b>：图中的灰色垂直虚线与顶部 <b>FOMC</b> 小标签表示会议日期；
    日期不在当前筛选范围内时自动隐藏。<br>
  · <b>重大变动标注</b>：橙/蓝色光环 + 实心点 = 当日某会议 max 概率日环比
    <b>绝对值 ≥ 8pp</b>，hover 弹窗会给出事件摘要与新闻链接；
    橙色=图上线值上涨（hawk）、蓝色=图上线值下降（dove）。<br>
  · <b>悬停浮窗</b>：浮窗停靠在折线图<b>右侧留白</b>处，与绘图区零重叠、
    <b>不会遮挡任何数据点</b>；位置固定不跟随鼠标。<br>
  · <b>📌 点击固定（click to pin）</b>：鼠标移上去浮窗出现后，<b>点一下</b>即可把浮窗
    <b>钉住</b>（出现琥珀色描边与「已固定」提示），此时鼠标可从容移到浮窗上点
    「🔗 查看来源」；<b>再点一下</b>（图上或浮窗内任意处）即取消固定，回到悬停跟随模式。<br>
  · <b>曲线变化来源</b>：① 该区间概率本身涨跌；② 不同日期「最大概率区间」标签切换
    （如从 <code>+25bp</code> 切到 <code>+50bp</code>）。标签的视觉跳变对应市场对累计加息幅度的预期重定价。
  </div>
</div>

<div class="card">
  <h2>所有会议全区间概率热力图（最新快照）</h2>
  {grid}
  <div class="note" style="margin-top:10px">颜色饱和度 = 概率大小。
  红色 = 高于当前区间、灰色 = 当前区间、绿色 = 低于当前区间。数据直接取自本站所标注时刻的 QuikStrike Aggregated 表格；官网盘中值随后可能变化。</div>
</div>

<div class="card">
  <h2>说明</h2>
  <div class="note">
  · <b>数据源</b>：CME QuikStrike FedWatch 工具的 Aggregated View / Historical Downloads
    （<code>cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html</code>）。
    通过浏览器自动化直接读取 QuikStrike Aggregated 表格，不再自行推导概率；比较时请以本站标注的采集时刻为准。<br>
  · <b>采集方式</b>：用 agent-browser 打开 QuikStrike → 点击 Aggregated tab → 从 DOM 表格抽取
    概率列；同时通过 <i>Downloads 面板</i> 的 "All upcoming meetings" CSV 一次性回填 1 年历史。<br>
  · <b>采集节奏</b>：每日北京时间 <b>10:00</b> 触发；脚本内建 10 分钟 × 3 次重试
    （<b>10:00 → 10:10 → 10:20</b>），任一次成功即停止；10:10 / 10:20 仅为失败重试。
    官网是盘中实时页面，因此用户稍后打开官网时的概率可能高于或低于本站快照。<br>
  · <b>交易日归档</b>：周末若 QuikStrike 仍返回上一交易日行情，折线按最近工作日归档；
    原始实际抓取时刻保留在 <code>data/snapshots/YYYY-MM-DD.json</code>。<br>
  · <b>历史回填</b>：<code>backfill_history.py</code> 把 QuikStrike Downloads 面板里
    "All upcoming meetings" 的 CSV 拉下来一次性回填（约 <b>251 个交易日，1 年</b>）；
    这些历史行是官方按交易日提供的日期级历史值，<code>10:00:00</code> 是统一标签，
    不是盘中实时更新，也不替代当天的 10:00 定时快照。<br>
  · <b>口径</b>：<b>最大概率区间</b>指 QuikStrike Aggregated/History 表中该会议
    所有目标区间概率最高的那一档；折线追踪这一档的概率变化。当区间标签切换时
    （如 <code>+25bp</code> → <code>+50bp</code>），线不会出现人为断点，而是直接连到
    新最大区间的概率位置。<br>
  · <b>历史与实时共一张图</b>：存档从 <b>{first_snap[:10]}</b> 起累积到 <b>{latest_cn[:10]}</b>；
    共 <b>{n_snap}</b> 个采集日、<b>{n_total_rows}</b> 行。远期会议在尚未产生概率时会有全 0 占位行，
    这些占位行不绘图；每条折线从该会议首次出现有效概率的日期开始，并随每天 10:00 自动累计。<br>
  · <b>文件位置</b>：长表 CSV <code>data/fedwatch_probabilities.csv</code>；
    原始历史 CSV <code>data/history/all_meetings_*.csv</code>；
    每日完整快照（含 ZQ 价格）<code>data/snapshots/YYYY-MM-DD.json</code>；
    看板 <code>report/index.html</code>。
  </div>
</div>
<script>
(function(){{
  function bpsLabel(rng){{
    var m = /^[0-9]+/.exec(rng);
    if (!m) return rng;
    var delta = (parseInt(m[0]) - 350) / 25;
    if (delta === 0) return '维持';
    if (delta > 0) return '+' + (delta * 25) + 'bp';
    return (delta * 25) + 'bp';
  }}
  function prettyRange(rng){{
    var p = rng.split('-').map(function(x){{ return (parseInt(x)/100).toFixed(2) + '%'; }});
    return p[0] + ' - ' + p[1];
  }}
  function fmtDate(snap){{ return snap.substring(0, 10); }}
  function escHtml(s){{
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }}
  var DIR_COLOR = {{ hawk: '#fb923c', dove: '#60a5fa', neutral: '#9ca3af' }};
  var DIR_LABEL = {{ hawk: '图上线值上涨', dove: '图上线值下降', neutral: '中性事件' }};
  var DIR_ICON  = {{ hawk: '🔺', dove: '🔻', neutral: '🔘' }};

  function initOne(container){{
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
    var series = data.series;
    var allx = data.allx;
    var n = allx.length;
    var xIndex = {{}};
    allx.forEach(function(x, i){{ xIndex[x] = i; }});
    series.forEach(function(s){{
      s.pointsByX = {{}};
      s.points.forEach(function(pt){{ s.pointsByX[pt.x] = pt; }});
    }});
    var pad_l = +svg.dataset.padL, pw = +svg.dataset.pw;
    var pad_t = +svg.dataset.padT, ph = +svg.dataset.ph;
    var lo = +svg.dataset.lo, hi = +svg.dataset.hi;
    var meetingLayer = svg.querySelector('.meeting-layer');
    var VBW = data.viewBox_w;

    var currentStart = 0;
    var currentEnd = n - 1;
    var active = {{}};
    series.forEach(function(s){{ active[s.md] = true; }});

    function paint(startIdx, endIdx){{
      startIdx = Math.max(0, Math.min(n - 2, startIdx | 0));
      endIdx = Math.max(startIdx + 1, Math.min(n - 1, endIdx | 0));
      currentStart = startIdx;
      currentEnd = endIdx;
      var filteredN = endIdx - startIdx + 1;

      function px(iRel){{ return pad_l + (pw * iRel / Math.max(1, filteredN - 1)); }}
      function py(v){{ return pad_t + ph * (1 - (v - lo) / (hi - lo)); }}

      // 清掉旧的 X tick 文本（Y 网格与 Y 轴标签保留）
      svg.querySelectorAll('text.xtick').forEach(function(el){{ el.remove(); }});

      // 重画 X tick 文本（均分 6 段，最多 6 个）
      var nTicks = Math.min(6, filteredN);
      for (var k = 0; k < nTicks; k++){{
        var rel = nTicks === 1 ? 0 : (k * (filteredN - 1) / (nTicks - 1));
        var i = startIdx + Math.round(rel);
        var xpos = px(i - startIdx);
        var date = allx[i];
        svg.insertAdjacentHTML('beforeend',
          '<text class="xtick" x="' + xpos.toFixed(1) + '" y="' + (pad_t + ph + 22) + '" '
          + 'font-size="11" fill="#6b7280" text-anchor="middle">'
          + date.substring(5, 10).replace('-', '/') + '</text>'
          + '<text class="xtick" x="' + xpos.toFixed(1) + '" y="' + (pad_t + ph + 38) + '" '
          + 'font-size="9.5" fill="#9ca3af" text-anchor="middle">'
          + date.substring(11, 16) + '</text>'
        );
      }}

      // FOMC 会议日：中性垂直虚线 + 顶部只显示「FOMC」小标签。
      // 会议标记独立于事件光环，不使用 hawk/dove 颜色，也不进入 tooltip。
      if (meetingLayer) {{
        meetingLayer.innerHTML = '';
        var fomcDates = data.fomc_dates || [];
        fomcDates.forEach(function(meetingDate){{
          var meetingIdx = -1;
          for (var mi = startIdx; mi <= endIdx; mi++) {{
            if (allx[mi].substring(0, 10) === meetingDate) {{
              meetingIdx = mi;
              break;
            }}
          }}
          if (meetingIdx < 0) return;
          var meetingX = px(meetingIdx - startIdx);
          meetingLayer.insertAdjacentHTML('beforeend',
            '<line class="meeting-guide" x1="' + meetingX.toFixed(1) + '" y1="' + pad_t
            + '" x2="' + meetingX.toFixed(1) + '" y2="' + (pad_t + ph) + '"/>'
            + '<g class="meeting-label" transform="translate(' + meetingX.toFixed(1) + ',0)" aria-label="FOMC">'
            + '<rect x="-20" y="' + (pad_t - 25) + '" width="40" height="18" rx="4"/>'
            + '<text x="0" y="' + (pad_t - 12) + '" text-anchor="middle">FOMC</text>'
            + '</g>'
          );
        }});
      }}

      // 重画每条 series 的 path + circle；线尾标签先收集，再统一纵向避让
      var endLabels = [];
      series.forEach(function(s){{
        var g = svg.querySelector('g.series[data-md="' + s.md + '"]');
        if (!g) return;
        g.innerHTML = '';
        var slice = s.points.filter(function(pt){{
          var idx = xIndex[pt.x];
          return idx !== undefined && idx >= startIdx && idx <= endIdx;
        }});
        if (slice.length === 0) return;
        var color = s.color;
        var pathD = slice.map(function(pt, j){{
          var xRel = xIndex[pt.x] - startIdx;
          return (j === 0 ? 'M' : 'L') + px(xRel).toFixed(1) + ',' + py(pt.v).toFixed(1);
        }}).join(' ');
        g.insertAdjacentHTML('beforeend',
          '<path d="' + pathD + '" fill="none" stroke="' + color + '" '
          + 'stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>'
        );
        slice.forEach(function(pt){{
          var cxStr = px(xIndex[pt.x] - startIdx).toFixed(1);
          var cyStr = py(pt.v).toFixed(1);
          var evsHere = data.events_by_snap[pt.x] || [];
          if (evsHere.length === 0) {{
            g.insertAdjacentHTML('beforeend',
              '<circle cx="' + cxStr + '" cy="' + cyStr + '" r="3.5" '
              + 'fill="#fff" stroke="' + color + '" stroke-width="2"/>'
            );
          }} else {{
            // 重大变动日：外圈光环 + 实心中心点（按方向上色）
            var dir = evsHere[0].direction;
            var halo = DIR_COLOR[dir] || '#fb923c';
            g.insertAdjacentHTML('beforeend',
              '<circle cx="' + cxStr + '" cy="' + cyStr + '" r="9" '
              + 'fill="none" stroke="' + halo + '" stroke-width="2" opacity="0.45" class="event-halo"/>'
              + '<circle cx="' + cxStr + '" cy="' + cyStr + '" r="4" '
              + 'fill="' + halo + '" stroke="#fff" stroke-width="2" class="event-dot"/>'
            );
          }}
        }});
        var last = slice[slice.length - 1];
        endLabels.push({{
          g: g, color: color, short: s.short,
          x: px(xIndex[last.x] - startIdx), y: py(last.v),
          rng: last.rng, v: last.v
        }});
      }});

      // 线尾标签避让：三条曲线概率接近时不要互相压住
      endLabels.sort(function(a, b){{ return a.y - b.y; }});
      var GAP = 38;
      var yTop = pad_t + 18, yBot = pad_t + ph + 4;
      for (var li = 1; li < endLabels.length; li++){{
        if (endLabels[li].y - endLabels[li - 1].y < GAP)
          endLabels[li].y = endLabels[li - 1].y + GAP;
      }}
      if (endLabels.length) {{
        var over = endLabels[endLabels.length - 1].y - yBot;
        if (over > 0) {{
          for (var lj = 0; lj < endLabels.length; lj++) endLabels[lj].y -= over;
        }}
      }}
      for (var lk = 0; lk < endLabels.length; lk++){{
        var L = endLabels[lk];
        if (L.y < yTop) L.y = yTop;
        L.g.insertAdjacentHTML('beforeend',
          '<g class="end-label" transform="translate(' + (L.x + 10).toFixed(1) + ',' + (L.y - 12).toFixed(1) + ')">'
          + '<rect x="0" y="-12" rx="4" width="180" height="34" fill="' + L.color + '" opacity="0.10"/>'
          + '<text x="8" y="2" font-size="11.5" font-weight="700" fill="' + L.color + '">'
          + L.short + ' 会议</text>'
          + '<text x="8" y="18" font-size="11" fill="#374151">'
          + prettyRange(L.rng) + '  →  ' + L.v.toFixed(2) + '%</text>'
          + '</g>'
        );
      }}

      // 更新滑块旁日期与天数
      if (startVal) startVal.textContent = fmtDate(allx[startIdx]);
      if (endVal) endVal.textContent = fmtDate(allx[endIdx]);
      if (countVal) countVal.textContent = filteredN;
    }}

    // 初始化滑块 max/value
    if (startSlider) {{
      startSlider.min = 0; startSlider.max = n - 1; startSlider.value = 0;
      startSlider.addEventListener('input', function(){{
        var s = +startSlider.value, e = +endSlider.value;
        if (s >= e) {{ s = e - 1; startSlider.value = s; }}
        paint(s, e);
        hideTip();
      }});
    }}
    if (endSlider) {{
      endSlider.min = 0; endSlider.max = n - 1; endSlider.value = n - 1;
      endSlider.addEventListener('input', function(){{
        var s = +startSlider.value, e = +endSlider.value;
        if (e <= s) {{ e = s + 1; endSlider.value = e; }}
        paint(s, e);
        hideTip();
      }});
    }}
    if (resetBtn) {{
      resetBtn.addEventListener('click', function(){{
        startSlider.value = 0;
        endSlider.value = n - 1;
        paint(0, n - 1);
        hideTip();
      }});
    }}

    // 图例切换
    legend.querySelectorAll('.legend-item').forEach(function(btn){{
      btn.addEventListener('click', function(){{
        var md = btn.dataset.md;
        active[md] = !active[md];
        btn.classList.toggle('off', !active[md]);
        svg.querySelectorAll('g.series').forEach(function(g){{
          if (g.dataset.md === md) g.classList.toggle('hidden', !active[md]);
        }});
        hideTip();   // 显隐变了，冻结的旧内容会误导 → 一并清掉
      }});
    }});

    // ---- hover tooltip v5 --------------------------------------------------
    // v4（已修）历史问题：
    //   ① hideTip() 被调用 4 次却从未定义 → 每次调用直接抛 ReferenceError；
    //   ② 浮窗位置绑定鼠标像素（mx+14）且每次 mousemove 重算 → 浮窗角始终
    //      与光标保持 14px，光标永远追不上；且向右追赶会改变十字线索引，
    //      浮窗继续被推走。于是浮窗内的链接天然点不到。
    //   修法：浮窗**停靠在绘图区右侧留白**（pad_r 区），位置只由图表几何决定、
    //   与鼠标坐标完全无关 ⇒ 不可能追逐，也不可能盖住任何数据点。
    //
    // v5（本次新增）click-to-pin：即便停靠后位置稳定，hover 语义下鼠标一旦移出
    //   图表 400ms 浮窗仍会消失，用户来不及把光标移上浮窗点「查看来源」。
    //   故加显式固定：hover 出浮窗 → 点一下 → 冻结（内容 + 十字线 + 不再自动隐藏），
    //   此时可从容移到浮窗上点链接；再点一下取消固定，交回 hover 逻辑。
    //   固定期间必须**忽略 mousemove**：否则内容跟着鼠标变，链接所指的事件
    //   就不再是用户看到的那条了。
    var cross = svg.querySelector('.crosshair');
    var capture = svg.querySelector('.capture');
    var tipHovered = false;
    var hideTimer = null;
    // click-to-pin：hover 出浮窗后点一下即冻结内容与位置，鼠标可从容移到浮窗上点
    // 「查看来源」；再点一下（图上或浮窗内均可）取消固定。固定期间忽略 mousemove，
    // 因为若内容跟着鼠标变，链接所指的事件就不再是用户看到的那条了。
    var pinned = false;

    function setPinned(on){{
      pinned = on;
      tip.classList.toggle('pinned', on);
      svg.classList.toggle('pinned', on);
    }}
    function togglePin(){{
      if (tip.style.display !== 'block') return;   // 没有浮窗可固定
      setPinned(!pinned);
      if (pinned) {{
        cancelHide();                              // 固定 → 永不自动隐藏
        cross.style.display = '';                  // 十字线一并钉住
      }} else if (!tipHovered) {{
        scheduleHide();                            // 取消固定 → 交回 hover 逻辑
      }}
    }}

    capture.addEventListener('mousemove', function(e){{
      if (pinned) return;                          // 已固定：内容与十字线都不动
      cancelHide();
      var rect = svg.getBoundingClientRect();
      var ratio = VBW / rect.width;
      var sx = (e.clientX - rect.left) * ratio;
      var filteredN = currentEnd - currentStart + 1;
      var iRel = Math.round((sx - pad_l) / pw * Math.max(1, filteredN - 1));
      var i = currentStart + iRel;
      if (i < currentStart || i > currentEnd) {{ hideTip(); return; }}
      var xp = pad_l + pw * (i - currentStart) / Math.max(1, filteredN - 1);
      cross.setAttribute('x1', xp); cross.setAttribute('x2', xp);
      cross.style.display = '';
      var snap = allx[i];
      var parts = ['<div class="tip-date">📅 ' + snap.substring(0, 16) + '</div>'];
      var any = false;
      series.forEach(function(s){{
        if (!active[s.md]) return;
        var pt = s.pointsByX[snap];
        if (!pt) return;
        var bps = bpsLabel(pt.rng);
        parts.push(
          '<div class="tip-row">'
          + '<div class="tip-row-top">'
          +   '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;flex:none;background:' + s.color + '"></span>'
          +   '<span class="tip-nm">' + s.short + ' 会议</span>'
          +   '<span class="tip-pv" style="color:' + s.color + '">' + pt.v.toFixed(2) + '%</span>'
          + '</div>'
          + '<div class="tip-row-bot">'
          +   '<span class="tip-rg">' + prettyRange(pt.rng) + '</span>'
          +   '<span class="tip-bp" style="background:' + s.color + '33;color:' + s.color + '">' + bps + '</span>'
          + '</div>'
          + '</div>'
        );
        any = true;
      }});
      if (!any) parts.push('<div class="tip-empty">所有会议已关闭，请点击图例开启</div>');
      // 重大变动日事件信号
      var evsSnap = data.events_by_snap[snap] || [];
      if (evsSnap.length > 0) {{
        var dir = evsSnap[0].direction;
        var dirColor = DIR_COLOR[dir] || '#fb923c';
        parts.push('<div class="tip-events-head ' + dir + '">⚠ 当日出现重大变动（≥8pp）：'
          + DIR_ICON[dir] + ' ' + escHtml(DIR_LABEL[dir] || '事件') + '</div>');
        evsSnap.forEach(function(ev) {{
          var evColor = DIR_COLOR[ev.direction] || '#fb923c';
          var arrowI = DIR_ICON[ev.direction] || '🔘';
          var evLab = DIR_LABEL[ev.direction] || '事件';
          parts.push(
            '<div class="tip-event">'
            // 「查看来源」直接跟在事件标题后面：即使正文很长需要滚动，链接也始终可见可点
            + '<div class="tip-event-head" style="color:' + evColor + '">' + arrowI + ' '
            + escHtml(evLab) + '：' + escHtml(ev.summary || '')
            + (ev.url ? ' <a class="tip-src" href="' + escHtml(ev.url)
                + '" target="_blank" rel="noopener">🔗 查看来源</a>' : '')
            + '</div>'
            + (ev.text ? '<div class="tip-event-body">' + escHtml(ev.text) + '</div>' : '')
            + '</div>'
          );
        }});
      }}
      // 固定提示条：始终在 DOM 里，由 .chart-tooltip.pinned 控制显隐
      parts.unshift('<div class="tip-pin">📌 已固定<span class="tip-pin-key">'
        + '点击图上或浮窗任意处取消</span></div>');
      tip.innerHTML = parts.join('');
      tip.style.display = 'block';
      positionTip();
    }});

    // 浮窗停靠在绘图区右侧留白（pad_r 区）里 —— 与折线图零重叠，不可能盖住任何数据点。
    // 位置完全由图表几何决定、与鼠标坐标无关 ⇒ 鼠标怎么移动浮窗都纹丝不动，
    // 可以直接把鼠标移到「查看来源」上点击，不存在追逐问题。
    function positionTip(){{
      var hr = container.getBoundingClientRect();
      var sr = svg.getBoundingClientRect();
      var svgLeft = sr.left - hr.left, svgTop = sr.top - hr.top;
      var scale = sr.width / VBW;
      var dockLeft = svgLeft + (pad_l + pw) * scale + 10;   // 紧贴绘图区右边界之外
      var dockW = svgLeft + sr.width - dockLeft - 4;        // 到 SVG 右边界为止
      tip.style.width = Math.max(150, Math.round(dockW)) + 'px';
      // 高度与竖直位置对齐绘图区上下沿，视觉上就像图表的侧栏
      tip.style.maxHeight = Math.round(ph * scale) + 'px';
      tip.style.left = Math.round(dockLeft) + 'px';
      tip.style.top = Math.round(svgTop + pad_t * scale) + 'px';
    }}

    // 状态机 + 400ms 延迟 + 隐藏前兜底校验
    function doHide(){{
      hideTimer = null;
      tip.style.display = 'none';
      cross.style.display = 'none';
    }}
    function cancelHide(){{ if (hideTimer){{ clearTimeout(hideTimer); hideTimer = null; }} }}
    // 供滑块 / 重置 / 越界分支调用（此前该函数未定义，4 处调用均直接抛 ReferenceError）
    function hideTip(){{ cancelHide(); tipHovered = false; setPinned(false); doHide(); }}
    function scheduleHide(){{
      if (pinned) return;                          // 固定期间不因鼠标移出而消失
      if (hideTimer) clearTimeout(hideTimer);
      hideTimer = setTimeout(function(){{
        if (tipHovered) {{ hideTimer = null; return; }}   // 兜底：定时器到期时鼠标在浮窗内
        doHide();
      }}, 400);
    }}
    capture.addEventListener('mousemove', cancelHide);
    capture.addEventListener('click', togglePin);          // 图上点一下 → 固定 / 取消固定
    container.addEventListener('mouseleave', scheduleHide);
    tip.addEventListener('mouseenter', function(){{ tipHovered = true; cancelHide(); }});
    tip.addEventListener('mouseleave', function(){{ tipHovered = false; scheduleHide(); }});
    // 浮窗内点一下也可取消固定；但点「查看来源」链接时不切换（否则会挡住新标签页打开）
    tip.addEventListener('click', function(e){{
      if (e.target && e.target.closest && e.target.closest('a')) return;
      togglePin();
    }});

    paint(0, n - 1);
  }}

  function boot(){{
    document.querySelectorAll('.chart-container').forEach(initOne);
  }}
  if (document.readyState === 'loading') {{
    document.addEventListener('DOMContentLoaded', boot);
  }} else {{
    boot();
  }}
}})();
</script>
</body></html>"""

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    html, site_paths = build_static_site(html, rows)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(html)
    validate_site(site_paths)
    print(
        f"站点已生成: {OUT}  (最新 {latest_cn}, 共 {n_snap} 个采集日, "
        f"{len(meetings)} 条曲线, {len(site_paths)} 个可索引页面)"
    )


if __name__ == "__main__":
    main()
