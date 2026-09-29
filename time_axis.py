#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
time_axis.py —— 时间轴口径的唯一权威实现。

职责：
  1. 定义「一个点 = 美东一个交易日的收盘状态」这一口径，以及它的落地规则；
  2. 把采集时刻 / 页面自带的 Data-as-of 戳翻译成 `us_trade_date`；
  3. 提供 CSV 的三个派生列（us_trade_date / data_asof_ct / time_basis）的
     写入口径、迁移（--migrate）与自检（--report / --selftest）。

------------------------------------------------------------------
定案（2026-09-17）
------------------------------------------------------------------
横轴改为**美东交易日**，采集挪到「美东收盘后的休市间隙」。去重采用 **A 方案**：
  一个点 = 一个美东交易日；周末三次采集落在同一格时，**只保留最早那一次**。

为什么必须是「收盘后的休市间隙」而不是随便某个时刻
--------------------------------------------------
CME Globex 利率期货的交易时段是 [D-1 17:00 CT, D 16:00 CT]（D 为交易日标签），
每天 16:00–17:00 CT 休市一小时；**周五 16:00 CT 收盘后要到周日 17:00 CT 才重开**。

  * 落在休市间隙 [16:00, 17:00) CT 采样 -> 读到的是 D 的**收盘定格值**
  * 落在时段内采样                     -> 读到的是**盘中读数**（旧北京 10:00 采集即此）

同一条曲线上混这两种值就是「口径不齐」。这才是真问题，**不是标签错位**
（旧标签 D = 采集瞬间正在进行中的交易日，时区算术上恰好等于北京日期）。

采集时刻（北京）与美东交易日的对应
---------------------------------
目标采样时刻 = **16:30 CT**（收盘后 30 分钟，给页面自身约 10–15 分钟的刷新延迟留余量）。
北京没有夏令时，但美东有，所以换算成北京会出现两套：

  | 美国时制            | 北京采集 | 折算到 CT      | 读到哪个交易日 |
  |---------------------|----------|----------------|----------------|
  | 夏令时 (3月第2周日~11月第1周日) | 05:30 | 前一日 16:30 | 前一日 |
  | 冬令时              | 06:30    | 前一日 16:30   | 前一日 |

因此 launchd 挂**两个**触发器（05:30 与 06:30），脚本用 `COLLECT_WINDOW` 自行判断
本次该不该跑：不在窗口内则**不启动浏览器、不落盘**，直接退出。两个触发器里每次
只会有一个真正工作，另一个是零成本的空转。**这比写死一个北京时刻更稳**：
写死 06:00 会在冬令时压在 16:00 CT 收盘整点上，写死 06:30 会在夏令时落在新时段开盘后。

周末重合（A 方案）
----------------
  周六北京 → 周五 16:30 CT：读到**周五收盘定格**（周末无新交易，最干净的一次）
  周日北京 → 周六 16:30 CT：同一份周五收盘，重复
  周一北京 → 周日 16:30 CT：同一份周五收盘，重复
三次都映射到 `us_trade_date = 周五`。A 方案 = 该交易日已有记录就跳过，
于是保留的正是**周六那次**（最早、且是收盘定格）。
每周因此固定 5 个点，与回填端（251 个日期全是美东交易日、从不重复）语义完全对齐。

标签从哪来（关键）
----------------
`us_trade_date` 由**采集时刻换算到 CT** 推出，**不依赖页面文案格式**：

    us_trade_date = (采集时刻 CT 的日期；若时刻 < 16:00 CT 则退一天) 再回溯到最近的美东交易日

页面自带的 `Data as of ... CT` 戳（`data_asof_ct`）只用于两件事：**新鲜度校验**与
**展示**（tooltip 上写「数据时点」）。它抓不到的代价是降级告警，而不是取不到数。

------------------------------------------------------------------
历史遗留（不回写、不粉饰）
------------------------------------------------------------------
CSV 现有 256 个快照分成两段，口径不同，迁移时**按原样标注、不改键**：

  | 段 | 行数 | snapshot_cn 填的是什么 | time_basis |
  |----|------|------------------------|------------|
  | 回填（snapshot_quikstrike 非空） | 2761 | QuikStrike 导出 CSV 的 Date 列 = **美东交易日** | settlement_export |
  | 实时（2026-09-11 ~ 09-17，5 个快照） | 54 | 北京采集时刻（周末折到周五） | legacy_intraday |

回填段本身就是美东交易日历，`us_trade_date` == 原日期，偏差 0。
实时段是**盘中读数**，`us_trade_date` 按原标签保留（它就是当时进行中的那个交易日），
但语义与前段不同 —— 报告里逐点标出，不假装连续。

