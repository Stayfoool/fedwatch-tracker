#!/usr/bin/env python3
"""
backfill_history.py — 用 agent-browser 内部会话去拉 QuikStrike
AllMeetings.aspx 的 CSV（Agg 历史下载），落到 data/history/all_meetings.csv，
再用 Python 解析为每日 × 会议 → max_range_label / max_range_pct，append 到
data/fedwatch_probabilities.csv。

历史跨度（CME FedWatch Tool 用户手册明示）：
  "在本栏的'下载'部分，可下载所有会议日期原始的历史可能性数据"

实测：可用 1 年（约 251 个交易日），从抓取当日往前回溯。

输出：
  data/history/all_meetings_<YYYYMMDD>.csv
  与
  追加 data/fedwatch_probabilities.csv（去重 on (snapshot_cn, meeting_date)）

注意：
  - 此脚本需 agent-browser 已经处于 QuikStrike 会话内（先 open QuikStrike）。
  - 仅在历史回填场景运行，平时不要调用；它会一次性追加大批量 row。
"""
from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
HIST_DIR = DATA / "history"
CSV_PATH = DATA / "fedwatch_probabilities.csv"
SNAP_DIR = DATA / "snapshots"

HIST_DIR.mkdir(parents=True, exist_ok=True)

QUIKSTRIKE_URL = (
    "https://cmegroup-tools.quikstrike.net/User/QuikStrikeTools.aspx"
    "?viewitemid=IntegratedFedWatchTool"
)
CME_REFERER = "https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html"
HEADERS_JSON = json.dumps({"Referer": CME_REFERER})

QUIK_URL = (
    "https://cmegroup-tools.quikstrike.net/User/Export/FedWatch/AllMeetings.aspx"
)

# Our standard aggregate column set (we currently use these in the dashboard).
# 历史文件中可能出现的额外列（绝对 bps 桶 (0-25) 到 (1550-1575)）会被丢弃，
# 仅保留这 7 个区间到 CSV。
RANGES = [
    "325-350", "350-375", "375-400", "400-425",
    "425-450", "450-475", "475-500",
]
CUR_TARGET_LO = 350
CUR_TARGET_HI = 375
CURRENT_TARGET = f"{CUR_TARGET_LO}-{CUR_TARGET_HI}"

# 与现有 daily CSV 一致
CSV_FIELDS = ["snapshot_cn", "snapshot_quikstrike",
              "meeting_date", "current_target",
              "agg_p_hike_pct", "agg_p_hold_pct", "agg_p_cut_pct",
              "max_range_label", "max_range_pct",
              "aggregated_ranges"] + [
    f"range_{r.replace('-', '_')}_pct" for r in RANGES
]

# AllMeetings.csv 的会议列块顺序（按"未来"顺序，第一个块 = 最近的上次会议后的下一次会议）
# 来自 cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html 与
# QuikStrike FedWatch Tool 当前 Download 页确认。
EXPECTED_MEETINGS = [
    "2026-09-16", "2026-10-28", "2026-12-09", "2027-01-27", "2027-03-17",
    "2027-04-28", "2027-06-09", "2027-07-28", "2027-09-15",
    "2027-10-27", "2027-12-08",
]

# Bejing timezone for snapshot_cn formatting.
CN_TZ = timezone(timedelta(hours=8))


def ab_eval(js: str, timeout: int = 60) -> str:
    """在 agent-browser 当前页面的 JS context 里跑一段代码，返回其字符串结果。"""
    proc = subprocess.run(
        ["agent-browser", "eval", js],
        capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"agent-browser eval 失败: {proc.stderr.strip()}")
    out = proc.stdout.strip()
    if out.startswith('"') and out.endswith('"'):
        # JSON-quoted string -> unquote
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return out[1:-1]
    return out


def ensure_quikstrike_session():
    """
    确保 agent-browser 已打开 QuikStrike 页面（否则 Downloads / Export 因无 session cookie
    而被拒）。先尝试 eval 一个探针；失败则 close 再 open。
    """
    probe = "(function(){return location.hostname||'none';})()"
    try:
        host = ab_eval(probe, timeout=20)
    except Exception:
        host = "ERR"
    if "quikstrike" not in host:
        print("  agent-browser 尚未在 QuikStrike 页面，自动打开 …")
        subprocess.run(["agent-browser", "close"], capture_output=True, timeout=30)
        subprocess.run(
            ["agent-browser", "open", QUIKSTRIKE_URL,
             "--headers", HEADERS_JSON],
            capture_output=True, text=True, timeout=120,
        )
        # 等 JS 渲染
        time.sleep(6)


