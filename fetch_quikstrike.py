#!/usr/bin/env python3
"""
fetch_quikstrike.py — 用浏览器自动化从 CME QuikStrike FedWatch 工具
抓取 Aggregated Meeting Probabilities 表的真实数据。

为什么走浏览器而不是直接 HTTP：
  - QuikStrike 是 ASP.NET 页面，概率数据通过 JavaScript 渲染到 DOM
  - 底层 JSON 接口不公开（动态 ScriptResource.axd 加密）
  - QuikStrike 拒访 referer 非 cmegroup.com 的请求；agent-browser 用 --headers 解决

输出：
  data/fedwatch_probabilities.csv — 长表，按 (snapshot_cn, meeting_date) 去重
  data/snapshots/YYYY-MM-DD.json — 每日完整快照（含所有会议 + ZQ 价格）

依赖：agent-browser CLI 已在 PATH 中、Chromium 已 install。
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CSV_PATH = DATA / "fedwatch_probabilities.csv"
SNAP_DIR = DATA / "snapshots"

QUIKSTRIKE_URL = (
    "https://cmegroup-tools.quikstrike.net/User/QuikStrikeTools.aspx"
    "?viewitemid=IntegratedFedWatchTool"
)
CME_REFERER = "https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html"
HEADERS_JSON = json.dumps({"Referer": CME_REFERER})

# 当前目标区间（FedWatch Aggregated 列宽）
CURRENT_TARGET_LO = 350
CURRENT_TARGET_HI = 375
RANGES = [
    "325-350", "350-375", "375-400", "400-425",
    "425-450", "450-475", "475-500",
]

CSV_FIELDS = ["snapshot_cn", "snapshot_quikstrike",
              "meeting_date", "current_target",
              "agg_p_hike_pct", "agg_p_hold_pct", "agg_p_cut_pct",
              "max_range_label", "max_range_pct",
              "aggregated_ranges"] + [f"range_{r.replace('-', '_')}_pct"
                                       for r in RANGES]


def most_likely_range(probs: dict) -> tuple[str, float]:
    """取概率最大的区间标签与值；并列时取下限最高（市场预期最激进）。"""
    if not probs:
        return "", 0.0
    filtered = {k: v for k, v in probs.items()
                if v is not None and float(v) > 0}
    if not filtered:
        return "", 0.0

    def lo_bps(rng: str) -> int:
        try:
            return int(rng.split("-")[0])
        except Exception:
            return 0

    sorted_by_pct = sorted(filtered.items(),
                           key=lambda kv: (-float(kv[1]), -lo_bps(kv[0])))
    top_label, top_val = sorted_by_pct[0]
    return str(top_label), float(top_val)


# ---------------- agent-browser 封装 ----------------

def ab(args: list[str], timeout: int = 120) -> str:
    """调 agent-browser CLI 并返回 stdout。失败抛 RuntimeError。"""
    cmd = ["agent-browser"] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"agent-browser {' '.join(args)} 超时 ({timeout}s)")
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "").strip()
        raise RuntimeError(f"agent-browser {' '.join(args)} 失败: {msg[:200]}")
    return r.stdout


def ab_json(args: list[str], timeout: int = 120) -> dict:
    """调 agent-browser --json 并解析返回。"""
    cmd = ["agent-browser", "--json"] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"agent-browser {' '.join(args)} 超时 ({timeout}s)")
    if r.returncode != 0:
        msg = (r.stderr or r.stdout or "").strip()
        raise RuntimeError(f"agent-browser {' '.join(args)} 失败: {msg[:200]}")
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"agent-browser JSON 解析失败: {r.stdout[:200]}")


def ensure_browser_idle() -> None:
    """确保 Chromium 处于就绪状态：先关掉所有残留 session。"""
    try:
        ab(["close", "--all"], timeout=15)
    except RuntimeError:
        pass


def open_quikstrike() -> str:
    """打开 QuikStrike FedWatch 工具页面，带正确 Referer。返回最终 URL。"""
    out = ab([
        "open", QUIKSTRIKE_URL,
        "--headers", HEADERS_JSON,
    ], timeout=120)
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("http"):
            return line
    raise RuntimeError(f"open 后未拿到 URL: {out[:200]}")


# JS 脚本：用 IIFE 包裹（agent-browser eval 不支持裸箭头函数表达式）
CLICK_AGG_JS = r"""
(function(){
  const links = [...document.querySelectorAll('a')];
  const agg = links.find(a => a.textContent.trim() === 'Aggregated');
  if (!agg) return {ok:false, reason:'NO_AGGREGATED_LINK', n_links: links.length};
  agg.click();
  return {ok:true};
})()
"""

EXTRACT_JS = r"""
(function(){
  const out = {meetings: [], zq_prices: [], snapshot_quikstrike: ''};
  for (const t of document.querySelectorAll('table')) {
    const txt = t.textContent;
    if (txt.includes('ZQU6') && txt.includes('ZQV6')) {
      const cells = [...t.querySelectorAll('th, td')].map(c => c.textContent.trim());
      const contracts = cells.filter(s => /^ZQ[FGHJKMNQUVXZ]\d$/.test(s));
      const prices = cells.filter(s => /^\d+\.\d+$/.test(s)).map(parseFloat);
      out.zq_prices = contracts.map((c, i) => ({contract: c, settle: prices[i]}));
    }
    if (txt.includes('Aggregated Meeting Probabilities')) {
      const rows = [...t.querySelectorAll('tr')];
      let headers = [];
      for (const r of rows) {
        const cells = [...r.querySelectorAll('th, td')].map(c => c.textContent.trim());
        if (cells[0] && cells[0].toLowerCase() === 'meeting date') {
          headers = cells; break;
        }
      }
      for (const r of rows) {
        const cells = [...r.querySelectorAll('th, td')].map(c => c.textContent.trim());
        const m = cells[0] && cells[0].match(/^(\d{4})\/(\d+)\/(\d+)$/);
        if (m) {
          const probs = cells.slice(1).map(s => parseFloat(s));
          const ymd = m[1] + '-' + String(m[2]).padStart(2,'0') + '-' + String(m[3]).padStart(2,'0');
          out.meetings.push({
            meeting_date: ymd,
            ranges: headers.slice(1),
            probabilities: probs,
          });
        }
      }
    }
  }
  const ts = document.body.innerText.match(/Data as of\s+([^\n]+)/);
  if (ts) out.snapshot_quikstrike = ts[1].trim();
  return out;
})()
"""


def click_aggregated_tab() -> None:
    """点击 Aggregated tab，返回 result.ok=True 才算成功。"""
    resp = ab_json(["eval", "--base64",
                    _b64(CLICK_AGG_JS)], timeout=60)
    res = resp.get("data", {}).get("result", {})
    if not (isinstance(res, dict) and res.get("ok")):
        raise RuntimeError(f"点击 Aggregated 失败: {res}")


def extract_data() -> dict:
    """从当前 DOM 抽取 Aggregated 表 + ZQ 价格 + 时间戳。"""
    resp = ab_json(["eval", "--base64",
                    _b64(EXTRACT_JS)], timeout=60)
    res = resp.get("data", {}).get("result", {})
    if not isinstance(res, dict):
        raise RuntimeError(f"eval 返回结构异常: {resp}")
    if not res.get("meetings"):
        raise RuntimeError(f"未抽到任何会议数据: {res}")
    return res


def _b64(s: str) -> str:
    import base64
    return base64.b64encode(s.encode()).decode()


# ---------------- 数据加工 ----------------

def to_rows(snapshot: dict, snapshot_cn: str) -> list[dict]:
    """把 QuikStrike 抽取的快照转成 CSV 行。"""
    rows = []
    cur_target = f"{CURRENT_TARGET_LO}-{CURRENT_TARGET_HI}"
    for m in snapshot["meetings"]:
        probs = dict(zip(m["ranges"], m["probabilities"]))
        # 用全部命中区间计算 max（含 0% 的也保留以保持与官网表一致）
        max_label, max_pct = most_likely_range(probs)
        row = {
            "snapshot_cn": snapshot_cn,
            "snapshot_quikstrike": snapshot.get("snapshot_quikstrike", ""),
            "meeting_date": m["meeting_date"],
            "current_target": cur_target,
            "aggregated_ranges": json.dumps(
                {r: probs.get(r, 0.0) for r in m["ranges"]},
                ensure_ascii=False),
        }
        for r in RANGES:
            row[f"range_{r.replace('-', '_')}_pct"] = f"{probs.get(r, 0.0):.2f}"
        hold = probs.get(cur_target, 0.0)
        hike = sum(v for k, v in probs.items() if k != cur_target)
        cut = 0.0
        for r, v in probs.items():
            lo = int(r.split("-")[0])
            if lo < CURRENT_TARGET_LO:
                cut += v
        row["agg_p_hike_pct"] = f"{hike:.2f}"
        row["agg_p_hold_pct"] = f"{hold:.2f}"
        row["agg_p_cut_pct"] = f"{cut:.2f}"
        row["max_range_label"] = max_label
        row["max_range_pct"] = f"{max_pct:.2f}"
        rows.append(row)
    return rows


def load_existing() -> dict:
    if not CSV_PATH.exists():
        return {}
    out = {}
    with open(CSV_PATH, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            key = (r.get("snapshot_cn", ""), r.get("meeting_date", ""))
            out[key] = r
    return out


def signature(rows: list[dict]) -> str:
    """行内容指纹：用于判断两次抓取的数据是否一致。"""
    sigs = []
    for r in sorted(rows, key=lambda x: x["meeting_date"]):
        sigs.append(f"{r['meeting_date']}|{r['aggregated_ranges']}")
    return "\n".join(sigs)


def write_csv(rows: list[dict], snapshot_cn: str | None = None) -> int:
    """把本次抓取结果合并到 CSV。

    去重逻辑：
      - snapshot_cn 取到分钟精度（去掉秒），同分钟内重复运行视为同一次
      - 如果该 snapshot_cn 已经存在且数据一致（aggregated_ranges 一致），
        跳过；否则覆盖更新该 (snapshot_cn, meeting_date) 行
      - 如果本次内容与上一个 snapshot_cn 完全一致（防抖），
        跳过整次写入
    """
    DATA.mkdir(parents=True, exist_ok=True)
    # 把秒数归零：同一分钟内所有行用同一戳。snapshot_cn 可由调用方传入
    # “交易日归档”标签：周末 QuikStrike 仍显示上一个交易日行情，不能把它
    # 直接写成周六/周日，否则折线图会产生不存在的周末交易日。
    if snapshot_cn is None:
        snapshot_cn = dt.datetime.now().replace(second=0, microsecond=0) \
                                       .strftime("%Y-%m-%d %H:%M:%S")
    for r in rows:
        r["snapshot_cn"] = snapshot_cn

    # 防抖：与上一个 snapshot_cn 数据一致则整次跳过
    existing = load_existing()
    prev_by_meet = {}
    for (sc, md), r in existing.items():
        if sc != snapshot_cn:
            prev_by_meet[md] = sc  # 最新一行（按 snapshot_cn 倒序）
    # 找真正的「上一分钟」的所有行（按 snapshot_cn 排序）
    sorted_snaps = sorted({sc for sc, _ in existing.keys()}, reverse=True)
    if len(sorted_snaps) >= 1:
        prev_snap = sorted_snaps[0]  # 最近的（snapshot_cn 倒序第一个就是最新）
        prev_rows = [r for (sc, _), r in existing.items() if sc == prev_snap]
        if prev_rows and signature(prev_rows) == signature(rows):
            return 0  # 数据未变，跳过整次

    new_count = 0
    for row in rows:
        key = (row["snapshot_cn"], row["meeting_date"])
        if key not in existing:
            new_count += 1
        existing[key] = row
    with open(CSV_PATH, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        for r in sorted(existing.values(),
                        key=lambda x: (x["snapshot_cn"], x["meeting_date"])):
            w.writerow(r)
    return new_count


def effective_snapshot_dt(captured_at: dt.datetime | None = None) -> dt.datetime:
    """把周末实时抓取归档到最近一个工作日，保留实际时分秒。

    QuikStrike 在周末通常仍展示上一交易日的收盘/周末前价格。本站的
    折线按交易日而不是自然日展示，因此周六、周日分别归档到周五。
    原始抓取时刻仍写入每日 JSON 的 captured_at_cn 供审计。
    """
    captured_at = captured_at or dt.datetime.now()
    if captured_at.weekday() == 5:      # Saturday -> Friday
        return captured_at - dt.timedelta(days=1)
    if captured_at.weekday() == 6:      # Sunday -> Friday
        return captured_at - dt.timedelta(days=2)
    return captured_at


def save_snapshot(snapshot: dict, captured_at_cn: str, effective_snapshot_cn: str) -> Path:
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    today = dt.datetime.now().strftime("%Y-%m-%d")
    p = SNAP_DIR / f"{today}.json"
    # Keep the extracted payload intact while recording both clocks explicitly.
    saved = dict(snapshot)
    saved["captured_at_cn"] = captured_at_cn
    saved["effective_snapshot_cn"] = effective_snapshot_cn
    p.write_text(json.dumps(saved, ensure_ascii=False, indent=2))
    return p


# ---------------- 主流程 ----------------

def run_once(dry: bool = False) -> dict:
    """完整跑一次：开浏览器 → 点击 Aggregated → 抓数据 → 落盘。"""
    ensure_browser_idle()
    final_url = open_quikstrike()
    # 给 ASP.NET 异步加载留时间（含第三方脚本）
    time.sleep(8)
    click_aggregated_tab()
    time.sleep(5)
    snapshot = extract_data()
    captured_at = dt.datetime.now().replace(second=0, microsecond=0)
    effective_at = effective_snapshot_dt(captured_at)
    captured_at_cn = captured_at.strftime("%Y-%m-%d %H:%M:%S")
    snapshot_cn_raw = effective_at.strftime("%Y-%m-%d %H:%M:%S")
    rows = to_rows(snapshot, snapshot_cn_raw)
    if dry:
        return {"rows": rows, "snapshot": snapshot,
                "snapshot_cn": snapshot_cn_raw,
                "captured_at_cn": captured_at_cn,
                "final_url": final_url}
    new_count = write_csv(rows, snapshot_cn_raw)
    snap_path = save_snapshot(snapshot, captured_at_cn, snapshot_cn_raw)
    return {
        "new_count": new_count,
        "n_meetings": len(rows),
        "snapshot_cn": snapshot_cn_raw,
        "captured_at_cn": captured_at_cn,
        "snapshot_quikstrike": snapshot.get("snapshot_quikstrike", ""),
        "snapshot_path": str(snap_path),
        "final_url": final_url,
    }


def report(s: dict) -> str:
    if "rows" in s:
        return f"[dry] 抽到 {len(s['rows'])} 行；第一行: {s['rows'][0]}"
    lines = [
        f"实际采集时间(北京): {s.get('captured_at_cn', s['snapshot_cn'])}",
        f"交易日归档时间: {s['snapshot_cn']}",
        f"QuikStrike 时间戳: {s['snapshot_quikstrike'] or '(未抓到)'}",
        f"新增行数: {s['new_count']} / 总 {s['n_meetings']} 个会议",
        f"快照文件: {s['snapshot_path']}",
        "",
        "关注会议 Aggregated 加息累计概率:",
    ]
    by_meet = {}
    with open(CSV_PATH, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["snapshot_cn"] == s["snapshot_cn"]:
                by_meet[r["meeting_date"]] = r
    for m in ("2026-09-16", "2026-10-28"):
        if m in by_meet:
            r = by_meet[m]
            lines.append(
                f"  {m}: 加息 {r['agg_p_hike_pct']}% / "
                f"维持 {r['agg_p_hold_pct']}% / 降息 {r['agg_p_cut_pct']}%")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只打印不落盘")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = ap.parse_args()

    try:
        s = run_once(dry=args.dry)
    except Exception as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 3
    finally:
        try:
            ab(["close"], timeout=15)
        except RuntimeError:
            pass

    if args.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
    else:
        print(report(s))
    return 0


if __name__ == "__main__":
    sys.exit(main())