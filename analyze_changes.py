#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_changes.py — 自动找出「重大变动日」并输出待分析清单。

判定口径：某次会议的 max_range_pct（最大概率区间的概率）相对**上一次采集**
日环比变化绝对值 >= 阈值（默认 8pp）。默认只看最近 30 天。

**焦点会议（--focus nearest，默认）**：只盯「最近的、尚未开完的那场 FOMC」。
焦点会议 = 该快照里最早的、`meeting_date >= 快照日` 的会议；全部已过期则取最后一个。
会议开完后的第一个快照，焦点会自然滚到下一场，此时换会议不比对（新会议需要自己的基线）。

为什么默认只看焦点会议：
  - 远端会议报价稀疏、常年冻结后偶发暴跳（中位 |Δ| = 0.00pp、一跳 40–70pp），
    这是薄流动性的形态，不是宏观重定价，送进归因队列只会产出编出来的故事；
  - 一场会议开完会让「整表」概率被重新归一化，同一快照里五六场会议齐刷刷跳同样的
    百分点数（实测 2026-09-16 五场齐刷刷 +14.00pp），那是机制性的，不是一个事件；
  - 只看焦点会议后，全年 ≥8pp 从 41 次降到 29 次，其中换桶仅 3 次，媒体命中率显著更高。
  需要全会议口径时用 `--focus all`。

用法：
  python analyze_changes.py                      # 最近 30 天，阈值 8pp，焦点会议
  python analyze_changes.py --days 7             # 最近 7 天
  python analyze_changes.py --thr 10             # 阈值改 10pp
  python analyze_changes.py --all                # 全部历史
  python analyze_changes.py --focus all           # 回到全会议口径
  python analyze_changes.py --quiet              # 只在有新变动日时输出

输出：
  1. 终端表格（每天一行，含变动最大会议）
  2. data/significant_changes.csv —— 待分析清单，供人工/上游流程消费
     列：snapshot_cn, meeting_date, direction, delta_pp,
         prev_label, prev_pct, cur_label, cur_pct,
         annotated, event_summary
     annotated=1 表示 data/events.csv 里已有对应条目。
     meeting_date 即该日「焦点会议」（全会议口径下为变动最大的那场），
     也是归因要挂到折线图哪条曲线上的依据。

退出码：0 正常；2 发现「尚未标注」的变动日（便于外层判断是否需要提醒）。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta

from snapshot_view import displayed_by_day, displayed_rows, us_day_of

# `range_375_400_pct` → 区间标签 '375-400'
RANGE_KEY_RE = re.compile(r"^range_(\d+)_(\d+)_pct$")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE_DIR, "data", "fedwatch_probabilities.csv")
EVENTS_PATH = os.path.join(BASE_DIR, "data", "events.csv")
OUT_PATH = os.path.join(BASE_DIR, "data", "significant_changes.csv")

DEFAULT_THR = 8.0        # pp
DEFAULT_DAYS = 30
MAX_GAP_DAYS = 5         # 超过这个天数的间隔视为数据缺口，跳过比对


def load_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def parse_dt(s: str):
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def focus_meeting_of(rows_of_snap, snap_date):
    """该快照的「焦点会议」：最早的尚未过期（meeting_date >= 快照日）的会议。

    会议开完当天仍算焦点（当天才公布决议），到下一个快照才滚走。
    全部会议都已过期（极少见）时退回最后一个会议，避免返回 None 让整条链路断掉。
    """
    dated = [r for r in rows_of_snap if parse_dt(r.get("meeting_date") or "")]
    if not dated:
        return None
    fut = [r for r in dated if parse_dt(r["meeting_date"]) >= snap_date]
    pool = fut if fut else dated
    return min(pool, key=lambda r: r["meeting_date"])["meeting_date"]


def range_dist(row) -> dict:
    """该行各绝对利率区间的概率，**统一为百分数**：{'375-400': 60.0, ...}。

    只用 `range_XXX_YYY_pct` 列，它们恒为百分数。
    不要用 `aggregated_ranges` 那一列：它在历史导出段是 0-1 小数、
    在实时段是 0-100 百分数，**同列混用两套单位** ——
    直接相减会把 24pp 的真实变动算成 0.24，`thr=8` 就永远判不成立
    （2026-09-18 就是这么把 03-12 / 09-03 / 09-04 三天误判成「不重大」的）。

    注意：历史导出段（settlement_export）的分布常常只列了主要区间、
    合计不足 100%，但同一段内前后两行的口径一致，差值仍然可用。
    """
    out = {}
    for k, v in row.items():
        m = RANGE_KEY_RE.match(k or "")
        if not m:
            continue
        raw = (v or "").strip()
        if raw == "":
            continue
        try:
            out[f"{m.group(1)}-{m.group(2)}"] = float(raw)
        except ValueError:
            continue
    return out


