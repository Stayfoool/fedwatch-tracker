#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze_changes.py — 自动找出「重大变动日」并输出待分析清单。

判定口径：某次会议的 max_range_pct（最大概率区间的概率）相对**上一次采集**
日环比变化绝对值 >= 阈值（默认 8pp）。默认只看最近 30 天。

用法：
  python analyze_changes.py                      # 最近 30 天，阈值 8pp
  python analyze_changes.py --days 7             # 最近 7 天
  python analyze_changes.py --thr 10             # 阈值改 10pp
  python analyze_changes.py --all                # 全部历史
  python analyze_changes.py --quiet              # 只在有新变动日时输出

输出：
  1. 终端表格（每天一行，含变动最大会议）
  2. data/significant_changes.csv —— 待分析清单，供人工/上游流程消费
     列：snapshot_cn, meeting_date, direction, delta_pp,
         prev_label, prev_pct, cur_label, cur_pct,
         annotated, event_summary
     annotated=1 表示 data/events.csv 里已有对应条目。

退出码：0 正常；2 发现「尚未标注」的变动日（便于外层判断是否需要提醒）。
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

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


def find_changes(rows, thr: float, days: int | None):
    """返回 [(snapshot_cn, meeting_date, direction, delta, prev_row, cur_row), ...]"""
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
            try:
                prev_p = float(prev.get("max_range_pct") or "")
                cur_p = float(cur.get("max_range_pct") or "")
            except ValueError:
                continue
            d_prev, d_cur = parse_dt(prev.get("snapshot_cn", "")), parse_dt(cur.get("snapshot_cn", ""))
            if not d_prev or not d_cur:
                continue
            if (d_cur - d_prev).days > MAX_GAP_DAYS:
                continue                      # 数据缺口，不比对
            delta = cur_p - prev_p
            if abs(delta) < thr:
                continue
            if cutoff and d_cur < cutoff:
                continue
            direction = "hawk" if delta > 0 else "dove"
            out.append((cur["snapshot_cn"], md, direction, delta, prev, cur))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help=f"只看最近 N 天（默认 {DEFAULT_DAYS}）；配合 --all 忽略")
    ap.add_argument("--thr", type=float, default=DEFAULT_THR,
                    help=f"变动阈值（pp，默认 {DEFAULT_THR}）")
    ap.add_argument("--all", action="store_true", help="全部历史，不限天数")
    ap.add_argument("--quiet", action="store_true", help="只在有新变动日时输出")
    args = ap.parse_args()

    rows = load_csv(CSV_PATH)
    if not rows:
        print(f"未找到数据：{CSV_PATH}")
        return 1

    # 已有标注（按日期匹配，忽略时分秒）
    events = load_csv(EVENTS_PATH)
    annotated_dates = {}
    for e in events:
        d = (e.get("snapshot_cn") or "")[:10]
        if d:
            annotated_dates.setdefault(d, []).append(e.get("summary") or "")

    found = find_changes(rows, args.thr, None if args.all else args.days)

    # 按 snapshot 聚合
    by_snap = defaultdict(list)
    for snap, md, direction, delta, prev, cur in found:
        by_snap[snap].append((md, direction, delta, prev, cur))

    if not by_snap:
        if not args.quiet:
            print(f"最近 {'全部' if args.all else str(args.days)+' 天'}内无 ≥{args.thr:g}pp 的重大变动。")
        return 0

    # 写待分析清单
    out_rows = []
    for snap in sorted(by_snap):
        d = snap[:10]
        evs = annotated_dates.get(d, [])
        for md, direction, delta, prev, cur in sorted(by_snap[snap], key=lambda x: -abs(x[2])):
            out_rows.append({
                "snapshot_cn": snap,
                "meeting_date": md,
                "direction": direction,
                "delta_pp": f"{delta:+.2f}",
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
        print(f"重大变动日（≥{args.thr:g}pp）：{len(by_snap)} 天\n")
    for snap in sorted(by_snap):
        d = snap[:10]
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
                print(f"      {md}  {prev.get('max_range_label')}({prev.get('max_range_pct')}%)"
                      f" → {cur.get('max_range_label')}({cur.get('max_range_pct')}%)  {delta:+.2f}pp")
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
