#!/usr/bin/env python3
"""Static SEO/discovery pages and validation for FedWatch Tracker.

This module intentionally publishes project/methodology information without naming or
otherwise identifying the maintainer.
"""
from __future__ import annotations

import csv
import html
import json
import os
import shutil
import subprocess
from collections import defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

from snapshot_pick import pick_latest_snapshot

BASE_DIR = Path(__file__).resolve().parent
REPORT_DIR = BASE_DIR / "report"
STATIC_DIR = BASE_DIR / "static"
SITE_URL = os.environ.get("FEDWATCH_SITE_URL", "https://fedwatch-tracker.pages.dev").rstrip("/")
SITE_NAME = "FedWatch Tracker"
GOOGLE_SITE_VERIFICATION = "DEH9MT4Vn3IN5GpTFBqiDV7WHbx3VUzhjNFJ7uI2EI8"
CME_URL = "https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html"
FED_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def pct(value: object, default: str = "—") -> str:
    try:
        if value in (None, ""):
            return default
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return default


def range_pct(label: str) -> str:
    if not label or "-" not in label:
        return "—"
    lo, hi = label.split("-", 1)
    try:
        return f"{int(lo) / 100:.2f}%–{int(hi) / 100:.2f}%"
    except ValueError:
        return esc(label)


def canonical(path: str = "/") -> str:
    return f"{SITE_URL}{path if path.startswith('/') else '/' + path}"