def measure_change(prev, cur):
    """一天的变动幅度怎么量 —— 换桶日必须换口径，否则会漏掉大变动。

    非换桶日：图上那条线的读数之差就是真实变动，直接用 `max_range_pct` 相减。

    换桶日：`max_range_pct` 前后属于**两个不同的结果**（如 375-400 → 400-425），
    直接相减没有意义。2026-09-17 就是典型：图上读数只动了 −6.86pp，
    但「维持 375-400」桶实际 60.00%→46.86%（−13.14pp）、
    「加息 25bp 400-425」桶 40.00%→53.14%（+13.14pp）——
    按最大桶差来判阈值，这次 ≥8pp 的大变动会被判成「不重大」，光环直接不画。
    03-12 / 09-03 / 09-04 三天同理。

    取「同一绝对区间」里 |Δ| 最大者作为幅度；方向仍沿用**图上最大桶**的涨跌，
    保证光环颜色与读者看到的线段方向一致（本项目既有口径，见 docs）。
    返回 (delta_signed, direction, detail)。
    """
    try:
        prev_p = float(prev.get("max_range_pct") or "")
        cur_p = float(cur.get("max_range_pct") or "")
    except ValueError:
        return None

    prev_label = (prev.get("max_range_label") or "").strip()
    cur_label = (cur.get("max_range_label") or "").strip()
    bucket_switch = prev_label != cur_label
    chart_delta = cur_p - prev_p

    detail = {"bucket_switch": bucket_switch, "chart_delta_pp": chart_delta,
              "range_label": "", "range_delta_pp": None}

    dist, dist_cur = range_dist(prev), range_dist(cur)
    if not bucket_switch or not dist or not dist_cur:
        detail["range_label"] = prev_label
        detail["range_delta_pp"] = chart_delta
        return chart_delta, ("hawk" if chart_delta > 0 else "dove"), detail

    deltas = {k: dist_cur.get(k, 0.0) - dist.get(k, 0.0) for k in sorted(set(dist) | set(dist_cur))}
    best_label, best_delta = max(deltas.items(), key=lambda kv: abs(kv[1]))
    # 幅度并列时优先取「旧主桶」的变动：那是上一个点真正代表的那个结果，
    # 也保证方向判断稳定（375-400 ↓13.14 与 400-425 ↑13.14 是同一件事的两面）。
    if prev_label in deltas and abs(deltas[prev_label]) >= abs(best_delta) - 1e-9:
        best_label, best_delta = prev_label, deltas[prev_label]

    amp = abs(best_delta)
    direction = "hawk" if chart_delta > 0 else ("dove" if chart_delta < 0
                                               else ("hawk" if best_delta > 0 else "dove"))
    detail["range_label"] = best_label
    detail["range_delta_pp"] = best_delta
    return (amp if direction == "hawk" else -amp), direction, detail


def find_changes_focus(rows, thr: float, days: int | None):
    """只看焦点会议的口径（推荐）。

    逐日取焦点会议，与**上一个出图日里同一场会议**的值比对。
    会议滚动（旧会议开完、焦点换到下一场）时，新焦点仍能拿它自己的历史值当基线；
    若上一个出图日根本没有这场会议（刚进 FedWatch 天窗），跳过——没有基线就没法算日环比。

    **日期序列必须与展示层完全一致**：同一个美东交易日可能有多条读数
    （早晨的收盘定格会顶掉前一天的盘中读数），图每天只画一个点。
    这里复用 `snapshot_view.displayed_by_day()` 的裁决，
    否则会出现「检测说这天有变动，图上却找不到那个点」——
    2026-09-17 的归因光环就是这么丢的。
    """
    by_snap = defaultdict(list)
    for r in rows:
        s = (r.get("snapshot_cn") or "").strip()
        if s:
            by_snap[s].append(r)

    winners = displayed_by_day(rows)
    snaps = sorted(set(winners.values()))
    day_of = {s: (next((r.get("us_trade_date") or "" for r in by_snap[s]
                        if (r.get("us_trade_date") or "").strip()), s[:10])).strip()
              for s in snaps}

    cutoff = None
    if days is not None and snaps:
        latest = parse_dt(day_of[snaps[-1]])
        if latest:
            cutoff = latest - timedelta(days=days)

    out = []
    for i in range(1, len(snaps)):
        ps, cs = snaps[i - 1], snaps[i]
        d_prev, d_cur = parse_dt(day_of[ps]), parse_dt(day_of[cs])
        if not d_prev or not d_cur:
            continue
        if (d_cur - d_prev).days > MAX_GAP_DAYS:
            continue                      # 数据缺口，不比对
        if cutoff and d_cur < cutoff:
            continue
        md = focus_meeting_of(by_snap[cs], d_cur)
        if not md:
            continue
        prev = next((r for r in by_snap[ps] if (r.get("meeting_date") or "") == md), None)
        cur = next((r for r in by_snap[cs] if (r.get("meeting_date") or "") == md), None)
        if not prev or not cur:
            continue                      # 新会议刚进表，无基线
        if not prev.get("max_range_label") or not cur.get("max_range_label"):
            continue
        measured = measure_change(prev, cur)
        if measured is None:
            continue
        delta, direction, _detail = measured
        if abs(delta) < thr:
            continue
        out.append((cs, md, direction, delta, prev, cur))
    return out