`snapshot_cn` **不改**：它同时是 CSV 去重键、`events.csv` 的唯一关联键（要求逐字符一致）、
站点 URL 与 sitemap 的来源。改它要连带迁移全表 + 41 条事件 + 重发站点，收益不成比例。

用法：
    python3 time_axis.py --report     # 生成 docs/time-axis-mapping.md
    python3 time_axis.py --migrate    # 给 CSV 补三个派生列（幂等，自动备份）
    python3 time_axis.py --selftest   # 单元自检
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import sys
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CSV_PATH = os.path.join(DATA_DIR, "fedwatch_probabilities.csv")
SNAP_DIR = os.path.join(DATA_DIR, "snapshots")
EVENTS_PATH = os.path.join(DATA_DIR, "events.csv")
SIG_PATH = os.path.join(DATA_DIR, "significant_changes.csv")
DOCS_DIR = os.path.join(BASE_DIR, "docs")
REPORT_PATH = os.path.join(DOCS_DIR, "time-axis-mapping.md")

CN_TZ = ZoneInfo("Asia/Shanghai")
CT_TZ = ZoneInfo("America/Chicago")

# 本模块负责的三个派生列（CSV 尾部）。其他脚本请从这里 import，不要各写一份。
TIME_AXIS_FIELDS = ["us_trade_date", "data_asof_ct", "time_basis"]

# time_basis 取值
BASIS_PAGE = "page_asof"              # 新口径：美东收盘休市间隙采集，读到收盘定格
BASIS_EXPORT = "settlement_export"    # 回填：QuikStrike 导出 CSV 的 Date 列（美东交易日）
BASIS_LEGACY = "legacy_intraday"      # 旧实时段：北京 10:00 采集的盘中读数

# 美东时段边界（CT 墙钟）
SETTLE_START = time(16, 0)   # 收盘
SETTLE_END = time(17, 0)     # 下一交易时段开盘
# 采集容差窗：比休市间隙略窄，避免贴边
COLLECT_START = time(16, 2)
COLLECT_END = time(16, 58)
# 页面 Data-as-of 戳允许的滞后期（实测约 10–15 分钟）
ASOF_MAX_LAG = timedelta(minutes=75)

# launchd 的两个触发器（北京）及其覆盖的美东时制
SCHEDULE_CN = [
    ("05:30", "美国夏令时", "→ 前一日 16:30 CT"),
    ("06:30", "美国冬令时", "→ 前一日 16:30 CT"),
]

# ---------------------------------------------------------------- 交易日历