def fetch_all_meetings_csv() -> str:
    """通过 agent-browser 页面 fetch QuikStrike 历史 CSV（Agg AllMeetings）。"""
    js = (
        "(function(){"
        "return fetch('" + QUIK_URL + "', {credentials: 'include'})"
        ".then(function(r){if(!r.ok)throw new Error('http '+r.status);"
        "return r.text();})"
        ".then(function(t){return t;})"
        ".catch(function(e){return 'ERR:'+e;});"
        "})()"
    )
    out = ab_eval(js, timeout=180)
    if out.startswith("ERR:"):
        raise RuntimeError("抓 AllMeetings.csv 失败: " + out)
    return out


def parse_meeting_blocks(hdr0: str, hdr1: str):
    """
    用正则匹配 row 0 里的 "History for X Fed meeting" 标记。
    row 0 中若干 meeting 字符串会因 CSV 转义合并到一个 cell 里（无逗号分隔），
    所以直接对原字符串扫所有匹配，再把字符位置映射到 cell 索引（数逗号）。

    然后按 cell 索引把 row 1 的 bps 桶 cell 分组给对应 meeting。
    """
    from datetime import datetime as _dt

    mtg_re = re.compile(r"History for (?P<label>[\d A-Za-z]+?) Fed meeting")
    matches = list(mtg_re.finditer(hdr0))
    if not matches:
        return []

    cells1 = hdr1.split(",")
    n = len(cells1)
    n_h0 = len(hdr0.split(","))
    n = min(n, n_h0)

    # 对每个 meeting 找到它在 cell 数组中的 cell 索引：
    # 它之前有 N 个逗号（每 1 个逗号 = 跨过 1 个 cell）。
    cell_to_meeting = [-1] * n
    meeting_labels = []
    for idx, m in enumerate(matches):
        label = m.group("label").strip()
        meeting_labels.append(label)
        cell_idx = hdr0[:m.start()].count(",")
        if 0 <= cell_idx < n:
            cell_to_meeting[cell_idx] = idx

    # 把无主 cell 归属到上一个 meeting（向前填）
    cur = -1
    for i in range(n):
        if cell_to_meeting[i] >= 0:
            cur = cell_to_meeting[i]
        elif cur >= 0:
            cell_to_meeting[i] = cur

    # 解析 meeting 日期
    n_mtg = len(meeting_labels)
    parsed_dates = []
    for lbl in meeting_labels:
        try:
            d = _dt.strptime(lbl, "%d %b %Y")
            parsed_dates.append(d.strftime("%Y-%m-%d"))
        except ValueError:
            parsed_dates.append(None)

    # 把 row1 的 bps 桶按 cell 归属分组
    blocks_data = [[] for _ in range(n_mtg)]
    for i in range(1, n):  # skip Date cell
        idx = cell_to_meeting[i]
        if idx < 0:
            continue
        c = cells1[i].strip().strip("\r").strip("\n")
        m = re.match(r"\((\d+)\-(\d+)\)", c)
        if m:
            blocks_data[idx].append((c, int(m.group(1)), int(m.group(2))))

    blocks = []
    for i in range(n_mtg):
        if parsed_dates[i]:
            blocks.append({"md": parsed_dates[i], "cols": blocks_data[i]})
    return blocks


