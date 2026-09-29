#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""snapshot_pick.py — 从全部采集快照中选出「用于渲染未来会议」的那一次。

## 背景：一个真实发生过的线上故障

CME QuikStrike FedWatch 的 Aggregated 表中，某次会议一旦开完就会从抓取表里
消失，快照的会议总数随之 **减 1**；而 FedWatch 只有在更远的会议进入可计算窗口
后才会追加新会议（通常间隔数月）。

因此旧排序键 `max(by_snap, key=lambda s: (len(by_snap[s]), s))`
（会议数多者优先，其次才取最新）会在每次会议开完后 **永久卡住**：
过期那天有 11 个会议，此后所有新快照只有 10 个，永远赢不了旧快照，
站点会一直展示「已经开完的那次会议」，直到会议数被重新推高为止。

实例（2026-09-17）：2026-09-16 FOMC 结束后，09-17 的快照由 11 条降为 10 条，
线上首页因此长期停留在「最近快照 2026-09-16」，未来会议仍显示
09-16 / 10-28 / 12-09，而不是应滚动的 10-28 / 12-09 / 2027-01-27。

## 现在的规则

**以「最新」为第一优先**，只用完整度下限挡住「半截采集」：

1. 在最近 RECENT_DAYS 个采集日中取会议数最大值 baseline；
2. 允许比 baseline 少 TOLERANCE 条——取 1，因为 FOMC 会议间隔约 6 周，
   任意 7 天窗口内最多只会掉出 1 个会议；
3. 从最新往前找第一个满足 `会议数 >= baseline - TOLERANCE` 的快照。

于是：正常的一天（10 条 vs baseline 11）会被采用；真正抓崩的一天（只有 1-3 条）
会被跳过并退回上一个完整快照——旧逻辑想防的正是后者。

## 为什么 TOLERANCE 取 3 而不是 1

「掉 1 条」是会议到期的正常表现，但**完整度下限宁松勿紧**：下限太严会在任何
非 1 条的自然缩减（例如 FedWatch 同时收窄远端天窗）时误判为半截采集，于是回退到
**含已过期会议的旧快照**——正好复现我们要修的故障。两个失败方向不对称：

- 下限过松 → 只丢失远端几场会议一天，次日自愈，且日志有 WARN；
- 下限过严 → 把已开完的会议当成「未来会议」展示，就是这次的事故。

实测的半截采集是「只剩 1-3 条」，`baseline - 3` 足以挡住，因此取 3。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

# 计算完整度基准时回看的采集日数（快照键按时间排序）。
RECENT_DAYS = 7
# 允许比基准少的会议条数。宁松勿紧，理由见模块 docstring。
TOLERANCE = 3


def pick_latest_snapshot(
    by_snap: Mapping[str, Sequence],
    *,
    recent_days: int = RECENT_DAYS,
    tolerance: int = TOLERANCE,
) -> str:
    """返回应当用于渲染的快照键（snapshot_cn）。

    :param by_snap: ``{snapshot_cn: [该快照的行, ...]}``，键为
        ``"YYYY-MM-DD HH:MM:SS"``，按字符串排序即按时间排序。
    """
    keys = sorted(by_snap)
    if not keys:
        raise ValueError("by_snap 为空，无法选择快照")

    recent = keys[-recent_days:] if recent_days > 0 else keys
    baseline = max(len(by_snap[k]) for k in recent)
    # 注意：这里**不能**写 max(1, baseline - tolerance)。若整个近期窗口本来就只有
    # 0-1 场会议（例如 FedWatch 天窗收窄到极限），下限取 1 会让最新的空快照永远
    # 不合格，于是回退到含已过期会议的旧快照。下限只允许来自 baseline。
    floor = baseline - tolerance

    for key in reversed(keys):
        if len(by_snap[key]) >= floor:
            return key
    return keys[-1]