# 美股/CME 假日（含 observed 调整）。回填日期序列与这份表完全吻合，见 --report。
FIXED_HOLIDAYS = [(1, 1), (6, 19), (7, 4), (12, 25)]
NTH_WEEKDAY_HOLIDAYS = [
    (1, 0, 3),   # MLK：1 月第 3 个周一
    (2, 0, 3),   # Presidents：2 月第 3 个周一
    (5, 0, -1),  # Memorial：5 月最后一个周一
    (9, 0, 1),   # Labor Day：9 月第 1 个周一
    (11, 3, 4),  # Thanksgiving：11 月第 4 个周四
]


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    if n > 0:
        d = date(year, month, 1)
        d += timedelta(days=(weekday - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    nxt = date(year + (month == 12), (month % 12) + 1, 1)
    d = nxt - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    """固定日期假日遇周六提前到周五、遇周日顺延到周一。"""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def good_friday(year: int) -> date:
    """Easter - 2 天（Anonymous Gregorian algorithm）。"""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1) - timedelta(days=2)


def us_holidays(year: int) -> set[date]:
    """含 Good Friday 的近似假日集合（故意不含 Columbus Day / Veterans Day，
    因为债券与利率期货在那些日子照常交易，而回填序列里它们都出现过）。"""
    out = {_observed(date(year, m, d)) for m, d in FIXED_HOLIDAYS}
    out |= {_nth_weekday(year, m, w, n) for m, w, n in NTH_WEEKDAY_HOLIDAYS}
    out.add(good_friday(year))
    return out


def is_us_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in us_holidays(d.year)


def prev_us_trading_day(d: date) -> date:
    """返回 <= d 的最近一个美东交易日。"""
    while not is_us_trading_day(d):
        d -= timedelta(days=1)
    return d


# ---------------------------------------------------------------- 核心映射

def now_ct() -> datetime:
    return datetime.now(CT_TZ)


def us_trade_date_from_ct(t: datetime) -> date:
    """美东时刻 -> 该时刻对应的最后一个「已收盘」的美东交易日。

    规则：若墙钟时刻 < 16:00 CT（当日尚未收盘），先退一天，再回溯到交易日。
    在 COLLECT_WINDOW 内调用时退一天分支不会命中，它是为容错留的。
    """
    d = t.date()
    if t.time() < SETTLE_START:
        d -= timedelta(days=1)
    return prev_us_trading_day(d)


def in_collect_window(t: datetime) -> bool:
    """是否落在「收盘后的休市间隙」容差窗内（唯一决定该不该采集的硬条件）。"""
    return COLLECT_START <= t.time() < COLLECT_END


def collect_window_note(t: datetime) -> str:
    if in_collect_window(t):
        return "在采集窗口内"
    if t.time() < SETTLE_START:
        return f"{t:%H:%M} CT 早于收盘（16:00 CT），页面仍是盘中读数"
    if t.time() < COLLECT_START:
        return f"{t:%H:%M} CT 太贴收盘整点，未给页面刷新留余量"
    return f"{t:%H:%M} CT 已过 17:00，新交易时段已开盘，读到的不再是收盘定格"


ASOF_RE_CN = re.compile(
    r"(\d{1,2})\s*(\d{1,2})\s*月\s*(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})")
ASOF_RE_EN = re.compile(
    r"([A-Za-z]{3})[A-Za-z]*\.?\s+(\d{1,2}),\s*(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})")
# 规范化后的形式（CSV 里存的就是这个），便于二次解析与人工读
ASOF_RE_ISO = re.compile(
    r"(\d{4})-(\d{1,2})-(\d{1,2})[ T](\d{1,2}):(\d{2}):(\d{2})")
MONTHS_EN = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def parse_page_asof(raw: str | None) -> datetime | None:
    """解析数据时点，返回 CT 墙钟时刻。

    接受三种输入，所以「页面原文」与「CSV 规范化值」都能直接喂进来：
        * Data as of 17 9月 2026 02:33:31 CT      ← 页面默认视图原文（实测格式）
        * Data as of Sep 17, 2026 02:33:31 CT     ← 英文兜底
        2026-09-17 02:33:31 CT                    ← 本模块规范化后的形式
    """
    if not raw:
        return None
    s = str(raw).strip()
    m = ASOF_RE_ISO.search(s)
    if m:
        y, mo, d, hh, mm, ss = (int(x) for x in m.groups())
    else:
        m = ASOF_RE_CN.search(s)
        if m:
            d, mo, y, hh, mm, ss = (int(x) for x in m.groups())
        else:
            m = ASOF_RE_EN.search(s)
            if not m:
                return None
            mon_s, d, y, hh, mm, ss = m.groups()
            mo = MONTHS_EN.get(mon_s.lower()[:3])
            if mo is None:
                return None
            d, y, hh, mm, ss = int(d), int(y), int(hh), int(mm), int(ss)
    try:
        return datetime(y, mo, d, hh, mm, ss, tzinfo=CT_TZ)
    except ValueError:
        return None


def normalize_asof(raw: str | None) -> str:
    """把页面原文规范化成 `YYYY-MM-DD HH:MM:SS CT`；解析不了就返回空串。

    空串是明确语义：**没有可信的数据时点**，不编一个填进去。
    """
    t = parse_page_asof(raw)
    return f"{t:%Y-%m-%d %H:%M:%S} CT" if t else ""


def asof_freshness(asof: datetime | None, now: datetime) -> tuple[bool, str]:
    """页面数据戳是否新鲜（不是昨天的残留）。

    只做**降级警告**，不阻断采集 —— 收盘口径的硬保证来自
    `in_collect_window(now)`，不依赖页面文案能否解析。
    """
    if asof is None:
        return False, "未抓到页面 Data-as-of 戳（切到 Aggregated 后该节点会消失）"
    lag = now - asof
    if lag < timedelta(0):
        return False, f"页面数据戳 {asof:%H:%M:%S} CT 晚于本机时刻，两钟不同步"
    if lag > ASOF_MAX_LAG:
        return False, f"页面数据滞后 {lag.total_seconds()/60:.0f} 分钟（阈值 {ASOF_MAX_LAG.seconds//60}）"
    return True, f"滞后 {lag.total_seconds()/60:.0f} 分钟"


def asof_is_post_close(asof: datetime | None) -> bool:
    """数据戳本身是否已经落在当日收盘之后（信息性，不用于阻断）。"""
    return asof is not None and asof.time() >= SETTLE_START


def news_window_et(prev_cn: str | None, cur_cn: str) -> str:
    """两次采集之间需要检索新闻的美东时间窗（左开右闭），供归因使用。"""
    cur = us_day_of_snapshot(cur_cn)
    if prev_cn is None:
        return f"美东 {cur} 全天"
    prev = us_day_of_snapshot(prev_cn)
    return f"美东 {prev} 收盘 → {cur} 收盘"


def parse_cn(s: str) -> datetime | None:
    try:
        return datetime.strptime(s.strip()[:19], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, AttributeError):
        return None


# ---------------------------------------------------------------- 读数据

def load_rows() -> list[dict]:
    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def captured_map() -> dict[str, datetime]:
    """从 snapshots/*.json 读出 `snapshot_cn -> 真实北京采集时刻`。

    CSV 的 snapshot_cn 是整分钟、且历史上被折过周末，真实时刻只存在 JSON 里。
    """
    out: dict[str, datetime] = {}
    if not os.path.isdir(SNAP_DIR):
        return out
    for name in sorted(os.listdir(SNAP_DIR)):
        if not name.endswith(".json"):
            continue
        try:
            d = json.load(open(os.path.join(SNAP_DIR, name), encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        key = d.get("snapshot_cn") or d.get("effective_snapshot_cn")
        cap = parse_cn(d.get("captured_at_cn") or "")
        k = parse_cn(key or "")
        if k and cap:
            out[k.strftime("%Y-%m-%d %H:%M:%S")] = cap
    return out


def us_day_of_snapshot(snapshot_cn: str) -> str:
    """快照的 us_trade_date：优先读 CSV 派生列，缺失时退回原标签日期。"""
    rows = _ROWS_CACHE if _ROWS_CACHE is not None else load_rows()
    for r in rows:
        if r.get("snapshot_cn") == snapshot_cn:
            return (r.get("us_trade_date") or "").strip() or snapshot_cn[:10]
    return snapshot_cn[:10]


_ROWS_CACHE: list[dict] | None = None


def set_rows_cache(rows: list[dict]) -> None:
    global _ROWS_CACHE
    _ROWS_CACHE = rows


def basis_of(row: dict) -> str:
    return (row.get("time_basis") or "").strip() or (
        BASIS_EXPORT if (row.get("snapshot_quikstrike") or "").strip() else BASIS_LEGACY)


def asof_label(row: dict) -> str:
    """站点副标签：让读者一眼看出这个点是什么口径的值。"""
    basis = basis_of(row)
    if basis == BASIS_PAGE:
        t = parse_page_asof(row.get("data_asof_ct"))
        return f"{t:%H:%M}CT" if t else "收盘"
    if basis == BASIS_EXPORT:
        return "结算"
    return "盘中"


def snapshot_table() -> list[dict]:
    """逐快照给出：现状标签 / 真实采集时刻 / us_trade_date / 口径 / 偏差。"""
    rows = load_rows()
    set_rows_cache(rows)
    caps = captured_map()
    by_snap: dict[str, dict] = {}
    for r in rows:
        s = r["snapshot_cn"]
        e = by_snap.setdefault(s, {"n": 0, "basis": basis_of(r),
                                   "us": (r.get("us_trade_date") or "").strip(),
                                   "asof": (r.get("data_asof_ct") or "").strip()})
        e["n"] += 1

    out = []
    for s in sorted(by_snap):
        info = by_snap[s]
        label = parse_cn(s)
        assert label is not None
        us = info["us"] or s[:10]
        us_d = date.fromisoformat(us)
        real_cap = caps.get(s, label)
        out.append({
            "snapshot_cn": s,
            "basis": info["basis"],
            "is_backfill": info["basis"] == BASIS_EXPORT,
            "captured_cn": real_cap,
            "captured_from_json": s in caps,
            "label_date": label.date(),
            "us_trade_date": us_d,
            "delta_days": (label.date() - us_d).days,
            "asof": info["asof"],
            "n_meetings": info["n"],
        })

    prev = None
    for row in out:
        row["news_window"] = news_window_et(prev, row["snapshot_cn"])
        prev = row["snapshot_cn"]
    return out


# ---------------------------------------------------------------- 迁移

def migrate(write: bool = True) -> dict:
    """给 CSV 补 TIME_AXIS_FIELDS 三列。幂等：已有值的行不动。"""
    rows = load_rows()
    if not rows:
        return {"ok": False, "reason": "CSV 为空"}
    fields = list(rows[0].keys())
    added_cols = [f for f in TIME_AXIS_FIELDS if f not in fields]
    stats = {"total": len(rows), "by_basis": {}, "already": 0}
    for r in rows:
        if (r.get("us_trade_date") or "").strip():
            stats["already"] += 1
            stats["by_basis"][basis_of(r)] = stats["by_basis"].get(basis_of(r), 0) + 1
            continue
        basis = BASIS_EXPORT if (r.get("snapshot_quikstrike") or "").strip() else BASIS_LEGACY
        r["us_trade_date"] = (r.get("snapshot_cn") or "")[:10]
        r["data_asof_ct"] = ""          # 历史上没有可信的数据时点，留空而不是编一个
        r["time_basis"] = basis
        stats["by_basis"][basis] = stats["by_basis"].get(basis, 0) + 1
    # 保证列存在（即使一行都不需要改）
    for r in rows:
        for f in TIME_AXIS_FIELDS:
            r.setdefault(f, "")
    if not write:
        stats["added_cols"] = added_cols
        return {"ok": True, "dry": True, **stats}
    if added_cols:
        backup = CSV_PATH + f".bak-{datetime.now():%Y%m%d%H%M%S}"
        shutil.copy2(CSV_PATH, backup)
        stats["backup"] = os.path.basename(backup)
    with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields + added_cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    stats["added_cols"] = added_cols
    return {"ok": True, **stats}


def verify() -> tuple[bool, list[str]]:
    """迁移后的不变量自检。"""
    rows = load_rows()
    set_rows_cache(rows)
    msgs = []
    bad = [r for r in rows if not (r.get("us_trade_date") or "").strip()]
    msgs.append(f"缺少 us_trade_date 的行：{len(bad)} / {len(rows)}")
    offdays = [r for r in rows
               if (r.get("us_trade_date") or "").strip()
               and not is_us_trading_day(date.fromisoformat(r["us_trade_date"]))]
    msgs.append(f"us_trade_date 落在非交易日的行：{len(offdays)}")
    counts = {b: sum(1 for r in rows if basis_of(r) == b)
              for b in (BASIS_PAGE, BASIS_EXPORT, BASIS_LEGACY)}
    msgs.append("口径分布：" + "，".join(f"{k}={v}" for k, v in counts.items()))
    keys = {(r["snapshot_cn"], r["meeting_date"]) for r in rows}
    msgs.append(f"(snapshot_cn, meeting_date) 唯一性：{len(keys)} / {len(rows)}"
                + ("（重复！）" if len(keys) != len(rows) else ""))
    ok = not bad and not offdays and len(keys) == len(rows)
    return ok, msgs


# ---------------------------------------------------------------- 报告

def _calibration(table: list[dict]) -> list[str]:
    back = [r["label_date"] for r in table if r["is_backfill"]]
    model = []
    d = back[0]
    while d <= back[-1]:
        if is_us_trading_day(d):
            model.append(d)
        d += timedelta(days=1)
    hol = sorted(us_holidays(2025) | us_holidays(2026))
    inside = [x for x in hol if back[0] <= x <= back[-1]]
    hit = [x for x in inside if x in set(back)]
    L = ["## 一、口径校准：回填端就是美东交易日历", ""]
    L.append(f"- 回填日期数 **{len(back)}**，范围 `{back[0]}` → `{back[-1]}`")
    L.append(f"- 用「工作日 − 美股假日」模型独立重算得 **{len(model)}** 天，"
             f"逐日比对：**{'完全一致' if model == back else '不一致'}**")
    L.append(f"- 落在周六/周日的回填日期：**{sum(1 for x in back if x.weekday() >= 5)}** 天")
    L.append(f"- 区间内美股假日 {len(inside)} 个，出现在回填序列里的：**{len(hit)}** 个")
    L.append("")
    L.append("> 结论：回填行的日期就是**美东交易日**本身，不是北京日期；"
             "这一段的 `us_trade_date` == 原标签，偏差 0。")
    L.append("")
    return L


def _rule() -> list[str]:
    return [
        "## 二、定案：口径与采样时刻",
        "",
        "**一个点 = 美东一个交易日的收盘状态。** 采样必须落在 CME 每天的休市间隙",
        "`[16:00, 17:00) CT` —— 此时上一个交易日刚收盘定格，新时段尚未开盘。",
        "",
        "```",
        "us_trade_date = (采集时刻换算到 CT 的日期；若时刻 < 16:00 CT 则退一天)",
        "                再回溯到最近的美东交易日",
        "```",
        "",
        "| 美国时制 | 北京采集 | 折算到 CT | 读到哪个交易日 |",
        "|---|---|---|---|",
        "| 夏令时（3 月第 2 周日 ~ 11 月第 1 周日） | **05:30** | 前一日 16:30 | 前一日 |",
        "| 冬令时 | **06:30** | 前一日 16:30 | 前一日 |",
        "",
        "两个触发器都挂在 launchd 上，脚本用 `COLLECT_WINDOW = [16:02, 16:58) CT` 自行判断，",
        "不在窗内则**不启动浏览器、不落盘**。所以每次只有一个真正工作，另一个是零成本空转。",
        "",
        "> 为什么不用单一固定时刻：北京无夏令时、美东有，固定 06:00 会在冬令时压在",
        "> 16:00 CT 收盘整点上；固定 06:30 会在夏令时落到 17:30 CT（新时段已开盘）。",
        "",
        "### 周末重合的去重（A 方案）",
        "",
        "| 北京采集 | 折算到 CT | 读到 | 映射的交易日 | 处置 |",
        "|---|---|---|---|---|",
        "| 周六 | 周五 16:30 | **周五收盘定格**（周末无新交易，最干净） | 周五 | **保留（最早）** |",
        "| 周日 | 周六 16:30 | 同一份周五收盘 | 周五 | 跳过（已存在） |",
        "| 周一 | 周日 16:30 | 同一份周五收盘 | 周五 | 跳过（已存在） |",
        "",
        "A 方案 = 该 `us_trade_date` 已有记录就跳过 → 每周固定 5 个点，",
        "与回填端语义（全是美东交易日、从不重复）完全对齐。",
        "",
        "> CME Globex 利率期货：周一~周四 17:00 CT 开下一交易日时段，",
        "> **周五 16:00 CT 收盘后要到周日 17:00 CT 才重开**，所以周末读到的是冻结的周五收盘。",
        "",
    ]


def _live_table(table: list[dict]) -> list[str]:
    live = [r for r in table if r["basis"] != BASIS_EXPORT]
    L = ["## 三、实时段逐点对照（历史遗留，不回写）", "",
         "| snapshot_cn | 真实采集时刻(北京) | 口径 | 现标签日期 | us_trade_date | 数据时点 |",
         "|---|---|---|---|---|---|"]
    for r in live:
        L.append(f"| `{r['snapshot_cn']}` | {r['captured_cn']:%Y-%m-%d %H:%M}"
                 f"{'' if r['captured_from_json'] else ' *'} | {r['basis']} | "
                 f"{r['label_date']} | **{r['us_trade_date']}** | {r['asof'] or '—'} |")
    L += ["", "\\* 该快照没有对应 JSON，真实采集时刻用 snapshot_cn 兜底。",
          "",
          "这一段的 `us_trade_date` 按**原标签**保留：`legacy_intraday` 的标签在时区算术上",
          "本来就是「采集瞬间正在进行中的那个交易日」，**没有错位**。",
          "真正的差别是**口径**：这 5 个点是北京 10:00 的**盘中读数**，",
          "而新口径是美东收盘定格。回填段（前 2761 行）偏差全部为 0。", ""]
    return L


def _junction(table: list[dict]) -> list[str]:
    L = ["## 四、回填 / 实时 / 新口径 交界处", "",
         "| snapshot_cn | 口径 | us_trade_date | 数据时点 |", "|---|---|---|---|"]
    idx = max(i for i, r in enumerate(table) if r["is_backfill"])
    for r in table[max(0, idx - 2): idx + 2]:
        L.append(f"| `{r['snapshot_cn']}` | {r['basis']} | {r['us_trade_date']} | "
                 f"{r['asof'] or '—'} |")
    L += ["", "切到新口径后，采集点会比原标签**早一天出现在横轴上**：",
          "北京 09-18 05:30 采集 → 横轴显示**美东 09-17**。这是对的，不是数据延迟。", ""]
    return L


def _events(table: list[dict]) -> list[str]:
    idx = {r["snapshot_cn"]: r for r in table}
    L = ["## 五、已有事件归因的检索日", "",
         "`events.csv` 的 `snapshot_cn` 是唯一关联键，**不要改它**；"
         "下面是它应当归属的美东交易日与检索窗。", "",
         "| snapshot_cn | 现展示日期 | us_trade_date | 应检索的美东新闻窗 | direction | summary |",
         "|---|---|---|---|---|---|"]
    if os.path.exists(EVENTS_PATH):
        with open(EVENTS_PATH, encoding="utf-8-sig", newline="") as f:
            evs = list(csv.DictReader(f))
        for e in sorted(evs, key=lambda x: x.get("snapshot_cn", "")):
            s = (e.get("snapshot_cn") or "").strip()
            r = idx.get(s)
            L.append(f"| `{s}` | {s[:10]} | **{r['us_trade_date'] if r else '-'}** | "
                     f"{r.get('news_window', '-') if r else '-'} | "
                     f"{e.get('direction','')} | {(e.get('summary') or '')[:36]} |")
    L.append("")
    return L


def _pending(table: list[dict]) -> list[str]:
    idx = {r["snapshot_cn"]: r for r in table}
    L = ["## 六、当前待归因事件的检索日", "",
         "`significant_changes.csv` 里 `annotated=0` 的行：", "",
         "| snapshot_cn | meeting | Δpp | 应检索的美东新闻窗 |", "|---|---|---|---|"]
    n = 0
    if os.path.exists(SIG_PATH):
        with open(SIG_PATH, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                if r.get("annotated") == "1":
                    continue
                n += 1
                s = r["snapshot_cn"]
                row = idx.get(s, {})
                L.append(f"| `{s}` | {r['meeting_date']} | {r['delta_pp']} | "
                         f"{row.get('news_window','-')} |")
    if not n:
        L.append("| — | — | — | 无 |")
    L.append("")
    return L


def build_report() -> str:
    table = snapshot_table()
    ok, msgs = verify()
    now = now_ct()
    L = ["# FedWatch 时间轴口径对照表", "",
         f"生成时间：{datetime.now():%Y-%m-%d %H:%M}（北京）"
         f" / {now:%Y-%m-%d %H:%M} CT", "",
         "> **定案（2026-09-17）**：横轴改用**美东交易日**；采集挪到美东收盘后的休市间隙",
         "> （北京 05:30 夏令时 / 06:30 冬令时）；周末三次采集冷落到同一交易日时",
         "> 采用 **A 方案：只保留最早那一次**。",
         ">",
         "> 旧结论「实时段标签错位一天」已被推翻并撤回：北京 10:00 采集的标签在时区算术上",
         "> 恰好等于当时进行中的交易日，**没有错位**；真问题是**同一条曲线上混了两种口径的值**",
         "> （周末采到收盘定格、工作日采到盘中读数）。详见 `time_axis.py` 文档头。",
         "",
         f"**迁移自检**：{'通过' if ok else '未通过'} —— " + "；".join(msgs), ""]
    L += _calibration(table)
    L += _rule()
    L += _live_table(table)
    L += _junction(table)
    L += _events(table)
    L += _pending(table)
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- 自检

def _selftest() -> None:
    # 1) 采集窗口判定
    assert in_collect_window(datetime(2026, 9, 17, 16, 30, tzinfo=CT_TZ))
    assert not in_collect_window(datetime(2026, 9, 17, 15, 30, tzinfo=CT_TZ))
    assert not in_collect_window(datetime(2026, 9, 17, 17, 30, tzinfo=CT_TZ))
    assert not in_collect_window(datetime(2026, 9, 17, 16, 59, tzinfo=CT_TZ))

    # 2) 交易日映射：工作日取当天，周末回溯到周五
    assert us_trade_date_from_ct(datetime(2026, 9, 17, 16, 30, tzinfo=CT_TZ)) == date(2026, 9, 17)
    assert us_trade_date_from_ct(datetime(2026, 9, 19, 16, 30, tzinfo=CT_TZ)) == date(2026, 9, 18)  # 周六→周五
    assert us_trade_date_from_ct(datetime(2026, 9, 20, 16, 30, tzinfo=CT_TZ)) == date(2026, 9, 18)  # 周日→周五
    assert us_trade_date_from_ct(datetime(2026, 9, 21, 16, 30, tzinfo=CT_TZ)) == date(2026, 9, 21)  # 周一→周一
    # 未收盘分支 + 假日回溯
    assert us_trade_date_from_ct(datetime(2026, 9, 17, 10, 0, tzinfo=CT_TZ)) == date(2026, 9, 16)
    assert us_trade_date_from_ct(datetime(2026, 9, 7, 16, 30, tzinfo=CT_TZ)) == date(2026, 9, 4)   # 09-07 劳动节 → 回溯到 09-04 周五

    # 3) 北京 05:30 / 06:30 在两种时制下都落在 16:30 CT
    cn_dst = datetime(2026, 9, 18, 5, 30, tzinfo=CN_TZ).astimezone(CT_TZ)
    assert (cn_dst.hour, cn_dst.minute) == (16, 30), cn_dst
    assert cn_dst.date() == date(2026, 9, 17)
    cn_std = datetime(2026, 12, 18, 6, 30, tzinfo=CN_TZ).astimezone(CT_TZ)
    assert (cn_std.hour, cn_std.minute) == (16, 30), cn_std
    assert cn_std.date() == date(2026, 12, 17)
    # 反例：写死 06:00 / 06:30 的另一半会落在哪
    assert datetime(2026, 12, 18, 6, 0, tzinfo=CN_TZ).astimezone(CT_TZ).hour == 16
    assert datetime(2026, 9, 18, 6, 30, tzinfo=CN_TZ).astimezone(CT_TZ).hour == 17
    # 两个触发器各自只在其中一个时制上命中采集窗口（这就是挂两个的理由）
    hit = {}
    for hhmm, _, _ in SCHEDULE_CN:
        h, m = (int(x) for x in hhmm.split(":"))
        for season, probe in (("夏令时", datetime(2026, 9, 18, h, m, tzinfo=CN_TZ)),
                              ("冬令时", datetime(2026, 12, 18, h, m, tzinfo=CN_TZ))):
            ct = probe.astimezone(CT_TZ)
            if in_collect_window(ct):
                hit.setdefault(hhmm, []).append(season)
    assert hit == {"05:30": ["夏令时"], "06:30": ["冬令时"]}, hit
    # 4) 页面时间戳解析（页面原文 / 英文 / 规范化 三种输入）
    t = parse_page_asof("* Data as of 17 9月 2026 02:33:31 CT")
    assert t == datetime(2026, 9, 17, 2, 33, 31, tzinfo=CT_TZ), t
    assert parse_page_asof("Data as of Sep 17, 2026 02:33:31 CT").date() == date(2026, 9, 17)
    assert parse_page_asof("2026-09-17 02:33:31 CT") == t
    assert parse_page_asof("") is None and parse_page_asof(None) is None
    assert parse_page_asof("Data as of 1 12月 2026 16:05:00 CT").month == 12
    assert normalize_asof("* Data as of 17 9月 2026 02:33:31 CT") == "2026-09-17 02:33:31 CT"
    assert normalize_asof("垃圾输入") == ""
    # 5) 新鲜度
    n = datetime(2026, 9, 17, 16, 30, tzinfo=CT_TZ)
    assert asof_freshness(datetime(2026, 9, 17, 16, 19, tzinfo=CT_TZ), n)[0]
    assert not asof_freshness(datetime(2026, 9, 16, 16, 19, tzinfo=CT_TZ), n)[0]
    assert not asof_freshness(None, n)[0]
    assert asof_is_post_close(datetime(2026, 9, 17, 16, 19, tzinfo=CT_TZ))
    assert not asof_is_post_close(datetime(2026, 9, 17, 15, 19, tzinfo=CT_TZ))
    print("time_axis: 自检全部通过")


def main() -> int:
    if "--selftest" in sys.argv:
        _selftest()
        return 0
    if "--migrate" in sys.argv:
        dry = "--dry" in sys.argv
        st = migrate(write=not dry)
        if not st.get("ok"):
            print(f"迁移失败：{st.get('reason')}")
            return 1
        print(("（dry-run）" if dry else "") + f"迁移完成：共 {st['total']} 行")
        print("  口径分布：" + "，".join(f"{k}={v}" for k, v in sorted(st["by_basis"].items())))
        print(f"  新增列：{st['added_cols'] or '（已存在，未改）'}")
        if st.get("backup"):
            print(f"  备份：data/{st['backup']}")
        if dry:
            print("自检：（dry-run 未落盘，跳过）")
            return 0
        ok, msgs = verify()
        print(f"自检：{'通过' if ok else '未通过'}")
        for m in msgs:
            print("  " + m)
        return 0 if ok else 1
    if "--report" in sys.argv:
        os.makedirs(DOCS_DIR, exist_ok=True)
        with open(REPORT_PATH, "w", encoding="utf-8") as f:
            f.write(build_report())
        table = snapshot_table()
        live = [r for r in table if r["basis"] != BASIS_EXPORT]
        print(f"报告已写入 {os.path.relpath(REPORT_PATH, BASE_DIR)}")
        print(f"快照总数 {len(table)}（回填 {len(table)-len(live)} / 实时 {len(live)}）")
        now = now_ct()
        print(f"当前 CT {now:%Y-%m-%d %H:%M}：{collect_window_note(now)}"
              f"（采集窗口 {COLLECT_START:%H:%M}–{COLLECT_END:%H:%M} CT）")
        print()
        print("实时段映射：")
        for r in live:
            print(f"  {r['snapshot_cn']}  {r['basis']:<18} → us_trade_date "
                  f"{r['us_trade_date']}  ({r['delta_days']:+d} 天)")
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