def find_changes(rows, thr: float, days: int | None, focus: str = "nearest"):
    """焦点会议口径 / 全会议口径的统一入口。

    focus="nearest" → find_changes_focus（默认，只跟最近一场会议）
    focus="all"     → 下面这段全会议扫描（历史上 41 次触发，含大量远端噪声）
    两种口径返回同一形状的 6 元组，下游 main() 无需分支。
    """
    if focus == "nearest":
        return find_changes_focus(rows, thr, days)

    # 同一天只留出图的那一条，保证与展示层对齐（否则盘中读数被顶掉后会对不上）
    rows = displayed_rows(rows)

    by_mtg = defaultdict(list)
    for r in rows:
        md = (r.get("meeting_date") or "").strip()
        if md:
            by_mtg[md].append(r)

    cutoff = None
    if days is not None and rows:
        latest = max((parse_dt(r.get("snapshot_cn", "")) for r in rows), default=None)
        if latest:
            cutoff = latest - timedelta(days=days)

    out = []
    for md, mrows in by_mtg.items():
        mrows.sort(key=lambda r: r.get("snapshot_cn") or "")
        for i in range(1, len(mrows)):
            prev, cur = mrows[i - 1], mrows[i]
            if not prev.get("max_range_label") or not cur.get("max_range_label"):
                continue
            measured = measure_change(prev, cur)
            if measured is None:
                continue
            delta, direction, _detail = measured
            d_prev, d_cur = parse_dt(prev.get("snapshot_cn", "")), parse_dt(cur.get("snapshot_cn", ""))
            if not d_prev or not d_cur:
                continue
            if (d_cur - d_prev).days > MAX_GAP_DAYS:
                continue                      # 数据缺口，不比对
            if abs(delta) < thr:
                continue
            if cutoff and d_cur < cutoff:
                continue
            out.append((cur["snapshot_cn"], md, direction, delta, prev, cur))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help=f"只看最近 N 天（默认 {DEFAULT_DAYS}）；配合 --all 忽略")
    ap.add_argument("--thr", type=float, default=DEFAULT_THR,
                    help=f"变动阈值（pp，默认 {DEFAULT_THR}）")
    ap.add_argument("--all", action="store_true", help="全部历史，不限天数")
    ap.add_argument("--focus", choices=("nearest", "all"), default="nearest",
                    help="nearest=只看最近一场未开完的会议（默认）；all=扫描全部会议")
    ap.add_argument("--quiet", action="store_true", help="只在有新变动日时输出")
    args = ap.parse_args()

    rows = load_csv(CSV_PATH)
    if not rows:
        print(f"未找到数据：{CSV_PATH}")
        return 1

    # 已有标注：按**美东交易日**匹配。
    # events.csv 可能写在任一 snapshot_cn 上（前一天的盘中读数，或当天早晨的收盘定格），
    # 而图上那个点的 snapshot_cn 每天由展示层裁决 —— 只按 snapshot_cn 精确匹配会漏。
    # 因此同时用「快照日期」与「该快照对应的美东交易日」两个键索引。
    snap_day = {}
    for r in rows:
        s = (r.get("snapshot_cn") or "").strip()
        if s and s not in snap_day:
            snap_day[s] = us_day_of(r)

    events = load_csv(EVENTS_PATH)
    annotated_dates = {}
    for e in events:
        s = (e.get("snapshot_cn") or "").strip()
        if not s:
            continue
        for k in {s[:10], snap_day.get(s)}:
            if k:
                annotated_dates.setdefault(k, []).append(e.get("summary") or "")

    def day_of(snap: str) -> str:
        return snap_day.get(snap) or snap[:10]

    found = find_changes(rows, args.thr, None if args.all else args.days, args.focus)
    scope = "焦点会议" if args.focus == "nearest" else "全部会议"

    # 按 snapshot 聚合
    by_snap = defaultdict(list)
    for snap, md, direction, delta, prev, cur in found:
        by_snap[snap].append((md, direction, delta, prev, cur))

    if not by_snap:
        if not args.quiet:
            print(f"最近 {'全部' if args.all else str(args.days)+' 天'}内"
                  f"（{scope}口径）无 ≥{args.thr:g}pp 的重大变动。")
        return 0

    # 写待分析清单
    out_rows = []
    for snap in sorted(by_snap):
        d = day_of(snap)
        evs = annotated_dates.get(d, [])
        for md, direction, delta, prev, cur in sorted(by_snap[snap], key=lambda x: -abs(x[2])):
            measured = measure_change(prev, cur) or (delta, direction, {})
            _delta, _dir, det = measured
            out_rows.append({
                "snapshot_cn": snap,
                # 归因检索必须以美东交易日为锚：北京日期会整体错位一天
                "us_trade_date": day_of(snap),
                "meeting_date": md,
                "direction": direction,
                "delta_pp": f"{delta:+.2f}",
                "bucket_switch": "1" if det.get("bucket_switch") else "0",
                "chart_delta_pp": f"{det.get('chart_delta_pp', 0.0):+.2f}",
                "sampled_range": det.get("range_label", ""),
                "sampled_range_delta_pp": (f"{det['range_delta_pp']:+.2f}"
                                           if det.get("range_delta_pp") is not None else ""),
                "prev_label": prev.get("max_range_label", ""),
                "prev_pct": prev.get("max_range_pct", ""),
                "cur_label": cur.get("max_range_label", ""),
                "cur_pct": cur.get("max_range_pct", ""),
                "annotated": "1" if evs else "0",
                "event_summary": "; ".join(s for s in evs if s),
            })
    with open(OUT_PATH, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
        w.writeheader()
        w.writerows(out_rows)

    # 终端输出：每个变动日一行摘要
    unannotated = 0
    if not args.quiet:
        print(f"重大变动日（{scope}口径，≥{args.thr:g}pp）：{len(by_snap)} 天\n")
    for snap in sorted(by_snap):
        d = day_of(snap)
        evs = annotated_dates.get(d, [])
        items = sorted(by_snap[snap], key=lambda x: -abs(x[2]))
        biggest_md, biggest_dir, biggest_delta, _, _ = items[0]
        arrow = "🔺" if biggest_delta > 0 else "🔻"
        mark = "✅ 已标注" if evs else "⚠️  待标注"
        if not evs:
            unannotated += 1
        if not args.quiet:
            print(f"{arrow} {d}  {mark}  最大变动 {biggest_md} {biggest_delta:+.2f}pp"
                  + (f"  （{len(items)} 个会议超阈值）" if len(items) > 1 else ""))
            for md, direction, delta, prev, cur in items:
                measured = measure_change(prev, cur) or (delta, direction, {})
                det = measured[2]
                extra = ""
                if det.get("bucket_switch"):
                    extra = (f"  ★换桶（图上读数 {det['chart_delta_pp']:+.2f}pp；"
                             f"按同一区间 {det['range_label']} "
                             f"{det['range_delta_pp']:+.2f}pp 判定）")
                print(f"      {md}  {prev.get('max_range_label')}({prev.get('max_range_pct')}%)"
                      f" → {cur.get('max_range_label')}({cur.get('max_range_pct')}%)  {delta:+.2f}pp{extra}")
            if evs:
                print(f"      事件：{'; '.join(e for e in evs if e)}")
            print()

    if not args.quiet:
        print(f"清单已写入：{os.path.relpath(OUT_PATH, BASE_DIR)}")
        if unannotated:
            print(f"\n有 {unannotated} 个变动日尚未在 data/events.csv 中说明原因。")
            print("补录格式：snapshot_cn,direction,summary,text,url")
    return 2 if unannotated else 0


if __name__ == "__main__":
    sys.exit(main())
