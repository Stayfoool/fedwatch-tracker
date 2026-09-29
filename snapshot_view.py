"""展示层「一天一个点」的唯一选取规则 —— 检测 / 绘图 / 归因绑定三方共用。

背景（2026-09-18 事故复盘）：同一个美东交易日可能先后有两条读数 ——
前一天的 ``legacy_intraday``（北京 10:00 采集的盘中值）与次日早晨的 ``page_asof``
（收盘定格）。展示层每天只画一个点，**收盘定格胜出**，被顶掉那条的
``snapshot_cn`` 就不再出现在图上。

`events.csv` 的归因与 `analyze_changes.py` 的检测原先都按 ``snapshot_cn``
逐字符匹配，于是 2026-09-17 出现了「数据换了、光环没了」：
归因挂在 ``2026-09-17 10:00:00``（盘中，已被顶掉），
而图上那个点是 ``2026-09-18 05:30:00``（收盘定格）→ 对不上，光环与浮窗双双失踪。

因此定下两条规则：

1. **选取规则集中在本模块**，`build_report.py`（画点）与 `analyze_changes.py`
   （检测）共用同一份实现，不允许各自维护一套。
2. **归因按 ``us_trade_date``（美东交易日）绑定**，不再按 ``snapshot_cn``
   精确匹配 —— 这样无论口径怎么切换、哪条快照胜出，光环都跟着当天那个点走。
"""

from __future__ import annotations

from collections import defaultdict

from time_axis import BASIS_EXPORT, BASIS_LEGACY, BASIS_PAGE

# 同一个美东交易日的多条读数，谁优先成为图上的那个点：
# 收盘定格（page_asof） > 结算导出（settlement_export） > 盘中读数（legacy_intraday）
BASIS_RANK = {BASIS_PAGE: 0, BASIS_EXPORT: 1, BASIS_LEGACY: 2}

DEFAULT_RANK = 9


def us_day_of(row: dict) -> str:
    """该行数据对应的美东交易日；缺失时退回 `snapshot_cn` 的日期部分。"""
    snap = (row.get("snapshot_cn") or "").strip()
    return (row.get("us_trade_date") or "").strip() or snap[:10]


def rank_of(row: dict) -> int:
    return BASIS_RANK.get((row.get("time_basis") or "").strip(), DEFAULT_RANK)


def _valid_reading(row: dict) -> bool:
    """该行是否是一条可用的读数（有标签、概率 > 0）。

    QuikStrike 会为尚未进入可计算窗口的远期会议写全 0 占位行，
    那些行不是「0% 概率」，不能参与判定。
    """
    if not (row.get("snapshot_cn") or "").strip():
        return False
    if not (row.get("meeting_date") or "").strip():
        return False
    if not (row.get("max_range_label") or "").strip():
        return False
    try:
        return float((row.get("max_range_pct") or "").strip()) > 0
    except ValueError:
        return False


def displayed_by_day(rows) -> dict[str, str]:
    """{us_trade_date: snapshot_cn} —— 每天只留一个点。

    同一天多条读数时按 `BASIS_RANK` 取最优口径；同口径取**最早**的
    `snapshot_cn`（字典序即时间序），与历史行为一致。
    """
    best: dict[str, tuple[int, str]] = {}
    for r in rows:
        if not _valid_reading(r):
            continue
        day = us_day_of(r)
        snap = (r.get("snapshot_cn") or "").strip()
        cand = (rank_of(r), snap)
        prev = best.get(day)
        if prev is None or cand < prev:
            best[day] = cand
    return {day: snap for day, (_rank, snap) in best.items()}


def displayed_rows(rows) -> list[dict]:
    """只保留落在「当天那个点」上的行（按美东交易日去重后）。"""
    winners = displayed_by_day(rows)
    return [r for r in rows
            if winners.get(us_day_of(r)) == (r.get("snapshot_cn") or "").strip()]


def remap_events_to_points(events_by_snap: dict, rows, series_map: dict) -> dict:
    """把 `events.csv` 的 `snapshot_cn` 键改写成**图上实际画出的那个点**。

    规则：归因挂到美东交易日相同的点上。这样每天早晨的收盘定格顶掉盘中读数
    （或将来任何一种口径切换）时，光环都不会再挂空。

    若某个交易日没有出现在图上（会议被裁掉等），保留原键 —— 宁可挂在不可见的点上，
    也不能把归因丢掉。
    """
    snap_day = {}
    for r in rows:
        s = (r.get("snapshot_cn") or "").strip()
        if s and s not in snap_day:
            snap_day[s] = us_day_of(r)

    plotted: dict[str, set[str]] = defaultdict(set)
    for pts in series_map.values():
        for item in pts:
            snap = item[0]
            plotted[snap_day.get(snap, snap[:10])].add(snap)

    out: dict[str, list] = defaultdict(list)
    for key, evs in (events_by_snap or {}).items():
        targets = plotted.get(snap_day.get(key, key[:10]))
        if targets:
            for t in targets:
                out[t].extend(evs)
        else:
            out[key].extend(evs)
    return dict(out)
