#!/usr/bin/env python3
"""
fetch_quikstrike.py — 用浏览器自动化从 CME QuikStrike FedWatch 工具
抓取 Aggregated Meeting Probabilities 表的真实数据。

为什么走浏览器而不是直接 HTTP：
  - QuikStrike 是 ASP.NET 页面，概率数据通过 JavaScript 渲染到 DOM
  - 底层 JSON 接口不公开（动态 ScriptResource.axd 加密）
  - QuikStrike 拒访 referer 非 cmegroup.com 的请求；agent-browser 用 --headers 解决

时间口径（详见 time_axis.py 文档头）：
  一个点 = 美东一个交易日的**收盘定格**。因此本脚本只在
  `COLLECT_WINDOW = [16:02, 16:58) CT`（CME 收盘后的休市间隙）内采集；
  不在窗内 → 不启动浏览器、不落盘，直接退出（exit 10）。
  横轴用的 `us_trade_date` 由采集时刻换算到 CT 推出，**不依赖页面文案格式**。

页面自带的 `Data as of ... CT` 戳：
  它只在**默认视图**里；一旦切到 Aggregated 视图该节点就消失 —— 必须在点击
  Aggregated **之前**读取。这正是它长期抓不到（5 条实时记录全为空）的原因。
  抓到的值写入 `data_asof_ct`：用于新鲜度校验（滞后 > 75 分钟告警）与 tooltip 展示；
  抓不到只是降级告警，不阻断采集。

输出：
  data/fedwatch_probabilities.csv — 长表，按 (snapshot_cn, meeting_date) 去重，
      另含三个时间轴派生列：us_trade_date / data_asof_ct / time_basis
  data/snapshots/YYYY-MM-DD.json — 每日完整快照（含所有会议 + ZQ 价格 + 多个时钟）

退出码：
  0  抓到新数据并落盘
  10 不在采集窗口（正常空转；外层视为「无新数据」）
  11 该美东交易日已有记录（A 方案：保留最早那一次）
  12 数据与上一快照完全一致（防抖，未写入）
  3  失败（外层应重试）

依赖：agent-browser CLI 已在 PATH 中、Chromium 已 install。
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from time_axis import (  # noqa: E402
    BASIS_PAGE, TIME_AXIS_FIELDS, asof_freshness, asof_is_post_close,
    collect_window_note, in_collect_window, normalize_asof, now_ct,
    parse_page_asof, us_trade_date_from_ct,
)
from rate_baseline import resolve_current_target  # noqa: E402

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CSV_PATH = DATA / "fedwatch_probabilities.csv"
SNAP_DIR = DATA / "snapshots"

QUIKSTRIKE_URL = (
    "https://cmegroup-tools.quikstrike.net/User/QuikStrikeTools.aspx"
    "?viewitemid=IntegratedFedWatchTool"
)
CME_REFERER = "https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html"
# Accept-Language 决定 QuikStrike 的区域格式（会议日期 YYYY/M/D、Data-as-of 中文月名
# 都是 zh-CN 会话的产物）。服务器托管 Chrome 默认 en-US 会给出 MM/DD/YYYY + PM 时间，
# 下游 parse_page_asof 不认；固定成 zh-CN 与 Mac 端完全同构。
HEADERS_JSON = json.dumps({"Referer": CME_REFERER,
                           "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})

# FedWatch Aggregated 列的固定区间宽度（页面表格布局，与目标区间无关）
RANGES = [
    "325-350", "350-375", "375-400", "400-425",
    "425-450", "450-475", "475-500",
]

CSV_FIELDS = ["snapshot_cn", "snapshot_quikstrike",
              "meeting_date", "current_target",
              "agg_p_hike_pct", "agg_p_hold_pct", "agg_p_cut_pct",
              "max_range_label", "max_range_pct",
              "aggregated_ranges"] + [f"range_{r.replace('-', '_')}_pct"
                                       for r in RANGES] + TIME_AXIS_FIELDS


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

# 页面自带的数据时点，只在默认视图里存在 —— 必须在点击 Aggregated 之前读。
ASOF_JS = r"""
(function(){
  const out = {raw: '', tag: '', mode: ''};
  for (const el of document.querySelectorAll('td,th,div,span,p')) {
    if (el.children.length) continue;
    const t = (el.textContent || '').trim();
    if (/data as of/i.test(t) && t.length < 120) {
      out.raw = t; out.tag = el.tagName; out.mode = 'dom'; break;
    }
  }
  if (!out.raw) {
    const m = document.body.innerText.match(/data as of[^\n]*/i);
    if (m) { out.raw = m[0].trim(); out.mode = 'innerText'; }
  }
  return out;
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
        // 会话区域格式两种都可能：YYYY/M/D（zh-CN）或 M/D/YYYY（en-US）
        const m = cells[0] && (cells[0].match(/^(\d{4})\/(\d+)\/(\d+)$/) || cells[0].match(/^(\d+)\/(\d+)\/(\d{4})$/));
        if (m) {
          const probs = cells.slice(1).map(s => parseFloat(s));
          const parts = /^\d{4}\//.test(cells[0]) ? [m[1], m[2], m[3]] : [m[3], m[1], m[2]];
          const ymd = parts[0] + '-' + String(parts[1]).padStart(2,'0') + '-' + String(parts[2]).padStart(2,'0');
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


def _b64(s: str) -> str:
    import base64
    return base64.b64encode(s.encode()).decode()


def read_page_asof() -> str:
    """在默认视图里读页面的 Data-as-of 戳。失败不抛异常（只是降级）。"""
    try:
        resp = ab_json(["eval", "--base64", _b64(ASOF_JS)], timeout=60)
        res = resp.get("data", {}).get("result", {})
        if isinstance(res, dict):
            return str(res.get("raw") or "").strip()
    except RuntimeError:
        pass
    return ""


def click_aggregated_tab() -> None:
    """点击 Aggregated tab，返回 result.ok=True 才算成功。"""
    resp = ab_json(["eval", "--base64",
                    _b64(CLICK_AGG_JS)], timeout=60)
    res = resp.get("data", {}).get("result", {})
    if not (isinstance(res, dict) and res.get("ok")):
        raise RuntimeError(f"点击 Aggregated 失败: {res}")


def extract_data() -> dict:
    """从当前 DOM 抽取 Aggregated 表 + ZQ 价格。"""
    resp = ab_json(["eval", "--base64",
                    _b64(EXTRACT_JS)], timeout=60)
    res = resp.get("data", {}).get("result", {})
    if not isinstance(res, dict):
        raise RuntimeError(f"eval 返回结构异常: {resp}")
    if not res.get("meetings"):
        raise RuntimeError(f"未抽到任何会议数据: {res}")
    return res


# ---------------- 数据加工 ----------------

def to_rows(snapshot: dict, snapshot_cn: str, us_trade_date: str,
            asof_raw: str, cur_target: str) -> list[dict]:
    """把 QuikStrike 抽取的快照转成 CSV 行（含三个时间轴派生列）。

    cur_target 由 resolve_current_target 按「最近已开完的 FOMC 决议」推导，
    不再写死（2026-09-29 前 CURRENT_TARGET 曾硬编码 350-375，9/16 加息 25bp 后
    未更新，导致 agg 口径把已兑现加息也计进「加息」；且旧算法 hike=除持稳桶
    外全部，把降息桶也误计为加息，现一并修正为严格的上下界划分）。
    """
    rows = []
    target_lo, target_hi = (int(x) for x in cur_target.split("-"))
    for m in snapshot["meetings"]:
        probs = dict(zip(m["ranges"], m["probabilities"]))
        # 用全部命中区间计算 max（含 0% 的也保留以保持与官网表一致）
        max_label, max_pct = most_likely_range(probs)
        row = {
            "snapshot_cn": snapshot_cn,
            # 这一列保留页面原文（含 "* Data as of ..."），便于审计
            "snapshot_quikstrike": asof_raw or snapshot.get("snapshot_quikstrike", ""),
            "meeting_date": m["meeting_date"],
            "current_target": cur_target,
            "aggregated_ranges": json.dumps(
                {r: probs.get(r, 0.0) for r in m["ranges"]},
                ensure_ascii=False),
            "us_trade_date": us_trade_date,
            # 规范化后的数据时点；解析不出就是空串（明确表示「没有可信时点」）
            "data_asof_ct": normalize_asof(asof_raw),
            "time_basis": BASIS_PAGE,
        }
        for r in RANGES:
            row[f"range_{r.replace('-', '_')}_pct"] = f"{probs.get(r, 0.0):.2f}"
        hold = hike = cut = 0.0
        for r, v in probs.items():
            try:
                lo, hi = (int(x) for x in r.split("-"))
            except ValueError:
                continue
            if lo >= target_hi:
                hike += v
            elif hi <= target_lo:
                cut += v
            else:
                hold += v
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
    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            key = (r.get("snapshot_cn", ""), r.get("meeting_date", ""))
            out[key] = r
    return out


def recorded_us_days() -> set[str]:
    """已经用**新口径**记录过的美东交易日（A 方案的去重依据）。

    只统计 time_basis=page_asof 的行。历史那 5 个 legacy_intraday 点是盘中读数，
    不应挡住同一交易日随后的收盘定格采集 —— 过渡日会出现一次「同日两个点」，
    那是两个口径的真实读数，不是重复。
    """
    out = set()
    for r in load_existing().values():
        if (r.get("time_basis") or "").strip() == BASIS_PAGE:
            d = (r.get("us_trade_date") or "").strip()
            if d:
                out.add(d)
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
      - snapshot_cn = 北京真实采集时刻（整分钟），同分钟内重复运行视为同一次
      - 若该 snapshot_cn 已存在，按 (snapshot_cn, meeting_date) 覆盖更新
      - 防抖：与上一个 snapshot_cn 内容完全一致则整次跳过（返回 0）
      - 跨交易日的去重（A 方案）由调用方用 recorded_us_days() 提前拦掉
    """
    DATA.mkdir(parents=True, exist_ok=True)
    if snapshot_cn is None:
        snapshot_cn = dt.datetime.now().replace(second=0, microsecond=0) \
                                       .strftime("%Y-%m-%d %H:%M:%S")
    for r in rows:
        r["snapshot_cn"] = snapshot_cn

    # 防抖：与上一个 snapshot_cn 数据一致则整次跳过
    existing = load_existing()
    sorted_snaps = sorted({sc for sc, _ in existing.keys()}, reverse=True)
    if sorted_snaps:
        prev_snap = sorted_snaps[0]
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
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in sorted(existing.values(),
                        key=lambda x: (x.get("snapshot_cn", ""),
                                       x.get("meeting_date", ""))):
            w.writerow(r)
    return new_count


def save_snapshot(snapshot: dict, snapshot_cn: str, us_trade_date: str,
                  asof_raw: str, collect_ct: str) -> Path:
    """落盘每日 JSON，把每个时钟都显式记下来（审计用）。"""
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    today = dt.datetime.now().strftime("%Y-%m-%d")
    p = SNAP_DIR / f"{today}.json"
    saved = dict(snapshot)
    saved["snapshot_cn"] = snapshot_cn                # = 北京采集时刻（整分钟）
    saved["captured_at_cn"] = snapshot_cn             # 北京真实采集时刻
    saved["collect_ct"] = collect_ct                  # 采集瞬间的 CT 墙钟
    saved["effective_snapshot_cn"] = snapshot_cn      # 兼容旧读取方（已废弃）
    saved["us_trade_date"] = us_trade_date            # 横轴用
    saved["data_asof_ct"] = normalize_asof(asof_raw)  # 规范化
    saved["data_asof_raw"] = asof_raw                 # 页面原文（审计用）
    p.write_text(json.dumps(saved, ensure_ascii=False, indent=2))
    return p


# ---------------- 主流程 ----------------

def run_once(dry: bool = False, force: bool = False) -> dict:
    """完整跑一次：门禁 → 开浏览器 → 读页面时间戳 → 抓数据 → 落盘。"""
    captured_at = dt.datetime.now().replace(second=0, microsecond=0)
    captured_at_cn = captured_at.strftime("%Y-%m-%d %H:%M:%S")
    # 采集瞬间只取一次 CT 时钟：窗口判定与交易日标签共用同一基准，避免跨分钟漂移
    collect_ct = now_ct()
    usd = us_trade_date_from_ct(collect_ct).isoformat()

    # 门禁一：必须在收盘后的休市间隙内（硬保证，不依赖页面文案）
    if not dry and not force and not in_collect_window(collect_ct):
        return {"status": "out_of_window", "exit_code": 10,
                "collect_ct": f"{collect_ct:%Y-%m-%d %H:%M:%S %Z}",
                "note": collect_window_note(collect_ct),
                "us_trade_date": usd, "captured_at_cn": captured_at_cn,
                "n_meetings": 0, "new_count": 0, "data_asof_ct": "",
                "asof_fresh": False, "asof_note": "未采集"}

    # 门禁二：该美东交易日已有记录 → 跳过，连浏览器都不开（A 方案）
    if not dry and not force and usd in recorded_us_days():
        return {"status": "duplicate_day", "exit_code": 11,
                "collect_ct": f"{collect_ct:%Y-%m-%d %H:%M:%S %Z}",
                "us_trade_date": usd, "captured_at_cn": captured_at_cn,
                "note": f"us_trade_date={usd} 已有记录，A 方案保留最早那一次",
                "n_meetings": 0, "new_count": 0, "data_asof_ct": "",
                "asof_fresh": False, "asof_note": "未采集"}

    ensure_browser_idle()
    final_url = open_quikstrike()
    # 给 ASP.NET 异步加载留时间（含第三方脚本）
    time.sleep(8)
    asof_raw = read_page_asof()          # 必须在点击 Aggregated 之前读
    click_aggregated_tab()
    time.sleep(5)
    snapshot = extract_data()
    asof_ct = parse_page_asof(asof_raw)
    fresh, fresh_note = asof_freshness(asof_ct, now_ct())

    cur_target = resolve_current_target(list(load_existing().values()),
                                        snapshot.get("meetings", []), usd)
    rows = to_rows(snapshot, captured_at_cn, usd, asof_raw, cur_target)
    meta = {
        "n_meetings": len(rows),
        "current_target": cur_target,
        "snapshot_cn": captured_at_cn,
        "captured_at_cn": captured_at_cn,
        "collect_ct": f"{collect_ct:%Y-%m-%d %H:%M:%S %Z}",
        "us_trade_date": usd,
        "data_asof_ct": asof_raw,
        "asof_fresh": fresh,
        "asof_note": fresh_note,
        "asof_post_close": asof_is_post_close(asof_ct),
        "snapshot_quikstrike": asof_raw,
        "final_url": final_url,
    }
    if dry:
        return {**meta, "status": "dry", "rows": rows, "snapshot": snapshot}
    if not fresh:
        # 只告警不阻断：收盘口径的保证来自 collect_ct 落在休市间隙
        meta["warn"] = fresh_note
    new_count = write_csv(rows, captured_at_cn)
    snap_path = save_snapshot(snapshot, captured_at_cn, usd, asof_raw,
                              f"{collect_ct:%Y-%m-%d %H:%M:%S %Z}")
    meta["new_count"] = new_count
    meta["snapshot_path"] = str(snap_path)
    if new_count == 0:
        meta.update(status="unchanged", exit_code=12,
                    note="数据与上一快照完全一致（防抖），未写入")
    else:
        meta["status"] = "ok"
    return meta


def report(s: dict) -> str:
    if s.get("status") == "dry":
        return (f"[dry] 抽到 {len(s['rows'])} 行 / {s['n_meetings']} 个会议；"
                f"us_trade_date={s['us_trade_date']}；页面戳={s['data_asof_ct']!r}")
    lines = [
        f"状态: {s['status']}",
        f"采集瞬间(CT): {s['collect_ct']}",
        f"实际采集时间(北京): {s['captured_at_cn']}",
        f"us_trade_date（横轴用）: {s['us_trade_date']}",
        f"页面自带数据时点: {s.get('data_asof_ct') or '(未抓到)'}",
        f"  新鲜度: {'OK' if s.get('asof_fresh') else '警告'} —— {s.get('asof_note', '')}",
    ]
    if s.get("n_meetings"):
        lines.append(f"新增行数: {s.get('new_count', 0)} / 共 {s['n_meetings']} 个会议")
    if s.get("note"):
        lines.append(f"说明: {s['note']}")
    if s.get("snapshot_path"):
        lines.append(f"快照文件: {s['snapshot_path']}")
    if s.get("status") != "ok":
        return "\n".join(lines)
    lines += ["", "关注会议 Aggregated 加息累计概率:"]
    by_meet = {}
    with open(CSV_PATH, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["snapshot_cn"] == s["snapshot_cn"]:
                by_meet[r["meeting_date"]] = r
    for m in sorted(by_meet)[:3]:
        r = by_meet[m]
        lines.append(
            f"  {m}: 加息 {r['agg_p_hike_pct']}% / "
            f"维持 {r['agg_p_hold_pct']}% / 降息 {r['agg_p_cut_pct']}%")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true",
                    help="只打印不落盘（同时跳过所有门禁，便于随时试跑）")
    ap.add_argument("--force", action="store_true",
                    help="跳过窗口门禁与当日去重（仍记录页面数据新鲜度）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = ap.parse_args()

    try:
        s = run_once(dry=args.dry, force=args.force)
    except Exception as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 3
    finally:
        try:
            ab(["close"], timeout=15)
        except RuntimeError:
            pass

    if args.json:
        print(json.dumps(s, ensure_ascii=False, indent=2, default=str))
    else:
        print(report(s))
    return int(s.get("exit_code", 0))


if __name__ == "__main__":
    sys.exit(main())
