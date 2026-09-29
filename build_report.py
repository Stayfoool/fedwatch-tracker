#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_report.py — 读取 data/fedwatch_probabilities.csv，
生成自包含 HTML 看板 report/index.html。

核心图表：未来三次 FOMC 会议「最大概率加息水平」随时间变化折线图。

横轴口径（2026-09-17 起）：**美东交易日**，取值来自 `us_trade_date` 列。
一个点 = 美东一个交易日的收盘定格（详见 time_axis.py）。副标签写明该点的口径：
「16:2xCT」（收盘后休市间隙读到的定格值）/「结算」（历史回填的结算值）/「盘中」（旧实时盘中读数）。
`snapshot_cn` 仍是排序键与事件关联键，不用于显示。

数据列：
  snapshot_cn         — 北京采集时刻（排序键 + 事件关联键；不显示）
  us_trade_date       — 该数据对应的美东交易日（**横轴用这一列**）
  data_asof_ct        — 页面自带的 "Data as of" 数据时点（规范化后）
  time_basis          — 口径来源：page_asof / settlement_export / legacy_intraday
  snapshot_quikstrike — 页面 "Data as of" 原文（含 "* Data as of ..."）
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
from snapshot_pick import pick_latest_snapshot
from snapshot_view import displayed_by_day, remap_events_to_points, us_day_of
from time_axis import asof_label, parse_page_asof
from rate_baseline import (
    FOMC_DECISION_DATES,
    bucket_stats,
    derive_target_eras,
    discover_range_columns,
    era_target_for,
)
from dual_charts import dual_charts_block

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE_DIR, "data", "fedwatch_probabilities.csv")
OUT = os.path.join(BASE_DIR, "report", "index.html")