def json_ld(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def shared_head(
    title: str,
    description: str,
    path: str,
    *,
    lang: str = "zh-CN",
    schema: object | None = None,
    alternate_zh: str | None = None,
    alternate_en: str | None = None,
) -> str:
    alt = []
    if alternate_zh:
        alt.append(f'<link rel="alternate" hreflang="zh-CN" href="{esc(canonical(alternate_zh))}">')
    if alternate_en:
        alt.append(f'<link rel="alternate" hreflang="en" href="{esc(canonical(alternate_en))}">')
    if alternate_zh or alternate_en:
        alt.append(f'<link rel="alternate" hreflang="x-default" href="{esc(canonical(alternate_zh or path))}">')
    schema_html = f'<script type="application/ld+json">{json_ld(schema)}</script>' if schema else ""
    return f"""<meta name="description" content="{esc(description)}">
<meta name="robots" content="index,follow,max-image-preview:large,max-snippet:-1,max-video-preview:-1">
<link rel="canonical" href="{esc(canonical(path))}">
{''.join(alt)}
<meta property="og:type" content="website">
<meta property="og:site_name" content="{SITE_NAME}">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{esc(canonical(path))}">
<meta property="og:image" content="{esc(canonical('/assets/og-image.png'))}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{esc(title)}">
<meta name="twitter:description" content="{esc(description)}">
<meta name="twitter:image" content="{esc(canonical('/assets/og-image.png'))}">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
{schema_html}"""


COMMON_CSS = """
:root{color-scheme:light;--ink:#0b1220;--muted:#64748b;--line:#e2e8f0;--brand:#1d4ed8;--bg:#f8fafc}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",system-ui,sans-serif;line-height:1.65}
a{color:var(--brand);text-decoration:none}a:hover{text-decoration:underline}.shell{max-width:1080px;margin:0 auto;padding:0 22px 64px}
.site-nav{display:flex;align-items:center;justify-content:space-between;gap:18px;padding:18px 0;border-bottom:1px solid var(--line);margin-bottom:32px}.brand{font-weight:800;color:var(--ink);letter-spacing:-.3px}.nav-links{display:flex;flex-wrap:wrap;gap:14px;font-size:14px}
.breadcrumbs{font-size:13px;color:var(--muted);margin-bottom:18px}.breadcrumbs a{color:var(--muted)}h1{font-size:32px;line-height:1.25;letter-spacing:-.7px;margin:0 0 10px}h2{font-size:21px;margin:30px 0 12px}h3{font-size:16px;margin:0 0 8px}.lede{font-size:17px;color:#475569;max-width:840px;margin:0 0 22px}.meta{font-size:13px;color:var(--muted);margin-bottom:22px}.card{background:#fff;border:1px solid var(--line);border-radius:14px;padding:20px 22px;margin:16px 0}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.metric strong{display:block;font-size:24px;font-variant-numeric:tabular-nums}.metric span{font-size:13px;color:var(--muted)}
table{width:100%;border-collapse:collapse;background:#fff;font-size:14px}th,td{padding:9px 11px;border-bottom:1px solid #eef2f7;text-align:left;font-variant-numeric:tabular-nums}th{background:#f8fafc;color:#475569;font-size:13px}td.num,th.num{text-align:right}.table-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:#fff}.notice{padding:14px 16px;border-left:4px solid #2563eb;background:#eff6ff;color:#1e3a8a;border-radius:8px}.small{font-size:13px;color:var(--muted)}.site-links{display:flex;flex-wrap:wrap;gap:8px 14px}.site-links a{display:inline-block}footer{margin-top:44px;padding-top:20px;border-top:1px solid var(--line);color:var(--muted);font-size:13px}
@media(max-width:760px){.grid{grid-template-columns:1fr}.site-nav{align-items:flex-start;flex-direction:column}.nav-links{gap:10px}h1{font-size:27px}.shell{padding-left:15px;padding-right:15px}th,td{padding:8px}}
"""


def nav(lang: str = "zh") -> str:
    labels = (
        [("/", "首页"), ("/history/", "会议历史"), ("/analysis/", "变化分析"), ("/data/", "数据下载"), ("/methodology/", "方法"), ("/about/", "关于"), ("/en/", "English")]
        if lang == "zh"
        else [("/en/", "Home"), ("/history/", "Meeting history"), ("/analysis/", "Analysis"), ("/data/", "Data"), ("/methodology/", "Methodology"), ("/about/", "About"), ("/", "中文")]
    )
    links = "".join(f'<a href="{href}">{esc(label)}</a>' for href, label in labels)
    return f'<nav class="site-nav" aria-label="Primary"><a class="brand" href="/">{SITE_NAME}</a><div class="nav-links">{links}</div></nav>'


def page_document(
    *, title: str, description: str, path: str, body: str, lang: str = "zh-CN",
    schema: object | None = None, alternate_zh: str | None = None, alternate_en: str | None = None,
) -> str:
    language = "en" if lang.startswith("en") else "zh"
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
{shared_head(title, description, path, lang=lang, schema=schema, alternate_zh=alternate_zh, alternate_en=alternate_en)}
<style>{COMMON_CSS}</style></head><body><main class="shell">{nav(language)}{body}
<footer><strong>{SITE_NAME}</strong> · Independent research and data-tracking project; not affiliated with CME Group or the Federal Reserve. Data source and snapshot timing are documented on the <a href="/methodology/">methodology page</a>.</footer>
</main></body></html>"""


def write_page(path: str, content: str) -> Path:
    target = REPORT_DIR / path.strip("/") / "index.html" if path != "/" else REPORT_DIR / "index.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def valid_rows_for_meeting(rows: list[dict], meeting: str) -> list[dict]:
    out = []
    for row in rows:
        if row.get("meeting_date") != meeting:
            continue
        label = (row.get("max_range_label") or "").strip()
        try:
            value = float(row.get("max_range_pct") or 0)
        except ValueError:
            continue
        if label and value > 0:
            out.append(row)
    return sorted(out, key=lambda r: r.get("snapshot_cn", ""))


def latest_rows(rows: list[dict]) -> tuple[str, list[dict]]:
    by_snapshot: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("snapshot_cn"):
            by_snapshot[row["snapshot_cn"]].append(row)
    latest_key = pick_latest_snapshot(by_snapshot)
    return latest_key, sorted(by_snapshot[latest_key], key=lambda r: r.get("meeting_date", ""))


def page_meetings(rows: list[dict], latest: list[dict]) -> dict[str, dict]:
    """需要独立页面的会议 → 该会议的「最新一条记录」。

    以最新快照中的会议为主；同时保留所有仍有有效历史的会议。FedWatch 会在会议
    开完后把该会议从抓取表里删掉，若只按最新快照生成页面，已上线并被索引的
    ``/meetings/<date>/`` 会在会议结束当天变成 404——而站点明确承诺「每个会议拥有
    独立、稳定的历史页面」，因此这里把刚结束的会议继续归档。
    """
    last_row: dict[str, dict] = {}
    for row in rows:
        meeting = (row.get("meeting_date") or "").strip()
        if not meeting:
            continue
        prev = last_row.get(meeting)
        if prev is None or (row.get("snapshot_cn") or "") >= (prev.get("snapshot_cn") or ""):
            last_row[meeting] = row

    pages = {row["meeting_date"]: row for row in latest}
    for meeting, row in last_row.items():
        if meeting not in pages and valid_rows_for_meeting(rows, meeting):
            pages[meeting] = row
    return pages


def delta(points: list[dict], periods: int) -> float | None:
    if len(points) <= periods:
        return None
    return float(points[-1]["max_range_pct"]) - float(points[-1 - periods]["max_range_pct"])


def delta_text(value: float | None) -> str:
    return "—" if value is None else f"{value:+.2f} pp"


def us_day_map(rows: list[dict]) -> dict[str, str]:
    """{snapshot_cn: 该快照对应的美东交易日}。

    站点上任何「显示给读者看的日期」都必须走这里，而不是 `snapshot_cn[:10]`
    （那是北京采集时刻）。口径见 time_axis.py。
    """
    out: dict[str, str] = {}
    for r in rows:
        key = (r.get("snapshot_cn") or "").strip()
        if key and key not in out:
            out[key] = (r.get("us_trade_date") or "").strip() or key[:10]
    return out


def build_homepage_meta(home_html: str, rows: list[dict], latest_key: str, latest: list[dict]) -> str:
    usd = us_day_map(rows)
    latest_day = usd.get(latest_key, latest_key[:10])
    first_date = min(usd.values()) if usd else latest_day
    coverage_last = max(usd.values()) if usd else latest_day
    description = "每日追踪 CME FedWatch 美联储加息、维持与降息概率，提供未来 FOMC 会议概率、过去一年历史变化、重大事件归因及 CSV 数据下载。"
    schema = [
        {
            "@context": "https://schema.org", "@type": "WebSite", "name": SITE_NAME,
            "alternateName": ["美联储利率概率历史追踪", "Fed Rate Probability History"],
            "url": canonical("/"), "inLanguage": ["zh-CN", "en"],
        },
        {
            "@context": "https://schema.org", "@type": "Dataset",
            "name": "FedWatch Tracker Historical FOMC Rate Probabilities",
            "description": "Daily snapshots and historical target-rate probability distributions for upcoming FOMC meetings, derived from CME QuikStrike FedWatch Aggregated View and historical downloads.",
            "url": canonical("/data/"), "dateModified": coverage_last,
            "temporalCoverage": f"{first_date}/{coverage_last}",
            "inLanguage": ["zh-CN", "en"], "isBasedOn": CME_URL,
            "distribution": [{"@type": "DataDownload", "encodingFormat": "text/csv", "contentUrl": canonical("/data/fedwatch-probabilities.csv")}],
        },
    ]
    title = "FedWatch Tracker：美联储加息与降息概率历史 | Fed Rate Probability History"
    replacement = (f"<title>{title}</title>\n<meta name=\"google-site-verification\" content=\"{GOOGLE_SITE_VERIFICATION}\">\n"
                   f"{shared_head(title, description, '/', schema=schema, alternate_zh='/', alternate_en='/en/')}")
    home_html = home_html.replace("<title>CME FedWatch · 利率预期追踪</title>", replacement, 1)
    extra_css = """
.site-nav{display:flex;align-items:center;justify-content:space-between;gap:18px;padding:0 0 16px;border-bottom:1px solid #e5e7eb;margin-bottom:24px}.site-nav .brand{font-weight:800;color:#0b1220;text-decoration:none}.site-nav .nav-links{display:flex;flex-wrap:wrap;gap:13px}.site-nav a{font-size:13px;color:#475569;text-decoration:none}.site-nav a:hover{text-decoration:underline}.search-summary{border-left:4px solid #2563eb;background:#eff6ff;border-radius:8px;padding:13px 15px;margin:0 0 16px;font-size:14px;line-height:1.65;color:#1e3a8a}.site-links{display:flex;flex-wrap:wrap;gap:9px;margin:0 0 16px}.site-links a{border:1px solid #dbeafe;background:#fff;color:#1d4ed8;border-radius:999px;padding:5px 11px;font-size:12px;text-decoration:none}.seo-footer{margin-top:30px;padding-top:18px;border-top:1px solid #e5e7eb;color:#64748b;font-size:12.5px;line-height:1.7}.seo-footer a{color:#1d4ed8}@media(max-width:720px){.site-nav{align-items:flex-start;flex-direction:column}.site-nav .nav-links{gap:9px}}
"""
    home_html = home_html.replace("</style></head><body><div class=\"wrap\">", f"{extra_css}</style></head><body><div class=\"wrap\">{nav('zh')}", 1)
    nearest = latest[0] if latest else {}
    summary = (
        f'<div class="search-summary"><strong>最新 FedWatch 概率快照：</strong>截至 '
        f'<time datetime="{esc(latest_day)}">{esc(latest_day)}</time>（美东交易日，收盘后读数），'
        f'下一次 FOMC 会议（{esc(nearest.get("meeting_date", "—"))}）累计加息、维持、降息概率分别为 '
        f'<strong>{pct(nearest.get("agg_p_hike_pct"))}</strong>、<strong>{pct(nearest.get("agg_p_hold_pct"))}</strong>、'
        f'<strong>{pct(nearest.get("agg_p_cut_pct"))}</strong>。本站是每日定时快照，并非 CME 盘中实时行情。</div>'
        '<div class="site-links" aria-label="站点内容">'
        '<a href="/history/">FOMC 会议概率历史</a><a href="/analysis/">概率变化与事件分析</a><a href="/data/">CSV 数据下载</a>'
        '<a href="/methodology/">数据来源与方法</a><a href="/about/">独立项目说明</a><a href="/en/">English summary</a></div>'
    )
    # 按前缀定位插入点：首页副标题的文案会随口径调整，写死整行会在改文案时静默失效
    needle_prefix = '<div class="sub">数据来源 CME QuikStrike FedWatch 工具'
    at = home_html.find(needle_prefix)
    if at >= 0:
        end = home_html.find("</div>", at)
        home_html = home_html[:end + len("</div>")] \
            + "\n" + summary + home_html[end + len("</div>"):]
    meeting_links = "".join(f'<a href="/meetings/{esc(r["meeting_date"])}/">{esc(r["meeting_date"])} 会议历史</a>' for r in latest)
    footer = f"""<footer class="seo-footer"><strong>{SITE_NAME}</strong> 是独立研究与数据追踪项目，不隶属于 CME Group 或美联储。本站不披露项目维护者个人信息。<br>
<a href="/history/">会议历史</a> · <a href="/analysis/">变化分析</a> · <a href="/data/">数据下载</a> · <a href="/methodology/">方法与来源</a> · <a href="/about/">关于本站</a> · <a href="/en/">English</a><br>{meeting_links}</footer>"""
    home_html = home_html.replace("<script>\n(function(){", footer + "\n<script>\n(function(){", 1)
    return home_html


def meeting_page(rows: list[dict], meeting: str, latest_key: str, meeting_records: dict[str, dict]) -> str:
    usd = us_day_map(rows)
    latest_us_day = max(usd.values()) if usd else latest_key[:10]
    points = valid_rows_for_meeting(rows, meeting)
    latest = meeting_records[meeting]
    # 已结束的会议用「它最后一次出现在抓取表里的快照」，而不是最新快照
    record_key = (latest.get("snapshot_cn") or latest_key).strip() or latest_key
    record_day = usd.get(record_key, record_key[:10])
    concluded = meeting < latest_us_day
    first = usd.get(points[0]["snapshot_cn"], points[0]["snapshot_cn"][:10]) if points else "—"
    d1, d5 = delta(points, 1), delta(points, 5)
    description = f"{meeting} FOMC 会议的 FedWatch 加息、维持、降息概率与历史最大概率利率区间，更新于 {record_day}（美东交易日）。"
    title = f"{meeting} FOMC 概率历史：加息/维持/降息 | FedWatch Tracker"
    distribution = {}
    try:
        distribution = json.loads(latest.get("aggregated_ranges") or "{}")
    except json.JSONDecodeError:
        pass
    dist_rows = "".join(
        f"<tr><td>{range_pct(label)}</td><td class=\"num\">{float(value)*100:.2f}%</td></tr>"
        for label, value in sorted(distribution.items(), key=lambda x: int(x[0].split("-")[0]))
    )
    history_rows = "".join(
        f'<tr><td><time datetime="{esc(usd.get(r["snapshot_cn"], r["snapshot_cn"][:10]))}">'
        f'{esc(usd.get(r["snapshot_cn"], r["snapshot_cn"][:10]))}</time></td>'
        f'<td>{range_pct(r["max_range_label"])}</td><td class="num">{pct(r["max_range_pct"])}</td></tr>'
        for r in reversed(points)
    )
    concl_row = (
        f'<div class="notice">该会议已于 {meeting} 结束，本页作为历史档案保留，'
        f'数值为会议结束前最后一次采集快照（美东 {record_day}）。'
        f'查看当前市场预期请回到<a href="/">首页</a>或<a href="/history/">会议历史</a>。</div>\n'
        if concluded else ""
    )
    body = f"""<div class="breadcrumbs"><a href="/">首页</a> / <a href="/history/">会议历史</a> / {meeting}</div>
<h1>{meeting} FOMC 会议概率历史</h1><p class="lede">追踪市场对 {meeting} FOMC 会议后目标利率区间的概率分布。页面同时提供{'结束前最后一次' if concluded else '最新'}累计加息、维持、降息概率，以及过去一年的最大概率区间历史。</p>
{concl_row}<div class="meta">最新数据点：<time datetime="{esc(record_day)}">{esc(record_day)}</time>（美东交易日） · 首个有效历史点：{first} · 有效历史点：{len(points)}</div>
<div class="grid"><div class="card metric"><span>累计加息概率</span><strong>{pct(latest.get('agg_p_hike_pct'))}</strong></div><div class="card metric"><span>累计维持概率</span><strong>{pct(latest.get('agg_p_hold_pct'))}</strong></div><div class="card metric"><span>累计降息概率</span><strong>{pct(latest.get('agg_p_cut_pct'))}</strong></div></div>
<div class="notice">最新最大概率目标区间为 <strong>{range_pct(latest.get('max_range_label',''))}</strong>，概率 <strong>{pct(latest.get('max_range_pct'))}</strong>；较上一有效交易日变化 <strong>{delta_text(d1)}</strong>，较 5 个有效交易日前变化 <strong>{delta_text(d5)}</strong>。</div>
<h2>最新目标利率区间概率分布</h2><div class="table-wrap"><table><thead><tr><th>目标利率区间</th><th class="num">概率</th></tr></thead><tbody>{dist_rows}</tbody></table></div>
<h2>最大概率区间历史</h2><p class="small">“最大概率区间”是每个快照中概率最高的一档目标利率区间，日期为对应的<b>美东交易日</b>。区间标签切换时，数值序列会直接连接到新的最高概率档。</p><div class="table-wrap"><table><thead><tr><th>美东交易日</th><th>最大概率区间</th><th class="num">该区间概率</th></tr></thead><tbody>{history_rows}</tbody></table></div>
<h2>来源与口径</h2><p>数据来自 <a href="{CME_URL}" rel="nofollow">CME FedWatch Tool / QuikStrike Aggregated View 与 Historical Downloads</a>。本站每个美东交易日在收盘后的休市间隙采集一次（北京时间次日 05:30 / 06:30），一个点 = 该交易日的收盘定格，不保证与稍后打开的盘中实时值相同。详细说明见<a href="/methodology/">方法页</a>，完整 CSV 见<a href="/data/">数据下载页</a>。</p>"""
    schema = {
        "@context": "https://schema.org", "@type": "Dataset", "name": f"{meeting} FOMC FedWatch Probability History",
        "description": description, "url": canonical(f"/meetings/{meeting}/"), "dateModified": record_day,
        "temporalCoverage": f"{first}/{record_day}", "isBasedOn": CME_URL,
        "distribution": [{"@type": "DataDownload", "encodingFormat": "text/csv", "contentUrl": canonical(f"/data/meetings/{meeting}.csv")}],
    }
    return page_document(title=title, description=description, path=f"/meetings/{meeting}/", body=body, schema=schema)


def history_page(rows: list[dict], latest_key: str, latest: list[dict],
                 archived: dict[str, dict] | None = None) -> str:
    usd = us_day_map(rows)
    first_day = lambda p: (usd.get(p[0]["snapshot_cn"], p[0]["snapshot_cn"][:10]) if p else "—")  # noqa: E731
    rows_html = []
    for item in latest:
        meeting = item["meeting_date"]
        points = valid_rows_for_meeting(rows, meeting)
        rows_html.append(
            f'<tr><td><a href="/meetings/{meeting}/">{meeting}</a></td><td>{range_pct(item.get("max_range_label",""))}</td>'
            f'<td class="num">{pct(item.get("max_range_pct"))}</td><td class="num">{pct(item.get("agg_p_hike_pct"))}</td>'
            f'<td class="num">{delta_text(delta(points,1))}</td><td>{first_day(points)}</td><td class="num">{len(points)}</td></tr>'
        )
    archived_html = []
    for meeting in sorted(archived or {}):
        points = valid_rows_for_meeting(rows, meeting)
        archived_html.append(
            f'<tr><td><a href="/meetings/{meeting}/">{meeting}</a></td>'
            f'<td>{usd.get(points[-1]["snapshot_cn"], points[-1]["snapshot_cn"][:10]) if points else "—"}</td>'
            f'<td class="num">{len(points)}</td></tr>'
        )
    title = "FOMC 会议 FedWatch 概率历史索引 | FedWatch Tracker"
    description = "按未来 FOMC 会议浏览 FedWatch 加息、维持、降息概率和最大概率目标利率区间的历史变化。"
    archived_block = (
        '<h2>已结束会议（历史档案）</h2>'
        '<div class="table-wrap"><table><thead><tr><th>会议日期</th><th>最后采集日</th>'
        f'<th class="num">历史点数</th></tr></thead><tbody>{"".join(archived_html)}</tbody></table></div>'
        if archived_html else ""
    )
    body = f"""<div class="breadcrumbs"><a href="/">首页</a> / 会议历史</div><h1>FOMC 会议概率历史</h1>
<p class="lede">每个会议拥有独立、稳定的历史页面，便于搜索、引用和比较市场对不同 FOMC 决议日的利率预期。</p><div class="meta">最近更新：<time datetime="{esc(usd.get(latest_key, latest_key[:10]))}">{esc(usd.get(latest_key, latest_key[:10]))}</time>（美东交易日）</div>
<div class="table-wrap"><table><thead><tr><th>会议日期</th><th>最新最大概率区间</th><th class="num">区间概率</th><th class="num">累计加息</th><th class="num">日变化</th><th>首个有效点</th><th class="num">历史点数</th></tr></thead><tbody>{''.join(rows_html)}</tbody></table></div>
<p class="small">累计加息/维持/降息只展示最新实时快照；历史下载数据主要提供各绝对利率区间的概率，因此会议页的历史表追踪“最大概率区间”。</p>
{archived_block}"""
    return page_document(title=title, description=description, path="/history/", body=body)


def analysis_page(rows: list[dict], latest_key: str) -> str:
    """Publish auditable event annotations as a searchable, stable analysis page."""
    events_path = BASE_DIR / "data" / "events.csv"
    events: list[dict] = []
    if events_path.exists():
        with events_path.open(encoding="utf-8-sig", newline="") as f:
            events = [r for r in csv.DictReader(f) if (r.get("snapshot_cn") or "").strip()]
    events.sort(key=lambda r: r.get("snapshot_cn", ""), reverse=True)
    usd = us_day_map(rows)
    usd_state_day = usd.get(latest_key, latest_key[:10])
    rows_html = []
    for event in events:
        snap = (event.get("snapshot_cn") or "").strip()
        day = usd.get(snap, snap[:10])
        direction = (event.get("direction") or "").strip()
        direction_label = "最大概率区间上升" if direction == "hawk" else "最大概率区间下降" if direction == "dove" else "方向未标记"
        status = "待核实" if "待核实" in (event.get("summary", "") + event.get("text", "")) else "已记录"
        source = (event.get("url") or "").strip()
        # url 字段可挂多条来源（| 分隔）；单条保持原「来源」文案，多条编号区分
        sources = [u.strip() for u in source.split("|") if u.strip().startswith("http")]
        if len(sources) == 1:
            source_link = f'<a href="{esc(sources[0])}" rel="nofollow noopener">来源</a>'
        elif sources:
            source_link = " ".join(
                f'<a href="{esc(u)}" rel="nofollow noopener">来源{i + 1}</a>'
                for i, u in enumerate(sources)
            )
        else:
            source_link = "—"
        rows_html.append(
            f'<tr><td><time datetime="{esc(day)}">{esc(day)}</time></td>'
            f'<td>{esc(direction_label)}</td><td>{esc(event.get("summary") or "—")}</td>'
            f'<td>{esc(status)}</td><td>{source_link}</td></tr>'
        )
    title = "美联储利率概率变化与事件分析 | FedWatch Tracker"
    description = "按日期记录 FedWatch 概率显著变化、最大概率区间方向、事件摘要和可核对来源；不把叙事归因当作因果模型。"
    body = f"""<div class="breadcrumbs"><a href="/">首页</a> / 变化分析</div><h1>FedWatch 概率变化与事件分析</h1>
<p class="lede">本页记录历史快照中较显著的概率变化，以及同一时间窗口内可核对的事件线索，帮助读者理解市场预期如何变化。</p>
<div class="notice"><strong>阅读口径：</strong>“上升/下降”指当日变化最大的目标利率区间概率变化，不等同于累计加息概率或新闻的宏观鹰派/鸽派判断。事件归因是叙事归档，不是因果模型；标为“待核实”的记录不应被视为已确认因果。</div>
<div class="meta">记录数：{len(events)} · 页面最近更新：<time datetime="{esc(usd.get(latest_key, latest_key[:10]))}">{esc(usd.get(latest_key, latest_key[:10]))}</time>（美东交易日）</div>
<div class="table-wrap"><table><thead><tr><th>美东交易日</th><th>区间方向</th><th>事件摘要</th><th>状态</th><th>来源</th></tr></thead><tbody>{''.join(rows_html)}</tbody></table></div>
<h2>数据与方法</h2><p>概率快照来自 <a href="{CME_URL}" rel="nofollow">CME FedWatch / QuikStrike</a>，本站按美东交易日在收盘后定时保存。完整字段和下载文件见<a href="/data/">数据下载页</a>，归因口径和限制见<a href="/methodology/">方法页</a>。每个日期仍可从<a href="/history/">会议历史</a>进入具体会议页面。</p>"""
    schema = {
        "@context": "https://schema.org", "@type": "Dataset",
        "name": "FedWatch Probability Change and Event Annotations",
        "description": description, "url": canonical("/analysis/"), "dateModified": usd_state_day,
        "isBasedOn": CME_URL,
    }
    return page_document(title=title, description=description, path="/analysis/", body=body, schema=schema)


def methodology_page(latest_key: str, rows: list[dict] | None = None) -> str:
    usd = us_day_map(rows or [])
    latest_day = usd.get(latest_key, latest_key[:10])
    title = "数据来源、采集时间与计算口径 | FedWatch Tracker"
    description = "说明 FedWatch Tracker 的 CME QuikStrike 数据来源、每个美东交易日收盘后的采集时点、Aggregated 概率口径与历史数据限制。"
    body = f"""<div class="breadcrumbs"><a href="/">首页</a> / 方法</div><h1>数据来源与方法</h1>
<p class="lede">本站的目标是保存可审计的 FedWatch 概率快照与历史变化，而不是替代 CME 的盘中实时工具。</p>
<h2>数据来源</h2><p>最新概率来自 <a href="{CME_URL}" rel="nofollow">CME FedWatch Tool 的 QuikStrike Aggregated View</a>；历史区间数据来自同一工具的 Historical Downloads。FOMC 日期参考<a href="{FED_CALENDAR_URL}" rel="nofollow">美联储 FOMC 官方日历</a>。</p>
<h2>更新时点</h2><p>采集目标是<b>每个美东交易日的收盘定格值</b>：任务在北京时间 <b>05:30</b>（美国夏令时）与 <b>06:30</b>（冬令时）各触发一次，两者折算到芝加哥时间都是<b>前一日 16:30 CT</b> —— 即 CME 每日 16:00–17:00 CT 休市间隙的中点，此时上一个交易日刚收盘、新时段尚未开盘。不在该窗口内的那次触发不会启动浏览器、也不会写入数据；成功采集后失败重试最多三次。CME 页面会随联邦基金期货盘中价格变化，因此本站显示的是带时间戳的定时快照，不承诺与用户稍后打开 CME 页面时的数值相同。当前最新数据点为 <time datetime="{esc(latest_day)}">{esc(latest_day)}</time>（美东交易日）。</p>
<h2>Aggregated 概率</h2><p>累计加息、维持和降息概率以当前目标区间为基准，将会议后所有更高、相同或更低的目标区间概率分别求和。本站直接保存 QuikStrike 页面展示值，不自行替代官方页面的内部计算。</p>
<h2>最大概率区间历史</h2><p>历史折线选择每个交易日概率最高的目标利率区间，并记录该区间及其概率。若市场最可能区间发生切换，折线会直接连接到新最高概率档。远期会议尚无有效定价时的全零占位行不绘制。</p>
<h2>交易日和时区</h2><p>横轴与各页显示的日期统一为<b>美东交易日</b>（<code>us_trade_date</code> 列）：一个点 = 一个已收盘的美东交易日。因北京无夏令时而美东有，采集点会比北京时间早一天出现在横轴上（北京 09-18 采集 → 横轴 09-17），这是口径使然，不是数据延迟。CME 周五 16:00 CT 收盘后要到周日 17:00 CT 才重开，因此周六/周日/周一北京早上的三次采集读到的是同一份周五收盘定格，本站按「一个交易日一个点」去重并保留最早那一次。原始的采集时刻、数据时点与口径标记都逐条保存在 <code>data/snapshots/YYYY-MM-DD.json</code> 与 CSV 的 <code>data_asof_ct</code> / <code>time_basis</code> 列中。</p>
<h2>限制与免责声明</h2><ul><li>本站是独立项目，不隶属于 CME Group 或美联储。</li><li>数据仅供研究和信息参考，不构成投资建议。</li><li>历史下载和定时快照可能与盘中实时值存在差异。</li><li>“FedWatch”是 CME 产品名称；本站引用该名称仅用于描述数据来源和主题。</li></ul>"""
    return page_document(title=title, description=description, path="/methodology/", body=body)


def data_page(rows: list[dict], latest_key: str) -> str:
    usd = us_day_map(rows)
    first = min(usd.values()) if usd else latest_key[:10]
    last = max(usd.values()) if usd else latest_key[:10]
    meetings = len({r["meeting_date"] for r in rows})
    title = "FedWatch 历史概率 CSV 与数据字典 | FedWatch Tracker"
    description = "下载 FedWatch Tracker 的 FOMC 概率历史 CSV，查看字段定义、更新时间、覆盖范围和数据来源。"
    fields = [
        ("snapshot_cn", "本站采集时刻（北京时间；排序键与事件关联键，不建议作为横轴）"),
        ("us_trade_date", "该数据对应的**美东交易日**，看板横轴用这一列"),
        ("data_asof_ct", "页面自带的 “Data as of” 数据时点（CT），回填行为空"),
        ("time_basis", "口径来源：page_asof（收盘后采集）/ settlement_export（官方结算导出）/ legacy_intraday（早期盘中读数）"),
        ("snapshot_quikstrike", "QuikStrike 页面显示的数据时间原文"),
        ("meeting_date", "FOMC 决议日期"), ("current_target", "当前联邦基金目标区间，单位 bp"),
        ("agg_p_hike_pct / hold / cut", "最新快照的累计加息、维持、降息概率"),
        ("max_range_label / max_range_pct", "该会议概率最高的目标区间及其概率"),
        ("aggregated_ranges", "所有目标区间概率的 JSON 对象"), ("range_*_pct", "常用目标区间的独立概率列"),
    ]
    field_rows = "".join(f"<tr><td><code>{esc(k)}</code></td><td>{esc(v)}</td></tr>" for k, v in fields)
    body = f"""<div class="breadcrumbs"><a href="/">首页</a> / 数据下载</div><h1>历史概率数据下载</h1>
<p class="lede">下载本站用于看板和会议历史页的处理后时间序列。数据覆盖美东交易日 {first} 至 {last}，包含 {len(rows):,} 行、{meetings} 个未来会议标识。</p>
<div class="card"><h2 style="margin-top:0">主数据集</h2><p><a href="/data/fedwatch-probabilities.csv" download><strong>下载 fedwatch-probabilities.csv</strong></a> · UTF-8 · 每日构建更新</p><p class="small">来源：CME QuikStrike FedWatch Aggregated View / Historical Downloads。使用或再发布时请保留来源和快照时间说明，并遵守原始数据源适用条款。</p></div>
<h2>按会议下载</h2><div class="site-links">{''.join(f'<a href="/data/meetings/{m}.csv" download>{m}.csv</a> ' for m in sorted({r['meeting_date'] for r in rows}))}</div>
<h2>字段说明</h2><div class="table-wrap"><table><thead><tr><th>字段</th><th>含义</th></tr></thead><tbody>{field_rows}</tbody></table></div>
<h2>引用建议</h2><p>引用数字时请同时注明：会议日期、对应的美东交易日、概率口径及数据来源。例如：“FedWatch Tracker，美东交易日 {esc(last)} 收盘快照，数据源 CME QuikStrike Aggregated View”。</p>"""
    schema = {
        "@context": "https://schema.org", "@type": "Dataset", "name": "FedWatch Tracker Historical FOMC Rate Probabilities",
        "description": description, "url": canonical("/data/"), "dateModified": last, "temporalCoverage": f"{first}/{last}",
        "isBasedOn": CME_URL, "distribution": [{"@type": "DataDownload", "encodingFormat": "text/csv", "contentUrl": canonical("/data/fedwatch-probabilities.csv")}],
    }
    return page_document(title=title, description=description, path="/data/", body=body, schema=schema)


def about_page() -> str:
    title = "关于 FedWatch Tracker | 独立美联储利率概率历史项目"
    description = "FedWatch Tracker 是一个独立的美联储利率概率快照和历史追踪项目，不隶属于 CME Group 或美联储。"
    body = """<div class="breadcrumbs"><a href="/">首页</a> / 关于</div><h1>关于本站</h1>
<p class="lede">FedWatch Tracker 保存未来 FOMC 会议的利率概率快照、历史变化和重大事件归因，使研究者能够查看市场预期如何随时间变化。</p>
<h2>项目定位</h2><p>本站聚焦于 CME 官方工具之外更方便引用的中文历史记录、会议级稳定页面、可下载数据和变化说明。它不是交易终端，也不替代官方实时行情。</p>
<h2>独立性</h2><p>本站是独立研究与数据追踪项目，不隶属于、不代表、也未获 CME Group 或美国联邦储备系统背书。</p>
<h2>隐私</h2><p>当前版本不公开项目维护者的姓名、组织、联系方式或其他个人身份信息。后续若增加反馈渠道，会在不披露不必要个人信息的前提下单独说明。</p>
<h2>准确性</h2><p>本站保留数据来源、快照时间和采集口径，尽力保证页面与存档一致。如果 CME 页面在盘中发生变化，本站的定时快照不会被描述为同一时刻的实时值。</p>"""
    return page_document(title=title, description=description, path="/about/", body=body)


def english_page(latest_key: str, latest: list[dict]) -> str:
    cards = "".join(
        f'<div class="card"><h3><a href="/meetings/{r["meeting_date"]}/">{r["meeting_date"]} FOMC</a></h3><p>Hike <strong>{pct(r.get("agg_p_hike_pct"))}</strong> · Hold <strong>{pct(r.get("agg_p_hold_pct"))}</strong> · Cut <strong>{pct(r.get("agg_p_cut_pct"))}</strong></p><p class="small">Most likely target range: {range_pct(r.get("max_range_label",""))} ({pct(r.get("max_range_pct"))})</p></div>'
        for r in latest[:3]
    )
    title = "FedWatch Tracker: Fed Rate Hike and Cut Probability History"
    description = "Daily CME FedWatch probability snapshots, historical FOMC target-rate distributions, meeting-level history and downloadable CSV data."
    latest_day = (latest[0].get("us_trade_date") or "").strip() if latest else ""
    if not latest_day:
        latest_day = latest_key[:10]
    body = f"""<div class="breadcrumbs"><a href="/en/">Home</a></div><h1>Fed Rate Probability History</h1>
<p class="lede">FedWatch Tracker publishes daily closing snapshots of rate-hike, hold and rate-cut probabilities for upcoming FOMC meetings, plus one-year meeting-level history and downloadable data. One point = one US trading day, taken after the 16:00 CT close.</p><div class="meta">Latest data point: <time datetime="{esc(latest_day)}">{esc(latest_day)}</time> (US trading day)</div>
<div class="notice">This is an independent research and data-tracking project. It is not affiliated with CME Group or the Federal Reserve. Values are scheduled snapshots, not guaranteed intraday real-time quotes.</div>
<h2>Upcoming meetings</h2><div class="grid">{cards}</div><h2>Explore the data</h2><ul><li><a href="/history/">Meeting-level probability history</a></li><li><a href="/data/">Download historical CSV data</a></li><li><a href="/methodology/">Source, snapshot time and methodology</a></li><li><a href="/">Chinese interactive dashboard</a></li></ul>"""
    return page_document(title=title, description=description, path="/en/", body=body, lang="en", alternate_zh="/", alternate_en="/en/")


def write_csv_downloads(rows: list[dict]) -> None:
    data_dir = REPORT_DIR / "data"
    meetings_dir = data_dir / "meetings"
    meetings_dir.mkdir(parents=True, exist_ok=True)
    source = BASE_DIR / "data" / "fedwatch_probabilities.csv"
    shutil.copyfile(source, data_dir / "fedwatch-probabilities.csv")
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    by_meeting: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_meeting[row["meeting_date"]].append(row)
    for meeting, meeting_rows in by_meeting.items():
        with (meetings_dir / f"{meeting}.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(meeting_rows)


def write_assets(latest_key: str) -> None:
    assets = REPORT_DIR / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    favicon = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="14" fill="#1d4ed8"/><path d="M12 43 23 31l9 7 19-22" fill="none" stroke="#fff" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/><circle cx="51" cy="16" r="4" fill="#fff"/></svg>"""
    (REPORT_DIR / "favicon.svg").write_text(favicon, encoding="utf-8")
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#0f172a"/><stop offset="1" stop-color="#1d4ed8"/></linearGradient></defs><rect width="1200" height="630" fill="url(#g)"/><path d="M90 438 250 306l145 87 220-226 180 91 280-160" fill="none" stroke="#93c5fd" stroke-width="18" stroke-linecap="round" stroke-linejoin="round" opacity=".9"/><text x="90" y="155" font-family="Arial,sans-serif" font-size="74" font-weight="700" fill="white">FedWatch Tracker</text><text x="90" y="225" font-family="Arial,sans-serif" font-size="34" fill="#dbeafe">Fed rate probability history · FOMC meeting snapshots</text><text x="90" y="556" font-family="Arial,sans-serif" font-size="25" fill="#bfdbfe">Updated {esc(latest_key[:10])} · Independent data-tracking project</text></svg>"""
    svg_path = assets / "og-image.svg"
    svg_path.write_text(svg, encoding="utf-8")
    png_path = assets / "og-image.png"
    try:
        subprocess.run(["/usr/bin/sips", "-s", "format", "png", str(svg_path), "--out", str(png_path)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        # Keep the build deterministic even outside macOS. A later validation will
        # only require the SVG if PNG conversion is unavailable.
        pass


def curves_page_url() -> str | None:
    """双图页（report/curves.html，由 build_curves.py 生成）的规范地址。

    文件不存在时返回 None —— sitemap / llms.txt 只在页面真实存在时收录，
    避免 build_report 先于 build_curves 运行的首轮部署挂出死链。
    """
    if (REPORT_DIR / "curves.html").is_file():
        return canonical("/curves")
    return None


def write_robots_sitemap(paths: list[str], latest_key: str, extras: tuple[str, ...] = ()) -> None:
    robots = f"""User-agent: *
Allow: /

User-agent: OAI-SearchBot
Allow: /

Sitemap: {canonical('/sitemap.xml')}
"""
    (REPORT_DIR / "robots.txt").write_text(robots, encoding="utf-8")
    # extras：不对应 write_page 产物的页面（如 curves.html 这种文件型页面），
    # 只进 sitemap、不参与 validate_site 的 canonical 逐页校验。
    entries = "".join(
        f"<url><loc>{esc(canonical(path))}</loc><lastmod>{latest_key[:10]}</lastmod></url>"
        for path in list(paths) + list(extras)
    )
    sitemap = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</urlset>'
    (REPORT_DIR / "sitemap.xml").write_text(sitemap, encoding="utf-8")


def write_404() -> None:
    title = "页面未找到 | FedWatch Tracker"
    description = "请求的页面不存在。返回 FedWatch Tracker 首页、会议历史或数据下载页。"
    body = """<div class="breadcrumbs"><a href="/">首页</a> / 404</div><h1>页面未找到</h1><p class="lede">这个地址不存在，可能已被移动或输入有误。</p><p><a href="/">返回首页</a> · <a href="/history/">浏览会议历史</a> · <a href="/data/">下载数据</a></p>"""
    doc = page_document(title=title, description=description, path="/404.html", body=body)
    (REPORT_DIR / "404.html").write_text(doc, encoding="utf-8")


def write_llms(latest_key: str, latest: list[dict]) -> None:
    meeting_lines = "\n".join(
        f"- {r['meeting_date']}: hike {pct(r.get('agg_p_hike_pct'))}, hold {pct(r.get('agg_p_hold_pct'))}, cut {pct(r.get('agg_p_cut_pct'))}; history {canonical(f'/meetings/{r['meeting_date']}/')}"
        for r in latest
    )
    text = f"""# FedWatch Tracker

Independent daily tracker of CME FedWatch / QuikStrike probability snapshots and historical FOMC target-rate distributions. Not affiliated with CME Group or the Federal Reserve.

Latest data point: US trading day {(latest[0].get("us_trade_date") or latest_key[:10]).strip() if latest else latest_key[:10]} (one point per US trading day, sampled after the 16:00 CT close). Values are scheduled snapshots, not guaranteed intraday real-time quotes.

Primary pages:
- Dashboard: {canonical('/')}
- Direction & magnitude charts (cumulative hike probability, expected bp): {curves_page_url() if curves_page_url() else '(not generated)'}
- Meeting history index: {canonical('/history/')}
- Probability change and event analysis: {canonical('/analysis/')}
- Methodology and source: {canonical('/methodology/')}
- Dataset and CSV downloads: {canonical('/data/')}
- English summary: {canonical('/en/')}

Upcoming meetings:
{meeting_lines}

Source provenance: {CME_URL}
"""
    (REPORT_DIR / "llms.txt").write_text(text, encoding="utf-8")


def copy_static_root_files() -> list[str]:
    """Copy durable root files such as search-engine ownership verification files."""
    copied: list[str] = []
    if not STATIC_DIR.is_dir():
        return copied
    for source in sorted(STATIC_DIR.glob("google*.html")):
        expected = f"google-site-verification: {source.name}"
        if source.read_text(encoding="utf-8").strip() != expected:
            raise RuntimeError(f"invalid Google verification file: {source}")
        shutil.copy2(source, REPORT_DIR / source.name)
        copied.append(source.name)
    return copied


def validate_site(expected_paths: list[str]) -> None:
    required = ["index.html", "robots.txt", "sitemap.xml", "404.html", "favicon.svg", "data/fedwatch-probabilities.csv", "analysis/index.html"]
    required.extend(source.name for source in STATIC_DIR.glob("google*.html")) if STATIC_DIR.is_dir() else None
    missing = [name for name in required if not (REPORT_DIR / name).is_file()]
    if missing:
        raise RuntimeError(f"missing generated site files: {missing}")
    ET.parse(REPORT_DIR / "sitemap.xml")
    robots = (REPORT_DIR / "robots.txt").read_text(encoding="utf-8")
    if "Sitemap:" not in robots or "Allow: /" not in robots:
        raise RuntimeError("robots.txt is incomplete")
    for path in expected_paths:
        target = REPORT_DIR / "index.html" if path == "/" else REPORT_DIR / path.strip("/") / "index.html"
        if not target.exists():
            raise RuntimeError(f"missing page for {path}: {target}")
        page = target.read_text(encoding="utf-8")
        expected = f'<link rel="canonical" href="{canonical(path)}">'
        if expected not in page:
            raise RuntimeError(f"canonical mismatch in {target}")
        if "meta name=\"description\"" not in page or ("application/ld+json" not in page and path in ("/", "/data/")):
            raise RuntimeError(f"SEO metadata incomplete in {target}")
        lowered = page.lower()
        for forbidden in ("mailto:", "维护者姓名", "maintainer name"):
            if forbidden in lowered:
                raise RuntimeError(f"maintainer disclosure found in {target}")



def clean_generated_site() -> None:
    """Remove only files/directories owned by this generator to prevent stale URLs."""
    for name in ("history", "analysis", "methodology", "data", "about", "en", "meetings", "assets"):
        target = REPORT_DIR / name
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    for name in ("robots.txt", "sitemap.xml", "404.html", "llms.txt", "favicon.svg", "_headers"):
        target = REPORT_DIR / name
        if target.exists():
            target.unlink()

def build_static_site(home_html: str, rows: list[dict]) -> tuple[str, list[str]]:
    if not rows:
        raise RuntimeError("cannot build site without probability data")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    clean_generated_site()
    latest_key, latest = latest_rows(rows)
    records = page_meetings(rows, latest)
    latest_by_meeting = {r["meeting_date"]: r for r in latest}
    archived = {m: r for m, r in records.items() if m not in latest_by_meeting}
    enhanced_home = build_homepage_meta(home_html, rows, latest_key, latest)
    write_page("/history/", history_page(rows, latest_key, latest, archived))
    write_page("/analysis/", analysis_page(rows, latest_key))
    write_page("/methodology/", methodology_page(latest_key, rows))
    write_page("/data/", data_page(rows, latest_key))
    write_page("/about/", about_page())
    write_page("/en/", english_page(latest_key, latest))
    meeting_paths = []
    for meeting in sorted(records):
        path = f"/meetings/{meeting}/"
        write_page(path, meeting_page(rows, meeting, latest_key, records))
        meeting_paths.append(path)
    write_csv_downloads(rows)
    write_assets(latest_key)
    write_404()
    paths = ["/", "/history/", "/analysis/", "/methodology/", "/data/", "/about/", "/en/", *meeting_paths]
    write_robots_sitemap(paths, latest_key,
                         extras=("/curves",) if curves_page_url() else ())
    write_llms(latest_key, latest)
    copy_static_root_files()
    (REPORT_DIR / "_headers").write_text(
        "/robots.txt\n  Content-Type: text/plain; charset=utf-8\n/sitemap.xml\n  Content-Type: application/xml; charset=utf-8\n/data/*.csv\n  Content-Type: text/csv; charset=utf-8\n",
        encoding="utf-8",
    )
    return enhanced_home, paths