def _selftest() -> None:
    """单元回归：会议到期、半截采集、同日多次采集。"""
    def snaps(counts: list[tuple[str, int]]) -> dict[str, list[int]]:
        return {f"2026-09-{d} 10:00:00": list(range(n)) for d, n in counts}

    # 1) 会议结束后总数由 11 降为 10：必须选最新的一天（旧逻辑会卡在 09-16）
    assert pick_latest_snapshot(snaps([("16", 11), ("17", 10)])) == "2026-09-17 10:00:00"

    # 2) 半截采集（只拿到 1-3 条）必须回退到上一个完整快照
    for n in (1, 2, 3):
        assert pick_latest_snapshot(snaps([("16", 11), ("17", n)])) == "2026-09-16 10:00:00", n

    # 3) 正常推进：数量相同时取最新
    assert pick_latest_snapshot(snaps([("16", 11), ("17", 11)])) == "2026-09-17 10:00:00"

    # 4) 同一天多次采集取最后一次
    by = {"2026-09-17 10:00:00": [0] * 10, "2026-09-17 14:00:00": [0] * 10}
    assert pick_latest_snapshot(by) == "2026-09-17 14:00:00"

    # 5) 远端天窗被收窄等导致一次掉多条（≤TOLERANCE）时，仍必须取最新
    assert pick_latest_snapshot(snaps([("16", 11), ("17", 8)])) == "2026-09-17 10:00:00"

    print("snapshot_pick: 单元回归 5 组通过")


# 真实 FOMC 决议日（两日会议第 2 天），含 Fed 已公布的 2028 年日程。
# 来源：https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
FOMC_CALENDAR = [
    "2026-09-16", "2026-10-28", "2026-12-09",
    "2027-01-27", "2027-03-17", "2027-04-28", "2027-06-09",
    "2027-07-28", "2027-09-15", "2027-10-27", "2027-12-08",
    "2028-01-26", "2028-03-15", "2028-04-26", "2028-06-14",
    "2028-07-26", "2028-09-20", "2028-11-01", "2028-12-13",
]


def _simulate_rollover() -> None:
    """按真实 FOMC 日程模拟 2026-09-17 → 2028-11-01 的每日采集。

    核心断言（对应用户可见的故障）：**所选快照里绝不允许出现已过期的会议**，
    且选中的必须是当天最新的一次采集；未来三次会议随会议到期自然向前滚动。

    两种 FedWatch 行为都跑：① 到期后不补远端（会议数单调递减，最坏情况）；
    ② 会议数掉到 9 时补一场更远的（线上实际观察到的行为）。
    """
    from datetime import date as _date, timedelta as _td

    cal = [_date.fromisoformat(s) for s in FOMC_CALENDAR]
    first = _date(2026, 9, 15)          # 2026-09-16 那次会议尚未开完，表里 11 场
    start_meetings = [d for d in cal if _date(2026, 9, 16) <= d <= _date(2027, 12, 8)]
    assert len(start_meetings) == 11, len(start_meetings)

    checked = 0
    for backfill in (False, True):
        live = list(start_meetings)
        by_snap: dict[str, list] = {}
        day = first
        while day <= _date(2028, 11, 1):
            snap = f"{day.isoformat()} 10:00:00"
            table = [m for m in live if m >= day]      # 会议开完即从表中消失
            by_snap[snap] = table

            chosen = pick_latest_snapshot(by_snap)
            assert chosen == snap, f"{snap} 未选中最新快照（backfill={backfill}）"
            chosen_table = by_snap[chosen]
            # 用户可见的两个不变量：不含已过期会议；不会跳过最近那次会议
            assert all(m >= day for m in chosen_table), \
                f"{snap} 出现了已过期会议（backfill={backfill}）"
            if chosen_table:
                truth = min(m for m in cal if m >= day)
                assert chosen_table[0] == truth, \
                    f"{snap} 最近会议 {chosen_table[0]} != 真实最近会议 {truth}"
                if len(chosen_table) >= 3:
                    # 未来三次会议随会议到期向前滚动
                    assert chosen_table[:3] == sorted(m for m in cal if m >= day)[:3]

            if backfill and len(table) <= 9:
                nxt = [m for m in cal if m > table[-1]]
                if nxt:
                    live.append(nxt[0])
            day += _td(days=1)
            checked += 1

    print(f"snapshot_pick: 滚动模拟 2026-09-17→2028-11-01 共 {checked} 个采集日全部通过"
          "（无过期会议、始终取最新）")


if __name__ == "__main__":
    _selftest()
    _simulate_rollover()