# 默认关注接下来 3 次会议；会自动按 meeting_date 升序取最新快照里最早的 3 个
DEFAULT_NEXT_N = 3

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

    去重（A 方案）：横轴一格 = 一个美东交易日，由 snapshot_view.displayed_by_day
    统一裁决「当天那个点」（收盘定格 > 结算导出 > 盘中读数）。
    **检测端（analyze_changes.py）用同一份规则**，两边不允许各写一套 ——
    否则会出现「检测说这天有变动、图上却找不到那个点」。
    """
    winners = displayed_by_day(rows)
    d = defaultdict(list)
    for r in rows:
        meeting = (r.get("meeting_date") or "").strip()
        snapshot = (r.get("snapshot_cn") or "").strip()
        if not meeting or meeting not in meetings:
            continue
        if winners.get(us_day_of(r)) != snapshot:
            continue                      # 这一天没轮到它出图
        label = (r.get("max_range_label") or "").strip()
        raw_pct = (r.get("max_range_pct") or "").strip()
        if not label or not raw_pct:
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


def bps_axis_label(rng: str, base_lo: int) -> str:
    """如 '375-400' + 基准 350 → '+25bp'；等于基准 → '维持'。"""
    try:
        lo = int(rng.split("-")[0])
    except Exception:
        return rng
    delta = (lo - base_lo) // 25
    if delta == 0:
        return "维持"
    if delta > 0:
        return f"+{delta*25}bp"
    return f"{delta*25}bp"


def bucket_class(label: str, target: str) -> str:
    """档位相对目标区间的类别：hike / hold / cut（共识色带着色用）。"""
    try:
        lo, hi = (int(x) for x in label.split("-"))
        tl, th = (int(x) for x in target.split("-"))
    except (ValueError, AttributeError):
        return "hold"
    if lo >= th:
        return "hike"
    if hi <= tl:
        return "cut"
    return "hold"


def build_xmeta(rows) -> dict:
    """{snapshot_cn: {"d": 美东交易日, "t": 口径副标签}} —— 横轴显示的唯一来源。

    `d` 取 `us_trade_date`（缺失时退回原标签日期，保证老数据也能渲染）；
    `t` 由 time_axis.asof_label 给出，让读者一眼看出这个点是「收盘定格 / 结算 / 盘中」。
    """
    out = {}
    for r in rows:
        s = (r.get("snapshot_cn") or "").strip()
        if not s or s in out:
            continue
        out[s] = {
            "d": (r.get("us_trade_date") or "").strip() or s[:10],
            "t": asof_label(r),
        }
    return out


def line_chart_max_pct(series_map, events_by_snap=None, width=920, height=380, today_label=None,
                       data_meeting_dates=(), xmeta=None, cur_base="350-375", bands=None):
    """3 条会议曲线 + 数据点；可点击图例、可双滑块筛选日期范围、hover 显示 tooltip。

    X 轴刻度、折线 path/circle/end-label 都由 JS paint() 重画，
    Python 这边只输出静态骨架（Y 网格 + 3 个空 g.series + 十字线 + 命中层）。

    xmeta: {snapshot_cn: {"d": 美东交易日, "t": 副标签}} —— 横轴与 tooltip 的显示来源。
           `allx` 仍是 snapshot_cn（排序键），但**标签一律走 xmeta**。
    events_by_snap: 由 load_events() 得到的 {snapshot_cn: [{direction, summary, text, url}, ...]}
                    —— paint() 据此给「重大变动日」数据点加光环、tooltip 增加事件信号。
    data_meeting_dates: 数据里实际存在的会议日期。与 FOMC_DECISION_DATES 取并集后作为
                    图上会议标记，这样 FedWatch 天窗延伸到 2028 年及以后时，无需改代码
                    就有标记（硬编码列表只用于补数据未覆盖的历史日期）。
    """
    # 2026-09-29 三次调整：pad_r 12→190，与「方向与幅度」双图对齐 —— 线尾标签
    # 重新画回右侧留白带（此前画在绘图区内侧右缘，会压住最右侧的数据点）；
    # 悬停浮窗仍锚定绘图区右下角内侧（几何固定、不追鼠标，见 positionTip 注释）。
    pad_l, pad_r, pad_t, pad_b = 50, 190, 32, 60
    allx = sorted({x for v in series_map.values() for x, _, _ in v})
    if not allx:
        return f'<svg width="{width}" height="60"></svg>'
    lo, hi = 0.0, 100.0
    pw, ph = width - pad_l - pad_r, height - pad_t - pad_b

    xmeta = xmeta or {}
    chart_data = {
        "allx": allx,
        "n": len(allx),
        "xmeta": xmeta,
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
        "fomc_dates": sorted(set(FOMC_DECISION_DATES) | set(data_meeting_dates)),
        "cur_base": cur_base,
        "bands": bands or [],
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
        f'style="display:block;font-family:-apple-system,system-ui,sans-serif" '
        f'data-pad-l="{pad_l}" data-pw="{pw}" data-pad-t="{pad_t}" data-ph="{ph}" '
        f'data-n-x="{len(allx)}" data-lo="{lo}" data-hi="{hi}">',
        # 共识色带层：垫在 Y 网格线之下，JS paint() 按日期筛选重画
        '<g class="band-layer" aria-hidden="true"></g>',
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


def legend_html(meetings, focus=None):
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
    if focus:
        parts.append(
            '<span class="chart-key" style="gap:4px">'
            '<span style="display:inline-flex;gap:2px;align-items:center">'
            '<span style="width:9px;height:9px;background:#fee2e2;border:1px solid #fca5a5;border-radius:2px"></span>'
            '<span style="width:9px;height:9px;background:#f1f5f9;border:1px solid #cbd5e1;border-radius:2px"></span>'
            '<span style="width:9px;height:9px;background:#dcfce7;border:1px solid #86efac;border-radius:2px"></span>'
            '</span>'
            f'背景＝{focus[5:]} 共识档（加/持/降）</span>'
        )
    parts.append(
        '<span class="chart-key chart-key-meeting" aria-label="FOMC 会议日标记">'
        '<span class="chart-key-line" aria-hidden="true"></span>FOMC'
        '</span>'
    )
    return "".join(parts)


def hero_card(md: str, max_rng: str, max_pct: float, hike: float, hold: float, cut: float,
              color: str, base_lo: int):
    """3 个大卡片之一：当前最大概率区间 + 数值 + 累计加减息概率。"""
    delta_text = bps_axis_label(max_rng, base_lo)
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


def heatmap_table(latest_rows, base_lo: int = 350):
    """Aggregated 全会议网格表（区间 × 会议）。颜色以 base_lo（现行目标下沿）分界。"""
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
            base = base_lo
            try:
                lo = int(rng.split("-")[0])
            except Exception:
                lo = base
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

    # 取最新一次采集，仅在疑似「半截采集」时回退（详见 snapshot_pick.py）
    by_snap = defaultdict(list)
    for r in rows:
        by_snap[r["snapshot_cn"]].append(r)
    latest_cn = pick_latest_snapshot(by_snap)
    newest_cn = max(by_snap)
    if latest_cn != newest_cn:
        print(f"WARN 最新采集 {newest_cn} 仅 {len(by_snap[newest_cn])} 个会议，"
              f"疑似半截采集；已回退使用 {latest_cn}（{len(by_snap[latest_cn])} 个会议）")
    latest = by_snap[latest_cn]
    # 会议数变化是「会议到期 / FedWatch 天窗伸缩」的信号，留在每日日志里便于回溯
    prev_keys = [k for k in sorted(by_snap) if k < latest_cn]
    prev_note = (f"（上一次采集 {prev_keys[-1]} 为 {len(by_snap[prev_keys[-1]])} 个会议）"
                 if prev_keys else "")
    print(f"快照 {latest_cn}：{len(latest)} 个会议{prev_note}")
    meetings = next_n_meetings(latest)
    # 现行目标区间：动态推导（current_target 列在 9/16 加息后未更新过，不再采信）
    range_cols = discover_range_columns(rows)
    latest_us_day = (latest[0].get("us_trade_date") or latest_cn[:10]).strip()
    fallback = next(((r.get("current_target") or "").strip() for r in reversed(rows)
                     if (r.get("current_target") or "").strip()), "350-375")
    eras = derive_target_eras(rows, range_cols, latest_us_day)
    cur_base = era_target_for(latest_us_day, eras, fallback)
    base_lo = int(cur_base.split("-")[0])
    if eras:
        print("现行目标区间:", cur_base, "| 时代切换:",
              "; ".join(f"{d} → {t}" for d, t in eras))
    series = series_max_pct(rows, meetings)

    # 3 个 hero 数据（加息/维持/降息按现行基准从桶分布现算，不读 agg 列）
    hero_data = []
    for idx, m in enumerate(meetings):
        rec = next((r for r in latest if r["meeting_date"] == m), None)
        if not rec:
            continue
        st = bucket_stats(rec, range_cols, cur_base) or (0.0, 0.0, 0.0, 0.0, "", 0.0)
        hero_data.append({
            "md": m,
            "color": meeting_color(m, idx),
            "max_rng": rec.get("max_range_label", ""),
            "max_pct": float(rec.get("max_range_pct") or 0),
            "hike": st[0],
            "hold": st[1],
            "cut": st[2],
        })

    n_snap = len({r["snapshot_cn"] for r in rows})
    first_snap = min(r["snapshot_cn"] for r in rows)
    n_total_rows = len(rows)
    xmeta = build_xmeta(rows)
    latest_us = xmeta.get(latest_cn, {}).get("d", latest_cn[:10])
    latest_note = xmeta.get(latest_cn, {}).get("t", "")
    first_us = xmeta.get(first_snap, {}).get("d", first_snap[:10])
    latest_desc = (f"美东 {latest_us} 收盘后（{latest_note}）" if latest_note not in ("", "盘中")
                   else f"美东 {latest_us}（{latest_note}）")

    hero_html = "".join(
        hero_card(h["md"], h["max_rng"], h["max_pct"],
                  h["hike"], h["hold"], h["cut"], h["color"], base_lo)
        for h in hero_data)
    hero_grid = (
        '<div style="display:grid;grid-template-columns:repeat(3,1fr);'
        'gap:14px;margin-bottom:18px">'
        + hero_html + '</div>')

    # 「方向与幅度」双图（与 /curves 独立页共用 dual_charts 组件）
    fomc_dates = sorted(set(FOMC_DECISION_DATES) | {r["meeting_date"] for r in rows})
    dual_html, _dual_ctx = dual_charts_block(rows, meetings, xmeta, fomc_dates)

    # 共识色带：焦点会议（最近一场未开完的 FOMC）每天的最大概率档位，
    # 按时代基准分 hike/hold/cut 三色，相邻同档合并；色带边界 = 换档日
    focus = meetings[0] if meetings else None
    focus_md = focus[5:] if focus else ""
    bands = []
    if focus:
        for snap, label, _pct in series.get(focus, []):
            day = xmeta.get(snap, {}).get("d") or snap[:10]
            cls = bucket_class(label, era_target_for(day, eras, fallback))
            if bands and bands[-1]["rng"] == label:
                continue
            bands.append({"x": snap, "rng": label, "cls": cls})
        if len(bands) > 1:
            print(f"共识色带（{focus_md}）: {len(bands)} 段")

    # 归因按美东交易日绑定到「当天那个点」：即便早晨的收盘定格顶掉了前一天的盘中读数，
    # 光环与浮窗也不会挂空（2026-09-17 事故的直接修复）。
    chart = line_chart_max_pct(series,
                                events_by_snap=remap_events_to_points(load_events(), rows, series),
                                width=952, height=380, today_label=latest_cn,
                                data_meeting_dates={r["meeting_date"] for r in rows},
                                xmeta=xmeta, cur_base=cur_base, bands=bands)
    legend = legend_html(meetings, focus=focus)
    grid = heatmap_table(latest, base_lo)

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
<div class="sub">数据来源 CME QuikStrike FedWatch 工具（Aggregated View） · 横轴＝<b>美东交易日</b>（一个点 = 该交易日收盘定格） · 最新数据点 {latest_desc}</div>
<div class="sub" style="margin-bottom:16px"><a href="curves.html" style="color:#1d4ed8;font-weight:600;text-decoration:none">方向与幅度双图（累计加息概率 · 预期变动 bp）→</a>
　口径恒定的互补视图：P(加息) 由低到高的完整过程 + 预期落点幅度，无「最大概率区间」切换跳变</div>

<div class="card">
  <h2>下一三次 FOMC 会议 · 最大概率加息水平</h2>
  {hero_grid}
  <div class="note" style="margin-top:6px">
  卡片显示截至 <b>{latest_desc}</b>，每个会议<b>当前市场预期概率最大的目标利率区间</b>，
  以及累计加息/维持/降息概率分布。
  </div>
</div>

{dual_html}

<div class="card">
  <h2>市场共识档位与共识强度（{len(meetings)} 条会议曲线）</h2>
  <div style="font-size:12.5px;color:#6b7280;margin:0 0 12px">
  每条线 = 该会议<b>当前共识档位</b>（最大概率区间）的发生概率，即市场对其最集中押注结果的把握度。
  <b>线下跌 ≠ 转鸽</b>——持稳共识瓦解、概率流向加息档时线同样下跌，宏观方向请看上方主图 A / 主图 B。
  换档处折线断开（前后概率属于不同档位，不可比）；背景色 = 最近会议（{focus_md}）的共识档位：红=加息、灰=持稳、绿=降息。
  </div>
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
  · <b>重大变动标注</b>：橙/蓝色光环 + 实心点 = 当日某会议共识档概率日环比
    <b>绝对值 ≥ 8pp</b>，hover 弹窗会给出事件摘要与新闻链接；
    橙色=当日共识档概率上涨、蓝色=下降。<b>注意：这只是线值方向，不是鹰/鸽结论</b>——
    宏观方向请看上方主图 A（P(加息)）与主图 B（预期 bp）。<br>
  · <b>悬停浮窗</b>：浮窗停靠在绘图区<b>右下角</b>（近期读数集中在图中上部，右下角遮挡最少）；
    位置固定不跟随鼠标，鼠标移出后自动消失。<br>
  · <b>📌 点击固定（click to pin）</b>：鼠标移上去浮窗出现后，<b>点一下</b>即可把浮窗
    <b>钉住</b>（出现琥珀色描边与「已固定」提示），此时鼠标可从容移到浮窗上点
    「🔗 查看来源」；<b>再点一下</b>（图上或浮窗内任意处）即取消固定，回到悬停跟随模式。<br>
  · <b>读线规则</b>：线的涨跌只反映「当前共识档位的把握度」变化；换档日
    （该会议最大概率区间切换，如 <code>+25bp</code> 档 → <code>+50bp</code> 档）折线<b>断开</b>，
    前后两个概率属于不同档位、不可当涨跌比较——换档本身就是重要的重定价信息，
    体现在色带边界与断口处。
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
  · <b>采集节奏</b>：每日北京时间 <b>05:30</b> 与 <b>06:30</b> 各触发一次
    （美国夏令时用 05:30、冬令时用 06:30，折算到芝加哥时间都是<b>前一日 16:30 CT</b>，
    即 CME 收盘后的休市间隙）。不在窗口内的那次不会启动浏览器、也不会落盘。
    失败则间隔 10 分钟重试，共 3 次（16:30 → 16:40 → 16:50 CT，均在窗口内）。<br>
  · <b>时间口径</b>：<b>一个点 = 美东一个交易日的收盘定格值</b>。横轴标的是美东交易日，
    副标签写明该点的口径：<code>16:2xCT</code>（收盘后读到）/ <code>结算</code>（历史回填的结算值）/
    <code>盘中</code>（早期北京 10:00 采集的盘中读数）。
    因北京无夏令时而美东有，采集点会比北京时间早一天出现在横轴上（北京 09-18 采集 → 横轴 09-17），
    这是对的，不是数据延迟。官网是盘中实时页面，因此稍后打开官网看到的概率可能高于或低于本站快照。<br>
  · <b>周末去重（A 方案）</b>：CME 周五 16:00 CT 收盘后要到周日 17:00 CT 才重开，
    因此周六/周日/周一北京早上的三次采集读到的是<b>同一份周五收盘定格</b>。
    本站按「一个交易日一个点」去重，<b>保留最早那一次</b>（周六那次），每周固定 5 个点。
    每个快照的采集时钟都记在 <code>data/snapshots/YYYY-MM-DD.json</code>。<br>
  · <b>历史回填</b>：<code>backfill_history.py</code> 把 QuikStrike Downloads 面板里
    "All upcoming meetings" 的 CSV 拉下来一次性回填（约 <b>251 个交易日，1 年</b>）；
    这些历史行是官方按交易日提供的<b>日期级结算值</b>，<code>snapshot_cn</code> 里的
    <code>10:00:00</code> 只是当时的统一标签（副标签显示为「结算」），不代表时刻。<br>
  · <b>口径</b>：<b>最大概率区间</b>指 QuikStrike Aggregated/History 表中该会议
    所有目标区间概率最高的那一档；折线追踪这一档的概率变化。当区间标签切换时
    （如 <code>+25bp</code> → <code>+50bp</code>），线不会出现人为断点，而是直接连到
    新最大区间的概率位置。<br>
  · <b>现行目标区间（加息/维持/降息的基准）</b>：取最近一次已开完 FOMC 决议后的目标，
    由本站从该会议决议日最终读数的众数桶自动推导（当前：<b>{cur_base}</b>）。
    卡片的累计加息/维持/降息、<code>+Xbp</code> 相对标签、热力图红/灰/绿分界均以它为准。
    CSV 的 <code>current_target</code> 列自 2026-09-16 加息后未随决议更新，展示端不再采用；
    更完整的方向/幅度双图见 <a href="curves.html" style="color:#1d4ed8">方向与幅度页</a>。<br>
  · <b>历史与实时共一张图</b>：存档从美东交易日 <b>{first_us}</b> 起累积到 <b>{latest_us}</b>；
    共 <b>{n_snap}</b> 个采集日、<b>{n_total_rows}</b> 行。远期会议在尚未产生概率时会有全 0 占位行，
    这些占位行不绘图；每条折线从该会议首次出现有效概率的日期开始，并随每个美东交易日自动累计。<br>
  · <b>文件位置</b>：长表 CSV <code>data/fedwatch_probabilities.csv</code>；
    原始历史 CSV <code>data/history/all_meetings_*.csv</code>；
    每日完整快照（含 ZQ 价格）<code>data/snapshots/YYYY-MM-DD.json</code>；
    看板 <code>report/index.html</code>。
  </div>
</div>
<script>
(function(){{
  function bpsLabel(rng, baseLo){{
    var m = /^[0-9]+/.exec(rng);
    if (!m) return rng;
    var delta = (parseInt(m[0], 10) - baseLo) / 25;
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
  var DIR_LABEL = {{ hawk: '当日共识档概率上涨', dove: '当日共识档概率下降', neutral: '中性事件' }};
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
    // 相对 bp 标签的基准：现行目标区间下沿（后端按 FOMC 决议动态推导，见 rate_baseline.py）
    var baseLo = parseInt((data.cur_base || '350-375').split('-')[0], 10) || 350;
    var xmeta = data.xmeta || {{}};
    // 横轴显示：日期取美东交易日，副标签取该点的口径（16:2xCT / 结算 / 盘中）
    function xDate(snap){{ var m = xmeta[snap]; return (m && m.d) || snap.substring(0, 10); }}
    function xNote(snap){{ var m = xmeta[snap]; return (m && m.t) || snap.substring(11, 16); }}
    function xTip(snap){{
      var d = xDate(snap), t = xNote(snap);
      if (t === '结算') return d + '（结算值）';
      if (t === '盘中') return d + '（盘中读数）';
      return d + ' 收盘后 ' + t;
    }}

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
        // 贴边的刻度改单侧锚定，避免文字溢出 SVG 边界被裁切
        var anchor = 'middle', tx = xpos;
        if (k === 0 && xpos < 34) {{ anchor = 'start'; tx = 2; }}
        if (k === nTicks - 1 && xpos > VBW - 34) {{ anchor = 'end'; tx = VBW - 2; }}
        var date = allx[i];
        svg.insertAdjacentHTML('beforeend',
          '<text class="xtick" x="' + tx.toFixed(1) + '" y="' + (pad_t + ph + 22) + '" '
          + 'font-size="11" fill="#6b7280" text-anchor="' + anchor + '">'
          + xDate(date).substring(5, 10).replace('-', '/') + '</text>'
          + '<text class="xtick" x="' + tx.toFixed(1) + '" y="' + (pad_t + ph + 38) + '" '
          + 'font-size="9.5" fill="#9ca3af" text-anchor="' + anchor + '">'
          + xNote(date) + '</text>'
        );
      }}

      // 共识色带：背景 = 焦点会议（最近一场未开完的 FOMC）当天的最大概率档位，
      // 后端按时代基准分 hike/hold/cut 三色；色带边界 = 换档日，配琥珀细虚线。
      // 方向类别变化且足够宽的色带才放文字标签，避免 5-6 月档位拉锯期文字叠成一团。
      var bandLayer = svg.querySelector('.band-layer');
      if (bandLayer) {{
        bandLayer.innerHTML = '';
        var bands = data.bands || [];
        var infos = [];
        bands.forEach(function(b){{
          var bi2 = xIndex[b.x];
          if (bi2 !== undefined) infos.push({{ idx: bi2, cls: b.cls, rng: b.rng }});
        }});
        var stepW = pw / Math.max(1, filteredN - 1);
        for (var bk = 0; bk < infos.length; bk++) {{
          var B = infos[bk];
          if (B.idx > endIdx) break;
          var s0 = Math.max(B.idx, startIdx);
          var e0 = (bk + 1 < infos.length) ? Math.min(infos[bk + 1].idx - 1, endIdx) : endIdx;
          if (e0 < s0) continue;
          var bx1 = px(s0 - startIdx);
          var bx2 = px(e0 - startIdx) + stepW;
          var fill = B.cls === 'hike' ? '#fee2e2' : (B.cls === 'cut' ? '#dcfce7' : '#f1f5f9');
          bandLayer.insertAdjacentHTML('beforeend',
            '<rect x="' + bx1.toFixed(1) + '" y="' + pad_t + '" width="' + (bx2 - bx1).toFixed(1)
            + '" height="' + ph + '" fill="' + fill + '"/>');
          if (bk > 0 && B.idx >= startIdx && B.idx <= endIdx) {{
            bandLayer.insertAdjacentHTML('beforeend',
              '<line x1="' + bx1.toFixed(1) + '" y1="' + pad_t + '" x2="' + bx1.toFixed(1)
              + '" y2="' + (pad_t + ph) + '" stroke="#d97706" stroke-width="1" '
              + 'stroke-dasharray="2 3" opacity="0.45"/>');
            var bandLen = ((bk + 1 < infos.length) ? infos[bk + 1].idx : n) - B.idx;
            if (infos[bk - 1].cls !== B.cls && bandLen >= 6 && bx2 - bx1 > 60) {{
              bandLayer.insertAdjacentHTML('beforeend',
                '<text x="' + (bx1 + 3).toFixed(1) + '" y="' + (pad_t + 13) + '" '
                + 'font-size="9.5" font-weight="700" fill="#92400e">共识 ' + B.rng + '</text>');
            }}
          }}
        }}
      }}

      // FOMC 会议日：中性垂直虚线 + 顶部只显示「FOMC」小标签。
      // 会议标记独立于事件光环，不使用 hawk/dove 颜色，也不进入 tooltip。
      if (meetingLayer) {{
        meetingLayer.innerHTML = '';
        var fomcDates = data.fomc_dates || [];
        fomcDates.forEach(function(meetingDate){{
          var meetingIdx = -1;
          for (var mi = startIdx; mi <= endIdx; mi++) {{
            if (xDate(allx[mi]) === meetingDate) {{
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
        // 换档处断线：换档前后是两个不同档位的概率，硬连会制造假涨跌
        var pathD = '';
        var prevRng = null;
        slice.forEach(function(pt, j){{
          var xRel = xIndex[pt.x] - startIdx;
          var cmd = (j === 0 || pt.rng !== prevRng) ? 'M' : 'L';
          pathD += (pathD ? ' ' : '') + cmd + px(xRel).toFixed(1) + ',' + py(pt.v).toFixed(1);
          prevRng = pt.rng;
        }});
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

      // 线尾标签放右侧留白带（pad_r=190），纵向避让：先做顶部钳制再向下推开——
      // 顺序反了的话钳制会压缩已算好的间距（与 dual_charts 同款修法）。
      endLabels.sort(function(a, b){{ return a.y - b.y; }});
      var GAP = 44;
      var yTop = pad_t + 6, yBot = pad_t + ph + 4;
      for (var lc = 0; lc < endLabels.length; lc++){{
        if (endLabels[lc].y < yTop) endLabels[lc].y = yTop;
      }}
      endLabels.sort(function(a, b){{ return a.y - b.y; }});
      for (var li = 1; li < endLabels.length; li++){{
        if (endLabels[li].y - endLabels[li - 1].y < GAP)
          endLabels[li].y = endLabels[li - 1].y + GAP;
      }}
      if (endLabels.length) {{
        var over = endLabels[endLabels.length - 1].y - yBot;
        if (over > 0) {{ for (var lj = 0; lj < endLabels.length; lj++) endLabels[lj].y -= over; }}
        var under = yTop - endLabels[0].y;
        if (under > 0) {{ for (var lu = 0; lu < endLabels.length; lu++) endLabels[lu].y += under; }}
      }}
      // 顶层标签图层：位于所有曲线之上、绘图区外；pointer-events:none 不挡 capture 层悬停
      var labelLayer = svg.querySelector('.end-label-layer');
      if (!labelLayer) {{
        labelLayer = document.createElementNS('http://www.w3.org/2000/svg', 'g');
        labelLayer.setAttribute('class', 'end-label-layer');
        labelLayer.setAttribute('pointer-events', 'none');
        svg.appendChild(labelLayer);
      }}
      labelLayer.innerHTML = '';
      for (var lk = 0; lk < endLabels.length; lk++){{
        var L = endLabels[lk];
        var labelLeft = pad_l + pw + 10;
        labelLayer.insertAdjacentHTML('beforeend',
          '<g class="end-label" transform="translate(' + labelLeft.toFixed(1) + ',' + (L.y - 12).toFixed(1) + ')">'
          + '<rect x="0" y="-12" rx="4" width="178" height="34" fill="#ffffff" fill-opacity="0.96" '
          + 'stroke="' + L.color + '" stroke-opacity="0.55"/>'
          + '<text x="8" y="2" font-size="11.5" font-weight="700" fill="' + L.color + '">'
          + L.short + ' 会议</text>'
          + '<text x="8" y="18" font-size="11" fill="#374151">'
          + prettyRange(L.rng) + '  →  ' + L.v.toFixed(2) + '%</text>'
          + '</g>'
        );
      }}

      // 更新滑块旁日期与天数
      if (startVal) startVal.textContent = xDate(allx[startIdx]);
      if (endVal) endVal.textContent = xDate(allx[endIdx]);
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
      // 固定 / 取消固定会改变浮窗高度策略（阅读模式），需要按新状态重新布局
      if (tip.style.display === 'block') positionTip();
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
      var parts = ['<div class="tip-date">📅 ' + xTip(snap) + '</div>'];
      var any = false;
      series.forEach(function(s){{
        if (!active[s.md]) return;
        var pt = s.pointsByX[snap];
        if (!pt) return;
        var bps = bpsLabel(pt.rng, baseLo);
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
          // 来源可挂多条：events.csv 的 url 字段用 | 分隔；单条时保持原「查看来源」文案
          var srcUrls = (ev.url || '').split('|').map(function(s) {{ return s.trim(); }})
                          .filter(function(s) {{ return s.length > 0; }});
          var srcHtml = srcUrls.map(function(u, i) {{
            var lab = srcUrls.length > 1 ? '🔗 来源' + (i + 1) : '🔗 查看来源';
            return '<a class="tip-src" href="' + escHtml(u)
                 + '" target="_blank" rel="noopener">' + lab + '</a>';
          }}).join(' ');
          parts.push(
            '<div class="tip-event">'
            // 来源链接直接跟在事件标题后面：即使正文很长需要滚动，链接也始终可见可点
            + '<div class="tip-event-head" style="color:' + evColor + '">' + arrowI + ' '
            + escHtml(evLab) + '：' + escHtml(ev.summary || '')
            + (srcHtml ? ' ' + srcHtml : '')
            + '</div>'
            // 正文里的换行转 <br>：先 escHtml 再替换，不引入注入面
            // 注意：本段位于 Python 模板字符串内，正则里的换行必须写成 \\n，
            // 否则会被 Python 先解释成真实换行，生成页出现跨行正则（SyntaxError）。
            + (ev.text ? '<div class="tip-event-body">'
                + escHtml(ev.text).split('\\n').join('<br>') + '</div>' : '')
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

    // 浮窗锚定在绘图区右下角内侧 —— 位置完全由图表几何决定、与鼠标坐标无关 ⇒
    // 鼠标怎么移动浮窗都纹丝不动，可以直接把鼠标移到「查看来源」上点击，不存在追逐问题。
    // 2026-09-29：pad_r 收窄到 12、绘图区铺满卡片后，右侧不再有停靠带；
    // 浮窗改为覆盖在图上（选右下角：近期读数集中在 40–75% 带，图的下右角数据最少）。
    function positionTip(){{
      var hr = container.getBoundingClientRect();
      var sr = svg.getBoundingClientRect();
      var svgLeft = sr.left - hr.left, svgTop = sr.top - hr.top;
      var scale = sr.width / VBW;
      var tipW = Math.min(300, Math.round(hr.width * 0.82));
      tip.style.width = tipW + 'px';
      // 右缘对齐绘图区右缘内缩 6px；底缘对齐绘图区下缘内缩 6px。
      // 用 bottom 定位：固定态长文向上生长，不会掉出绘图区下沿。
      var plotBottomCss = svgTop + (pad_t + ph) * scale;
      tip.style.left = Math.round(svgLeft + (pad_l + pw) * scale - tipW - 6 * scale) + 'px';
      tip.style.top = 'auto';
      tip.style.bottom = Math.round(hr.height - plotBottomCss + 6 * scale) + 'px';
      // 悬停态限高在绘图区内；固定态进入阅读模式，放宽高度以便读完归因正文
      var hovH = Math.round(ph * scale - 12);
      if (pinned) {{
        tip.style.maxHeight = Math.round(Math.max(hovH,
          Math.min(hr.height * 0.82, 660))) + 'px';
      }} else {{
        tip.style.maxHeight = hovH + 'px';
      }}
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
