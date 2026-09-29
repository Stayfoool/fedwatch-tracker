#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rate_baseline.py — 联邦基金目标区间（加息/维持/降息的基准）的统一推导。

背景（2026-09-29 官网对账事故）：美联储 9/16 加息 25bp 后，
  · fetch_quikstrike.py 曾写死 CURRENT_TARGET = 350-375，CSV 的 current_target
    列从不更新；
  · build_report.py 的 +Xbp 相对标签在 Python / JS 两处硬编码 350 基准。
拿过期基准算「加息」会把已兑现的加息也计进去 —— P(加息) 自 9/11 起虚钉 100%，
而 FedWatch 官网同口径约 70%（维持 27.5 / 加息 72.5）。

推导规则（落盘 fetch / 主看板 build_report / 双图页 build_curves 三方共用）：

  现行目标 = 最近一次**已开完** FOMC 决议的结果。决议结果从该会议决议日当天的
  最终读数取众数桶（临近决议市场已收敛，如 2026-09-16 会议最终读数 92.4% 落在
  375-400 → 决议后目标 375-400）。数据里没有的决议（QuikStrike 回填只含下载时
  未开完的会议）沿用 current_target 列作为该时代的目标。

注意：CSV 的 current_target / agg_* 列是**落盘时刻的派生值**，历史行不会回改；
展示端一律用本模块现算，不读这些列。
"""
from __future__ import annotations

from collections import defaultdict

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


def discover_range_columns(rows) -> dict[str, tuple[int, int]]:
    """{列名: (lo, hi)} —— 从表头发现所有 range_XXX_YYY_pct 列。"""
    if not rows:
        return {}
    cols = {}
    for k in rows[0].keys():
        if not (k.startswith("range_") and k.endswith("_pct")):
            continue
        try:
            lo_s, hi_s = k[len("range_"):-len("_pct")].split("_")
            cols[k] = (int(lo_s), int(hi_s))
        except ValueError:
            continue
    return cols


def bucket_stats(row: dict, range_cols: dict, target: str):
    """→ (hike, hold, cut, exp_bp, best_label, best_pct) 或 None（占位行）。

    hike/hold/cut 均为百分数，按给定 target 区间划分：桶 lo ≥ 目标 hi 为加息、
    桶 hi ≤ 目标 lo 为降息、其余（含目标桶本身）为维持；
    exp_bp = Σ p/100 × (桶中点 − 目标中点)。
    """
    try:
        tl, th = (int(x) for x in target.split("-"))
    except (ValueError, AttributeError):
        return None
    tm = (tl + th) / 2.0
    hike = hold = cut = 0.0
    exp = 0.0
    best_label, best_pct = "", 0.0
    seen = False
    for key, (lo, hi) in range_cols.items():
        try:
            v = float(row.get(key) or 0)
        except ValueError:
            continue
        if v <= 0:
            continue
        seen = True
        if lo >= th:
            hike += v
        elif hi <= tl:
            cut += v
        else:
            hold += v
        exp += v / 100.0 * ((lo + hi) / 2.0 - tm)
        if v > best_pct:
            best_pct, best_label = v, f"{lo}-{hi}"
    if not seen:
        return None
    return hike, hold, cut, exp, best_label, best_pct


def derive_target_eras(rows, range_cols, latest_us_day: str):
    """[(生效日, 决议后目标区间)] —— 已开完的 FOMC 决议从其会议最终读数推目标。

    只看决议日 ≤ latest_us_day 且在数据里有读数的会议；未开完的会议其
    「最后读数」只是最新快照，不代表决议结果，不参与。
    """
    by_m = defaultdict(list)
    for r in rows:
        m = (r.get("meeting_date") or "").strip()
        if m:
            by_m[m].append(r)
    eras = []
    for d in sorted(set(FOMC_DECISION_DATES) & set(by_m)):
        if d > latest_us_day:
            continue
        rs = sorted(by_m[d], key=lambda r: r["snapshot_cn"])
        last = rs[-1]
        st = bucket_stats(last, range_cols, (last.get("current_target") or "350-375").strip())
        if not st or not st[4] or st[5] < 50:
            continue
        if st[5] < 80:
            print(f"WARN 决议 {d} 众数桶 {st[4]} 仅 {st[5]:.1f}%，目标推导存疑")
        eras.append((d, st[4]))
    return eras


def era_target_for(day: str, eras, fallback: str) -> str:
    """该美东交易日适用的目标区间。"""
    tgt = fallback
    for d, label in eras:
        if day >= d:
            tgt = label
    return tgt


def resolve_current_target(history_rows, snapshot_meetings, us_today: str) -> str:
    """采集/回填落盘时的现行目标。

    优先级：① 今天恰有决议（meeting_date == us_today，采集在收盘后）且该会议
    读数已收敛（众数 ≥ 50%）→ 众数桶即新目标；
    ② 否则用历史数据推导（derive_target_eras + era_target_for）；
    ③ 无历史时退回 350-375（数据起点时代的值）。
    """
    for m in snapshot_meetings or []:
        if (m.get("meeting_date") or "") != us_today:
            continue
        probs = dict(zip(m.get("ranges", []), m.get("probabilities", [])))
        probs = {k: v for k, v in probs.items() if isinstance(v, (int, float))}
        if probs:
            label, pct = max(probs.items(), key=lambda kv: kv[1])
            if pct >= 50:
                return label
    if history_rows:
        range_cols = discover_range_columns(history_rows)
        eras = derive_target_eras(history_rows, range_cols, us_today)
        fallback = next(((r.get("current_target") or "").strip()
                         for r in reversed(history_rows)
                         if (r.get("current_target") or "").strip()), "350-375")
        return era_target_for(us_today, eras, fallback)
    return "350-375"