def main():
    print("→ 从 QuikStrike (agent-browser 内部会话) 抓 AllMeetings.csv …")
    ensure_quikstrike_session()
    csv_text = fetch_all_meetings_csv()
    saved_csv = HIST_DIR / "all_meetings.csv"
    saved_csv.write_text(csv_text, encoding="utf-8")
    print(f"  保存原始 CSV → {saved_csv}  ({len(csv_text):,} chars)")
    # 清理早期带时间戳的历史文件（避免堆积）
    for old in HIST_DIR.glob("all_meetings_2*.csv"):
        try:
            old.unlink()
        except OSError:
            pass

    lines = [ln for ln in csv_text.split("\n") if "," in ln]
    if len(lines) < 3:
        print("FAIL: CSV 内容过短"); sys.exit(1)
    hdr0, hdr1 = lines[0], lines[1]
    data_lines = lines[2:]

    blocks = parse_meeting_blocks(hdr0, hdr1)
    print(f"  检测到 {len(blocks)} 个会议区段 in CSV")
    for b in blocks:
        print(f"    {b['md']}  ({len(b['cols'])} 桶)")

    if not blocks:
        print("FAIL: 未找到任何会议区段"); sys.exit(2)

    # 建立 RANGES 桶的快速定位表
    rng_pos = []
    for b in blocks:
        rp = {}
        for off, (lbl, lo, hi) in enumerate(b["cols"]):
            k = f"{lo}-{hi}"
            if k in RANGES:
                rp[k] = off
        rng_pos.append(rp)

    # Process each date row
    new_rows = []
    n_dates = 0
    date_indices = []
    for ln in data_lines:
        cells = ln.rstrip("\r").split(",")
        if not cells or not cells[0].strip():
            continue
        date_str = cells[0].strip()
        try:
            d = datetime.strptime(date_str, "%Y/%m/%d")
        except ValueError:
            continue
        snapshot_cn = d.strftime("%Y-%m-%d") + " 10:00:00"
        snapshot_quikstrike = date_str + " 23:59:59 CT"
        n_dates += 1
        date_indices.append(date_str)

        # offset of each meeting's first col in the row1 array (skip Date col)
        col_offset = 1

        for i, m in enumerate(blocks):
            md_date = datetime.strptime(m["md"], "%Y-%m-%d").date()
            if md_date < d.date():
                col_offset += len(m["cols"])
                continue  # 会议已过；该历史点此会议无数据
            n_cols = len(m["cols"])

            # max range
            peak_label, peak_pct = "", 0.0
            for off, (lbl, lo, hi) in enumerate(m["cols"]):
                v_raw = cells[col_offset + off]
                try:
                    v = float(v_raw) if v_raw not in ("", None) else 0.0
                except ValueError:
                    v = 0.0
                if v > peak_pct:
                    peak_label = f"{lo}-{hi}"  # 去掉 AllMeetings CSV 的括号，统一为 fetch_quikstrike 的 '375-400' 形式
                    peak_pct = v

            # aggregated ranges
            rng_probs = {r: 0.0 for r in RANGES}
            for r in RANGES:
                off = rng_pos[i].get(r)
                if off is None:
                    continue
                v_raw = cells[col_offset + off]
                try:
                    v = float(v_raw) if v_raw not in ("", None) else 0.0
                except ValueError:
                    v = 0.0
                rng_probs[r] = v

            new_rows.append({
                "snapshot_cn": snapshot_cn,
                "snapshot_quikstrike": snapshot_quikstrike,
                "meeting_date": m["md"],
                "current_target": CURRENT_TARGET,
                "agg_p_hike_pct": "",
                "agg_p_hold_pct": "",
                "agg_p_cut_pct": "",
                "max_range_label": peak_label,
                "max_range_pct": f"{peak_pct * 100:.2f}",
                "aggregated_ranges": json.dumps(rng_probs, ensure_ascii=False),
                **{f"range_{r.replace('-','_')}_pct": f"{rng_probs[r]*100:.2f}" for r in RANGES},
            })

            col_offset += n_cols

    if n_dates:
        print(f"  日期范围：{date_indices[0]} → {date_indices[-1]}  (共 {n_dates} 天)")
    print(f"  共 {len(new_rows):,} 行 待 append")

    # De-dup
    existing = set()
    wrote_header = False
    if CSV_PATH.exists() and CSV_PATH.stat().st_size > 0:
        with open(CSV_PATH, encoding="utf-8") as f:
            r = csv.DictReader(f)
            wrote_header = r.fieldnames is not None
            for row in r:
                existing.add((row["snapshot_cn"], row["meeting_date"]))
    if not wrote_header:
        with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            w.writeheader()

    added = 0
    with open(CSV_PATH, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        for row in new_rows:
            key = (row["snapshot_cn"], row["meeting_date"])
            if key in existing:
                continue
            w.writerow(row)
            existing.add(key)
            added += 1

    print(f"  ✓ 追加 {added:,} 行 (跳过已存在 {len(new_rows) - added:,})")
    print(f"  → 重建看板 report/index.html …")
    subprocess.run([sys.executable, str(ROOT / "build_report.py")], check=False)


if __name__ == "__main__":
    main()
