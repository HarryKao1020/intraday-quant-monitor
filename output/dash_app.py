# output/dash_app.py
"""
盤中量化監看儀表板 — 深色網頁風
─────────────────────────────
柔和深色介面：文字以白／灰為主，紅綠僅用於漲跌幅與量比；
等寬字維持表格對齊，無 CRT 特效。

已接：模組一（股期標的爆量）、模組二（市場廣度）、模組四（漲跌停統計）、API 流量。
模組三（MACD）以 OFFLINE 占位。

內建 30 秒排程更新 MARKET_STATE，dcc.Interval 每 1 秒重繪。
執行：python -m output.dash_app   →  http://127.0.0.1:8050
"""
import threading
import unicodedata
import uuid
import webbrowser
from datetime import datetime

from dash import Dash, dcc, html, Input, Output, State, ALL, ctx, no_update
import plotly.graph_objects as go
from apscheduler.schedulers.background import BackgroundScheduler

from data.session import init_api, logout_api
from data.scanner import (
    build_high_price_universe, update_scanners, update_index_quotes,
)
from data.usage import update_usage
from data.fetcher import prefetch_all
from data.holdings import (
    get_stock_futures_watchlist, update_holdings_volume, sync_holdings, logout_prod,
)
from data.high52w import build_high52w
from data.regulatory import update_regulatory
from data.performance import build_performance, refresh_today_point, daily_perf_job
from data.industry import update_industry_flow, build_week_baseline
from compute.industry_flow import get_industry_display, get_market_total
from compute.market_breadth import calc_market_breadth
from compute.limit_stats import get_limit_stats
from compute.usage_stats import get_usage
from compute.volume_surge import calc_volume_surge
from compute.macd import get_macd_display
from compute.bias import get_bias_display
from compute.new_high import get_new_highs
from compute.ma_break import get_ma_break, get_ma_bias_top
from store.memory_store import MARKET_STATE
from config import (
    SCANNER_INTERVAL_SEC, HIGH_PRICE_COUNT, BIAS_PERIODS, HOLDINGS_SYNC_SEC,
    PERF_REFRESH_SEC, PERF_SNAPSHOT_TIME, MA_BREAK_PERIODS,
)

# ── 配色（深色網頁，柔和）────────────────────────────────
TEXT   = "#e6edf3"   # 主要文字（白）
DIM    = "#8b949e"   # 標籤 / 次要（灰）
FAINT  = "#3a4250"   # 分隔 / 占位
ACCENT = "#5fb37a"   # 柔綠（區段標題 / LIVE）
AMBER  = "#d6a740"   # 警告 / OFFLINE
UP     = "#f0716b"   # 漲（柔紅）
DOWN   = "#56b877"   # 跌（柔綠）
SURGE  = "#6fcf8f"   # 爆量觸發（柔綠）
CODE   = "#79c0ff"   # 代碼（柔藍中性強調）

FONT = "'JetBrains Mono','Fira Code',Menlo,Consolas,monospace"

# 每次伺服器啟動的識別碼：分頁載入時烙進 layout，之後每次 tick 比對，
# 不一致（= 伺服器已重啟）就自動重新整理頁面，避免舊分頁凍住。
_BOOT_ID = uuid.uuid4().hex


def _w(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _sp(text, color=TEXT, bold=False):
    style = {"color": color}
    if bold:
        style["fontWeight"] = "600"
    return html.Span(text, style=style)


def _line(*spans):
    return html.Div(list(spans), style={"whiteSpace": "pre"})


def _blank():
    return html.Div(" ", style={"whiteSpace": "pre", "lineHeight": "0.6"})


def _padr(s, width):
    return s + " " * max(0, width - _w(s))


def _padl(s, width):
    return " " * max(0, width - _w(s)) + s


# ── CSS 固定欄寬（表頭與資料同寬，對齊不受中文寬度影響）──
def _col(text, ch, align="right", color=TEXT, bold=False):
    style = {
        "display": "inline-block", "width": f"{ch}ch", "textAlign": align,
        "color": color, "whiteSpace": "nowrap", "overflow": "hidden",
    }
    if bold:
        style["fontWeight"] = "600"
    return html.Span(text, style=style)


def _trow(*cells):
    return html.Div(list(cells), style={"whiteSpace": "nowrap", "paddingLeft": "2px"})


def _bar(pct, width=34, full="█", empty="░"):
    pct = max(0.0, min(pct, 100.0))
    f = int(round(pct / 100 * width))
    return full * f + empty * (width - f)


def _chg(v):
    """漲跌幅上色：漲紅、跌綠、平灰。"""
    col = UP if v > 0 else (DOWN if v < 0 else DIM)
    return f"{v:+.2f}%", col


def _modhead(tag, title, status, status_color):
    """區段標題列（tag 保留參數但不顯示編號）。"""
    return html.Div(className="modhead", children=[
        html.Span(title, className="mh-title"),
        html.Span(status, className="mh-badge",
                  style={"color": status_color, "borderColor": status_color}),
    ])


# ── 資料更新警示（任一來源抓取失敗 / 行情停更時顯示）────
def render_alert_banner():
    msgs = []
    # 行情排程停更偵測：例外會進 data_errors，這裡抓「卡住沒回來」的情況
    lag = (datetime.now() - MARKET_STATE.last_scanner_update).total_seconds()
    if lag > SCANNER_INTERVAL_SEC * 3:
        msgs.append(f"行情已 {int(lag // 60)} 分鐘未更新（排程卡住或連線中斷）")
    for src, info in sorted(MARKET_STATE.data_errors.items()):
        msgs.append(f"{src}更新失敗 {info['time']:%H:%M:%S}｜{info['msg']}")
    if not msgs:
        return []
    return [html.Div(className="alert-banner", children=[
        html.Div(f"⚠ 資料更新異常（{len(msgs)}）", className="alert-title"),
        *[html.Div(m) for m in msgs],
    ])]


# ── API 流量 ──────────────────────────────────────────────
def render_usage_lines():
    u = get_usage()
    if not u["limit_bytes"]:
        return [_modhead("SYS", "API 流量", "INIT", AMBER)]
    scol = {"ok": ACCENT, "warn": AMBER, "alert": UP}.get(u["status"], ACCENT)
    stag = {"ok": "OK", "warn": "WARN", "alert": "ALERT"}.get(u["status"], "OK")
    return [
        _modhead("SYS", "API 流量", stag, scol),
        _line(_sp("  ", DIM), _sp("[", DIM), _sp(_bar(u["used_pct"]), scol),
              _sp("] ", DIM), _sp(f"{u['used_pct']:.1f}%", TEXT, bold=True)),
        _line(_sp("  已用 ", DIM), _sp(u["used_human"], TEXT),
              _sp(" / ", DIM), _sp(u["limit_human"], TEXT),
              _sp("   剩 ", DIM), _sp(u["remaining_human"], TEXT),
              _sp("   連線 ", DIM), _sp(f"{u['connections']}/5", TEXT)),
    ]


# ── 模組一：股期標的爆量 ──────────────────────────────────
# 預估比狀態 → (顏色, 加粗)
_VOL_STATUS = {
    "爆量": (SURGE, True),   # 亮綠·粗
    "量縮": (DIM, False),    # 灰
    "正常": (TEXT, False),   # 白
    "—":    (FAINT, False),
}


def _yi(v) -> str:
    """指數量能顯示：成交金額（元）→ 億元，如 8,911億。"""
    return f"{v / 1e8:,.0f}億" if v > 0 else "—"


def _vol_emit(r):
    scol, sbold = _VOL_STATUS.get(r["status"], (TEXT, False))
    is_index = r.get("kind") == "index"
    if is_index:
        v5d_txt = _yi(r["v5d_avg"])
        cur_txt = _yi(r["today_vol"])
        est_txt = _yi(r["est_today"])
    else:
        v5d_txt = f"{int(r['v5d_avg']):,}"
        cur_txt = f"{r['today_vol']:,}"
        est_txt = f"{r['est_today']:,}" if r["est_today"] > 0 else "—"
    ratio_txt = f"{r['est_ratio']:.2f}" if r["est_ratio"] > 0 else "—"
    price = r.get("price", 0.0)
    chg = r.get("change_rate", 0.0)
    price_txt = f"{price:,.2f}" if price else "—"
    chg_txt, chg_col = (f"{chg:+.2f}%", (UP if chg > 0 else (DOWN if chg < 0 else DIM))) \
        if price else ("—", DIM)
    label = r["name"] if is_index else r["symbol"]
    name_txt = "" if is_index else r["name"]
    return _trow(
        _col(label, 10 if is_index else 7, "left", CODE),
        _col(name_txt, 7 if is_index else 10, "left", TEXT),
        _col(price_txt, 10, "right", TEXT),                  # 現價
        _col(chg_txt, 10, "right", chg_col, bold=True),      # 今日漲跌幅
        _col(v5d_txt, 10, "right", TEXT),                    # 5日均量
        _col(cur_txt, 10, "right", TEXT),                    # 現在量
        _col(est_txt, 11, "right", TEXT),                    # 預估今量
        _col(ratio_txt, 9, "right", scol, bold=sbold),       # 預估比
        _col("", 2), _col(r["status"], 8, "left", scol, bold=sbold),  # 狀態
    )


def render_module1_lines():
    rows = calc_volume_surge()
    stocks = [r for r in rows if r.get("kind") != "index"]
    if not rows or (not stocks and not any(r["price"] for r in rows)):
        return [_modhead("MOD.01", "量能監控", "WAIT", AMBER),
                _line(_sp("  盤前量能載入中，或無股期部位", DIM))]
    n_surge = sum(1 for r in rows if r["status"] == "爆量")
    stag = f"LIVE · {n_surge} 爆量" if n_surge else "LIVE"
    lines = [_modhead("MOD.01", "量能監控", stag, SURGE if n_surge else ACCENT)]
    lines.append(_trow(
        _col("代碼", 7, "left", DIM), _col("名稱", 10, "left", DIM),
        _col("現價", 10, "right", DIM), _col("漲跌幅", 10, "right", DIM),
        _col("5日均", 10, "right", DIM), _col("現在量", 10, "right", DIM),
        _col("預估今量", 11, "right", DIM), _col("預估比", 9, "right", DIM),
        _col("", 2), _col("狀態", 8, "left", DIM),
    ))
    for r in rows:                       # 指數：加權 / 櫃買
        if r.get("kind") == "index":
            lines.append(_vol_emit(r))
    lines.append(_line(_sp("  " + "─" * 60, FAINT)))   # 區分 指數 / 股期標的
    for r in stocks:
        lines.append(_vol_emit(r))
    return lines


# ── 模組二：市場廣度 ──────────────────────────────────────
def render_breadth_lines():
    b = calc_market_breadth()
    if not b:
        return [_modhead("MOD.02", "成值前200超額報酬", "SCAN…", AMBER)]
    up, dn = b["top200_up"], b["top200_down"]
    tot = max(up + dn, 1)
    bw = 24
    up_n = int(round(up / tot * bw))
    ex_v, ex_c = _chg(b["excess_vs_taiex"])
    wavg_v, wavg_c = _chg(b["top200_wavg_chg_pct"])
    avg_v, avg_c = _chg(b["top200_avg_chg_pct"])
    tx_v, tx_c = _chg(b["taiex_chg_pct"])
    otc_v, otc_c = _chg(b["otc_chg_pct"])
    return [
        _modhead("MOD.02", "成值前200超額報酬", "LIVE", ACCENT),
        _line(_sp("  超額報酬 ", DIM), _sp(ex_v, ex_c, bold=True),
              _sp("   = 前200加權 ", DIM), _sp(wavg_v, wavg_c),
              _sp(" − 加權指數 ", DIM), _sp(tx_v, tx_c)),
        _line(_sp("  前200均幅 ", DIM), _sp(avg_v, avg_c, bold=True),
              _sp("    加權 ", DIM), _sp(tx_v, tx_c, bold=True),
              _sp("    櫃買 ", DIM), _sp(otc_v, otc_c, bold=True)),
        _line(_sp("  前200 ", DIM), _sp("▲", UP), _sp(f"{up:>3} ", TEXT, bold=True),
              _sp("[", DIM), _sp("█" * up_n, UP), _sp("█" * (bw - up_n), DOWN),
              _sp("] ", DIM), _sp(f"{dn:<3}", TEXT, bold=True), _sp("▼", DOWN)),
    ]


# ── 模組四：漲跌停統計 ────────────────────────────────────
def _limit_row(label, up, dn):
    """單行漲跌停統計：CSS 固定欄寬，標籤與數字保持間距。"""
    return _trow(
        _col(label, 16, "left", DIM),
        _col("▲", 4, "right", UP), _col(f"{up}", 5, "right", TEXT, bold=True),
        _col("", 5),
        _col("▼", 4, "right", DOWN), _col(f"{dn}", 5, "right", TEXT, bold=True),
    )


_limit_tier_row = _limit_row   # 分價統計行與一般行同格式（自然對齊）


def render_limit_lines():
    s = get_limit_stats()
    thr = int(s["price_threshold"])
    return [
        _modhead("MOD.04", "漲跌停統計", "LIVE", ACCENT),
        _limit_row("全市場", s["limit_up_total"], s["limit_down_total"]),
        _limit_tier_row(f"股價小於{thr}元", s["limit_up_low"], s["limit_down_low"]),
        _limit_tier_row(f"股價大於{thr}元", s["limit_up_high"], s["limit_down_high"]),
        _limit_row("成交值前200", s["top200_limit_up"], s["top200_limit_down"]),
        _limit_row(f"高價前{HIGH_PRICE_COUNT}", s["highprice_limit_up"], s["highprice_limit_down"]),
    ]


# ── 模組六：成值前200 創一年新高 ─────────────────────────
def render_module6_lines():
    d = get_new_highs()
    if not d["ready"]:
        return [_modhead("MOD.06", "成值前200創年新高", "建立中", AMBER),
                _line(_sp("  52週高點表建立中（每日首次約 1~2 分鐘）…", DIM))]
    stag = f"{d['count']} 檔新高" if d["count"] else "0 檔"
    lines = [_modhead("MOD.06", "成值前200創年新高",
                      stag, SURGE if d["count"] else ACCENT)]
    lines.append(_line(_sp("  依成交值排序 · 創一年新高 ", DIM),
                       _sp(f"{d['count']}", SURGE if d["count"] else TEXT, bold=True),
                       _sp(f" / {d['total']} 檔", DIM)))
    if not d["items"]:
        return lines
    # 盤中「收盤」欄其實是當前價；13:30 後才是真正收盤
    now = datetime.now()
    is_closed = (now.hour, now.minute) >= (13, 30)
    price_label = "收盤" if is_closed else "現價"
    lines.append(_trow(
        _col("代碼", 7, "left", DIM), _col("名稱", 12, "left", DIM),
        _col(price_label, 11, "right", DIM), _col("前一年高", 12, "right", DIM),
        _col("幅度", 10, "right", DIM), _col("創高間隔", 10, "right", DIM),
        _col("", 2), _col("說明", 12, "left", DIM),
    ))
    for it in d["items"]:
        col = UP if it["pct"] > 0 else (DOWN if it["pct"] < 0 else DIM)
        if it["close_new_high"]:
            note = "收盤創新高" if is_closed else "現價站上"   # 站上前高
            ncol, nbold = UP, True
        else:
            note, ncol, nbold = "盤中觸高", DIM, False          # 摸高但未站上
        gap = it.get("days_gap", 0)
        gap_txt = f"{gap}日" if gap > 0 else "—"
        # 隔越久的突破越有意義：>=60 日亮色加粗
        gcol, gbold = (AMBER, True) if gap >= 60 else (TEXT, False)
        lines.append(_trow(
            _col(it["code"], 7, "left", CODE),
            _col(it["name"], 12, "left", TEXT),
            _col(f"{it['close']:,.2f}", 11, "right", TEXT),
            _col(f"{it['prior_high']:,.2f}", 12, "right", DIM),
            _col(f"{it['pct']:+.2f}%", 10, "right", col, bold=True),
            _col(gap_txt, 10, "right", gcol, bold=gbold),
            _col("", 2), _col(note, 12, "left", ncol, bold=nbold),
        ))
    return lines


# ── 模組十一：成值前200 站上月／季／半年線家數 ───────────
def render_module11_lines():
    d = get_ma_break()
    if not d["ready"]:
        return [_modhead("MOD.11", "成值前200均線上家數", "建立中", AMBER),
                _line(_sp("  日收序列建立中（隨 52 週高點表一併產生）…", DIM))]
    rows = d["rows"]
    newly_total = sum(r["newly"] for r in rows)
    stag = f"今日新站上 {newly_total}" if newly_total else "無新站上"
    lines = [_modhead("MOD.11", "成值前200均線上家數",
                      stag, UP if newly_total else ACCENT)]
    lines.append(_trow(
        _col("均線", 12, "left", DIM), _col("線上家數", 12, "right", DIM),
        _col("佔比", 9, "right", DIM),
        _col("平均乖離", 11, "right", DIM), _col("", 3),
        _col("今日站上", 10, "right", DIM),
    ))
    for r in rows:
        # 線上佔比越高＝盤面越強：過半亮紅（漲色）加粗
        bcol = UP if r["valid"] and r["above"] * 2 > r["valid"] else TEXT
        gcol = UP if r["bias"] > 0 else (DOWN if r["bias"] < 0 else DIM)
        ncol, nbold = (UP, True) if r["newly"] else (DIM, False)
        # 可點擊列：點擊開啟該均線乖離最大前 20 檔 modal
        lines.append(html.Div([
            _col(f"{r['label']}({r['n']}MA)", 12, "left", TEXT),
            _col(f"{r['above']} / {r['valid']}", 12, "right", bcol, bold=True),
            _col(f"{r['pct']:.0f}%", 9, "right", bcol),
            _col(f"{r['bias']:+.2f}%", 11, "right", gcol),
            _col("", 3),
            _col(f"{r['newly']}", 10, "right", ncol, bold=nbold),
        ], id={"type": "ma-row", "index": r["n"]}, n_clicks=0,
           className="grp-row",
           style={"whiteSpace": "nowrap", "paddingLeft": "2px"}))
    return lines


# ── 模組十一 modal：乖離最大前 20 檔 ──────────────────────
_MA_NAME = dict(MA_BREAK_PERIODS)   # {20: "月線", 60: "季線", 120: "半年線"}


def render_ma_bias_modal_body(n: int):
    items = get_ma_bias_top(n, 20)
    if not items:
        return [html.Div("資料載入中…", style={"color": DIM})]
    lines = [_trow(
        _col("代號", 7, "left", DIM), _col("股名", 13, "left", DIM),
        _col("現價", 10, "right", DIM), _col(f"{n}MA", 10, "right", DIM),
        _col("乖離", 10, "right", DIM),
    )]
    for s in items:
        gcol = UP if s["bias"] > 0 else (DOWN if s["bias"] < 0 else DIM)
        lines.append(_trow(
            _col(s["code"], 7, "left", CODE),
            _col(s["name"], 13, "left", TEXT),
            _col(f"{s['close']:,.2f}", 10, "right", TEXT),
            _col(f"{s['ma']:,.2f}", 10, "right", TEXT),
            _col(f"{s['bias']:+.2f}%", 10, "right", gcol, bold=True),
        ))
    return lines


# ── 模組三：MACD 狀態（加權 / 櫃買 + 持股）────────────────
# 柱狀體前一日狀態比較 → (顏色, 是否加粗)。增長/翻轉加粗，縮短不加粗。
_HIST_STYLE = {
    "翻紅": (UP, True),   "紅柱增長": (UP, True),   "紅柱縮短": (UP, False),
    "翻綠": (DOWN, True), "綠柱增長": (DOWN, True), "綠柱縮短": (DOWN, False),
}

# 日柱狀體狀態 → 帶盤勢註解的顯示文字（其餘沿用原字）
_D_HIST_LABEL = {
    "紅柱縮短": "紅柱縮短(注意高檔反轉)",
    "綠柱縮短": "綠柱縮短(注意低檔反彈)",
}

# 週柱狀體狀態 → 帶盤勢註解的顯示文字（翻紅/翻綠沿用原字）
_W_HIST_LABEL = {
    "紅柱增長": "紅柱增長(多頭延續)",
    "紅柱縮短": "！紅柱縮短(多頭震盪或回檔)",
    "綠柱增長": "！綠柱增長(大回檔或空頭)",
    "綠柱縮短": "綠柱縮短(反彈或新的一波)",
}


def _macd_emit(r):
    label = r["name"] if r["kind"] == "index" else f"{r['symbol']} {r['name']}"
    if not r["ok"]:
        return _trow(_col(label, 15, "left", TEXT), _col("資料不足", 20, "left", FAINT))
    scol, sbold = _HIST_STYLE.get(r["hist_status"], (DIM, False))
    if r["w_ok"]:   # 週線柱狀體狀態（帶盤勢註解，不顯示數值）
        wscol, wsbold = _HIST_STYLE.get(r["w_hist_status"], (DIM, False))
        w_label = _W_HIST_LABEL.get(r["w_hist_status"], r["w_hist_status"])
        w_cells = (
            _col("", 2), _col(w_label, 26, "left", wscol, bold=wsbold),
        )
    else:           # 週 K 根數不足
        w_cells = (
            _col("", 2), _col("—", 26, "left", FAINT),
        )
    return _trow(
        _col(label, 15, "left", CODE if r["kind"] == "index" else TEXT),
        _col(f"{r['dif']:+.2f}", 10, "right", DOWN if r["dif"] < 0 else TEXT),    # DIF（負值綠字）
        _col(f"{r['macd']:+.2f}", 10, "right", DOWN if r["macd"] < 0 else TEXT),  # MACD（負值綠字）
        _col("", 2), _col(_D_HIST_LABEL.get(r["hist_status"], r["hist_status"]), 24, "left", scol, bold=sbold),  # 日柱狀體狀態（帶盤勢註解）
        *w_cells,
    )


def render_module3_lines():
    rows = get_macd_display()
    n_ok = sum(1 for r in rows if r["ok"])
    stag = "LIVE" if n_ok else "WAIT"
    lines = [_modhead("MOD.03", "MACD 狀態", stag, ACCENT if n_ok else AMBER)]
    lines.append(_trow(
        _col("標的", 15, "left", DIM), _col("DIF", 10, "right", DIM),
        _col("MACD", 10, "right", DIM),
        _col("", 2), _col("日柱狀體狀態", 24, "left", DIM),
        _col("", 2), _col("週柱狀體狀態", 26, "left", DIM),
    ))
    for r in rows:                       # 指數：加權 / 櫃買
        if r["kind"] == "index":
            lines.append(_macd_emit(r))
    lines.append(_line(_sp("  " + "─" * 56, FAINT)))   # 區分 指數 / 持股
    for r in rows:                       # 持股
        if r["kind"] == "stock":
            lines.append(_macd_emit(r))
    return lines


# ── 模組五：乖離率（加權/櫃買指數 + 持股，對 5/10/20/60 日均線）──
def _bias_col(v, ch=11):
    if v is None:
        return _col("—", ch, "right", FAINT)
    col = UP if v > 0 else (DOWN if v < 0 else DIM)   # 正乖離紅、負乖離綠
    return _col(f"{v:+.2f}%", ch, "right", col, bold=True)


def _ma_bias_note(v):
    """月季乖離分級註解：>30 乖離過大、20~30 高檔、10~20 初升段、<=10 低檔。"""
    if v is None:
        return "—", FAINT, False
    if v > 30:
        return "乖離過大", UP, True
    if v > 20:
        return "高檔", AMBER, True
    if v > 10:
        return "初升段", TEXT, False
    return "低檔", DIM, False


def _bias_emit(r):
    label = r["name"] if r["kind"] == "index" else f"{r['symbol']} {r['name']}"
    price_txt = f"{r['price']:,.2f}" if r["price"] else "—"
    cells = [_col(label, 15, "left", CODE if r["kind"] == "index" else TEXT),
             _col(price_txt, 11, "right", TEXT)]
    for n in BIAS_PERIODS:
        cells.append(_bias_col(r["bias"].get(n)))      # 股價對 N 日均乖離
    cells.append(_col("", 2))
    cells.append(_bias_col(r["ma_bias"]))              # 月季線乖離 (20MA/60MA)
    note, ncol, nbold = _ma_bias_note(r["ma_bias"])
    cells.append(_col("", 2))
    cells.append(_col(note, 10, "left", ncol, bold=nbold))
    return _trow(*cells)


def render_module5_lines():
    rows = get_bias_display()
    n_ok = sum(1 for r in rows if r["ok"])
    lines = [_modhead("MOD.05", "乖離率", "LIVE" if n_ok else "WAIT",
                      ACCENT if n_ok else AMBER)]
    hdr = [_col("標的", 15, "left", DIM), _col("現價", 11, "right", DIM)]
    for n in BIAS_PERIODS:
        hdr.append(_col(f"{n}日乖離", 11, "right", DIM))
    hdr.append(_col("", 2))
    hdr.append(_col("月季乖離", 11, "right", DIM))
    hdr.append(_col("", 2))
    hdr.append(_col("註解", 10, "left", DIM))
    lines.append(_trow(*hdr))

    for r in rows:                       # 指數：加權 / 櫃買
        if r["kind"] == "index":
            lines.append(_bias_emit(r))
    lines.append(_line(_sp("  " + "─" * 60, FAINT)))   # 區分 指數 / 個股
    for r in rows:                       # 持股
        if r["kind"] == "stock":
            lines.append(_bias_emit(r))
    return lines


# ── 模組七：庫存帳務（期貨部位明細，讀部位時更新）──────────
def render_module7_lines():
    rows = MARKET_STATE.fut_positions
    if not rows:
        return [_modhead("MOD.07", "庫存帳務", "WAIT", AMBER),
                _line(_sp("  部位讀取中，或無期貨庫存", DIM))]
    total_expo = sum(r["exposure"] for r in rows)
    total_abs_expo = sum(abs(r["exposure"]) for r in rows)
    total_cost = sum(r.get("cost", 0) for r in rows)
    total_pnl = sum(r["pnl"] for r in rows)
    total_rate = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0
    pcol = UP if total_pnl > 0 else (DOWN if total_pnl < 0 else DIM)
    lines = [_modhead("MOD.07", "庫存帳務",
                      f"損益 {total_pnl:+,.0f}", pcol)]
    lines.append(_trow(
        _col("股票期貨名稱", 16, "left", DIM), _col("口數", 6, "right", DIM),
        _col("庫存均價", 11, "right", DIM), _col("現價", 10, "right", DIM),
        _col("曝險金額", 15, "right", DIM), _col("損益", 12, "right", DIM),
        _col("獲利率", 9, "right", DIM), _col("占比", 8, "right", DIM),
    ))
    for r in rows:
        qcol = UP if r["qty"] > 0 else DOWN            # 買方紅 / 賣方綠
        rp = UP if r["pnl"] > 0 else (DOWN if r["pnl"] < 0 else DIM)
        cost = r.get("cost", 0)
        rate_txt = f"{r['pnl'] / cost * 100:+.2f}%" if cost > 0 else "—"
        w_txt = f"{abs(r['exposure']) / total_abs_expo * 100:.1f}%" if total_abs_expo > 0 else "—"
        lines.append(_trow(
            _col(r["name"], 16, "left", TEXT),
            _col(f"{r['qty']:+d}", 6, "right", qcol, bold=True),
            _col(f"{r['avg']:,.2f}", 11, "right", TEXT),
            _col(f"{r['last']:,.2f}", 10, "right", TEXT),
            _col(f"{r['exposure']:,.0f}", 15, "right", TEXT),
            _col(f"{r['pnl']:+,.0f}", 12, "right", rp, bold=True),
            _col(rate_txt, 9, "right", rp, bold=True),          # 獲利率
            _col(w_txt, 8, "right", TEXT),                      # 占比
        ))
    lines.append(_trow(
        _col("合計", 16, "left", DIM), _col("", 6),
        _col("", 11), _col("", 10),
        _col(f"{total_expo:,.0f}", 15, "right", TEXT, bold=True),
        _col(f"{total_pnl:+,.0f}", 12, "right", pcol, bold=True),
        _col(f"{total_rate:+.2f}%", 9, "right", pcol, bold=True),
        _col("100%", 8, "right", DIM),
    ))
    return lines


# ── 模組九：庫存處置清單（持股標的 vs 處置名單）───────────
def render_module9_lines():
    from store.memory_store import STOCK_STORE
    punish_by_code = {p["code"]: p for p in MARKET_STATE.punish_list}
    hits = [(code, st.name, punish_by_code[code])
            for code, st in list(STOCK_STORE.items()) if code in punish_by_code]
    if not hits:
        return [_modhead("MOD.09", "庫存處置清單", "安全", ACCENT),
                _line(_sp("  持股標的均不在處置名單 ✓", DIM))]
    lines = [_modhead("MOD.09", "庫存處置清單", f"{len(hits)} 檔處置中", UP)]
    lines.append(_trow(
        _col("代號", 7, "left", DIM), _col("股名", 10, "left", DIM),
        _col("處置起迄日", 26, "left", DIM), _col("撮合時間", 10, "left", DIM),
    ))
    for code, name, p in hits:
        lines.append(_trow(
            _col(code, 7, "left", CODE),
            _col(name, 10, "left", TEXT),
            _col(f"{p['start']} ~ {p['end']}", 26, "left", TEXT),
            _col(p["interval"], 10, "left", AMBER, bold=True),
        ))
    return lines


# ── 模組十二：淨值績效曲線（2026 起每日，vs 加權/櫃買）────
# 三色經 dataviz 驗證（深色底 CVD ΔE 100、對比 ≥3:1）：
# 我的績效=藍、加權=琥珀、櫃買=紫；不用紅綠，避免與漲跌語意衝突。
_PERF_MINE  = "#3d8fd6"
_PERF_TAIEX = "#b3841f"
_PERF_OTC   = "#9a63e8"


def render_module12_lines():
    p = MARKET_STATE.perf
    if not p.get("ready"):
        return [_modhead("MOD.12", "淨值績效曲線", "建立中", AMBER),
                _line(_sp("  帳務歷史重建中（首次約 1~2 分鐘）…", DIM))]
    ytd = p["mine"][-1]
    ycol = UP if ytd > 0 else (DOWN if ytd < 0 else DIM)
    # 期初淨值非正 = 期間外部出入金未登錄，報酬率會失真 → 顯眼警示
    flows_missing = p["base_nv"] <= 0
    if flows_missing:
        lines = [_modhead("MOD.12", "淨值績效曲線", "待補出入金", AMBER),
                 _line(_sp("  ⚠ 回推期初淨值為負：期間有外部出入金未登錄，"
                           "請填 cache/capital_flows.json（入金為正）", AMBER))]
    else:
        lines = [_modhead("MOD.12", "淨值績效曲線", f"今年 {ytd:+.2f}%", ycol)]

    x = p["dates"]
    fig = go.Figure()
    for name, ys, color, width in [
        ("我的績效", p["mine"],  _PERF_MINE,  2.6),
        ("加權指數", p["taiex"], _PERF_TAIEX, 1.6),
        ("櫃買指數", p["otc"],   _PERF_OTC,   1.6),
    ]:
        fig.add_trace(go.Scatter(
            x=x, y=[round(v, 2) for v in ys], mode="lines", name=name,
            line=dict(color=color, width=width),
            hovertemplate="%{y:+.2f}%<extra>" + name + "</extra>",
        ))
    # 月初刻度（category 軸自動跳過非交易日，曲線才連貫）
    ticks = []
    seen = set()
    for d in x:
        if d[:7] not in seen:
            seen.add(d[:7])
            ticks.append(d)
    # 基準月只有年底一兩個點，刻度會跟下一個月重疊 → 捨去
    if len(ticks) >= 2 and x.index(ticks[1]) - x.index(ticks[0]) < 5:
        ticks = ticks[1:]
    fig.update_layout(
        height=380, margin=dict(l=10, r=16, t=8, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=TEXT, family=FONT, size=11),
        hovermode="x unified",
        hoverlabel=dict(bgcolor="#161b22", bordercolor="#2b3a55",
                        font=dict(color=TEXT, family=FONT, size=12)),
        legend=dict(orientation="h", x=0, y=1.08, bgcolor="rgba(0,0,0,0)",
                    font=dict(color=TEXT)),
        xaxis=dict(type="category", tickvals=ticks,
                   ticktext=[t[:7] for t in ticks],
                   showgrid=False, tickfont=dict(color=DIM),
                   linecolor=FAINT),
        yaxis=dict(ticksuffix="%", tickfont=dict(color=DIM),
                   gridcolor="#21262d", zeroline=True,
                   zerolinecolor=FAINT, zerolinewidth=1),
    )
    lines.append(dcc.Graph(figure=fig, config={"displayModeBar": False}))
    # 最新讀值（一行一項；指數行附年初首個交易日收盤與目前盤中價）
    def _idx_summary(dot, name, pct, y0_date, y0, live):
        cells = [_sp("  ● ", dot), _sp(f"{name} ", DIM),
                 _sp(f"{pct:+.2f}%", TEXT)]
        if y0 > 0:
            md = f"{int(y0_date[5:7])}/{int(y0_date[8:10])}"   # 例：1/2
            cells += [
                _sp(f"   年初{md} ", DIM), _sp(f"{y0:,.2f}", TEXT),
                _sp("  →  現在 ", DIM),
                _sp(f"{live:,.2f}" if live > 0 else "—",
                    TEXT if live > 0 else FAINT, bold=live > 0),
            ]
        return _line(*cells)

    m, t, o = p["mine"][-1], p["taiex"][-1], p["otc"][-1]
    # 最大回檔：淨值財富指數相對歷史高點的最大回落（含今日盤中值），
    # 並記下該段的高點日～谷底日
    peak, peak_i, mdd, mdd_span = 1.0, 0, 0.0, None
    for i, v in enumerate(p["mine"]):
        w = 1 + v / 100
        if w > peak:
            peak, peak_i = w, i
        dd = (w / peak - 1) * 100
        if dd < mdd:
            mdd, mdd_span = dd, (p["dates"][peak_i], p["dates"][i])

    def _md(iso):   # "2026-05-19" → "5/19"
        return f"{int(iso[5:7])}/{int(iso[8:10])}"

    mdd_cells = [_sp("   MDD ", DIM), _sp(f"{mdd:.2f}%", DOWN if mdd < 0 else TEXT)]
    if mdd_span:
        mdd_cells.append(_sp(f" ({_md(mdd_span[0])}~{_md(mdd_span[1])})", FAINT))
    lines.append(_line(
        _sp("  ● ", _PERF_MINE), _sp("我的績效 ", DIM),
        _sp(f"{m:+.2f}%", TEXT, bold=True),
        *mdd_cells,
    ))
    lines.append(_idx_summary(_PERF_TAIEX, "加權", t,
                              p.get("taiex_y0_date", ""), p.get("taiex_y0", 0.0),
                              MARKET_STATE.taiex_close))
    lines.append(_idx_summary(_PERF_OTC, "櫃買", o,
                              p.get("otc_y0_date", ""), p.get("otc_y0", 0.0),
                              MARKET_STATE.otc_close))
    lines.append(_line(
        _sp("  淨值 ", DIM), _sp(f"{p['nv_today']:,.0f}", TEXT, bold=True),
        _sp("   期初 ", DIM), _sp(f"{p['base_nv']:,.0f}", TEXT),
        _sp("   今年損益 ", DIM),
        _sp(f"{p['nv_today'] - p['base_nv']:+,.0f}", ycol, bold=True),
        _sp(f"   更新 {p['asof']:%H:%M}", FAINT),
    ))
    return lines


# ── 模組八：處置/注意股數量 ───────────────────────────────
def _reg_val(v, ch=9):
    return _col("—" if v is None else f"{v:g}", ch, "right",
                FAINT if v is None else TEXT, bold=v is not None)


def render_module8_lines():
    st = MARKET_STATE.reg_stats
    if not st:
        return [_modhead("MOD.08", "處置/注意股數量", "WAIT", AMBER),
                _line(_sp("  公告資料載入中…", DIM))]
    p, n = st["punish"], st["notice"]
    lines = [_modhead("MOD.08", "處置/注意股數量", "LIVE", ACCENT)]
    lines.append(_trow(
        _col("", 10, "left", DIM), _col("今日", 9, "right", DIM),
        _col("昨日", 9, "right", DIM), _col("近五日均", 10, "right", DIM),
    ))
    lines.append(_trow(
        _col("處置股", 10, "left", TEXT),
        _reg_val(p["today"]), _reg_val(p["yesterday"]),
        _reg_val(p["avg5"], 10),
    ))
    lines.append(_trow(
        _col("注意股", 10, "left", TEXT),
        _reg_val(n["today"]), _reg_val(n["yesterday"]),
        _reg_val(n["avg5"], 10),
    ))
    return lines


# ── 模組十：族群金流與漲跌幅監控 ──────────────────────────
_PERIOD_LABEL = {"day": "當日", "week": "本週", "lastweek": "上週"}


def _chg_shade(chg: float) -> str:
    """
    固定級距上色（純 RGB，不用透明度——黑底上透明度只會變濁看不出深淺）：
    |平均漲跌幅| 0~2% / 2~4% / 4~6% / 6%+ 四級。
    紅（漲）只調 R 通道 110→160→210→255；綠（跌）只調 G 通道，越漲/跌越鮮明。
    """
    m = abs(chg)
    level = 0 if m < 2 else (1 if m < 4 else (2 if m < 6 else 3))
    v = (110, 160, 210, 255)[level]
    if chg >= 0:
        return f"rgb({v},55,50)"     # 紅（漲）：調 R
    return f"rgb(35,{v},75)"         # 綠（跌）：調 G


def render_group_pie(rows, period="day"):
    """
    甜甜圈圖（仿 圓餅圖.png）：切片=族群成交金額、顏色=平均漲跌幅紅綠深淺、
    灰色「其他」補到全市場 100%、外部拉線標籤、中心顯示追蹤合計與佔大盤比。
    點切片開成分股 modal。period="week" 時全部改本週累計口徑。
    """
    market_total = get_market_total(period) / 1e8
    data = sorted(rows, key=lambda r: r["amount"], reverse=True)
    labels = [r["group"] for r in data]
    values = [r["amount"] / 1e8 for r in data]
    colors = [_chg_shade(r["avg_chg"]) for r in data]
    covered = sum(values)
    other = max(market_total - covered, 0)   # 未分類（負值防呆歸零）
    labels.append("其他(未分類)")
    values.append(other)
    colors.append("#252b36")
    # 標籤顯示「族群 平均漲跌幅」；金流佔比移到 hover
    texts = [f"{r['group']} {r['avg_chg']:+.2f}%" for r in data] + [""]   # 其他不標
    chg_hover = [f"{r['avg_chg']:+.2f}%" for r in data] + ["—"]

    fig = go.Figure(go.Pie(
        labels=labels, values=values, sort=False, hole=0.62,
        marker=dict(colors=colors, line=dict(color="#161b22", width=1.5)),
        text=texts, textinfo="text", textposition="outside",
        customdata=chg_hover,
        hovertemplate="%{label}<br>漲跌 %{customdata} · %{value:,.0f}億 · 佔 %{percent}"
                      "<extra></extra>",
    ))
    center_pct = f"{covered / market_total * 100:.1f}%" if market_total > 0 else "—"
    plabel = _PERIOD_LABEL.get(period, "當日")
    fig.update_layout(
        showlegend=False, height=500,
        margin=dict(l=85, r=85, t=30, b=30),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=TEXT, family=FONT, size=11),
        annotations=[
            dict(text=f"<b>{covered:,.0f}億</b>", x=0.5, y=0.54, showarrow=False,
                 font=dict(size=28, color=TEXT)),
            dict(text=f"{plabel}追蹤族群成交 · 佔大盤 {center_pct}", x=0.5, y=0.43,
                 showarrow=False, font=dict(size=11, color=DIM)),
        ],
    )
    return dcc.Graph(id="grp-pie", figure=fig, config={"displayModeBar": False})


def render_module10_lines(period="day"):
    rows = get_industry_display(period)
    toggle = html.Div(dcc.RadioItems(
        id="flow-radio",
        options=[{"label": " 當日", "value": "day"},
                 {"label": " 本週", "value": "week"},
                 {"label": " 上週", "value": "lastweek"}],
        value=period, inline=True, className="flow-toggle",
        labelStyle={"marginRight": "16px", "cursor": "pointer",
                    "color": "#e6edf3"},   # 選項文字白色（inline 強制，不靠繼承）
    ), style={"margin": "4px 0 2px"})
    if not rows:
        return [_modhead("MOD.10", "族群金流監控", "WAIT", AMBER), toggle,
                _line(_sp("  族群行情載入中…", DIM))]
    plabel = _PERIOD_LABEL.get(period, "當日")
    lines = [_modhead("MOD.10", "族群金流監控", f"LIVE · {plabel}", ACCENT)]
    lines.append(toggle)
    lines.append(render_group_pie(rows, period))   # 圓餅：金額占全市場（含灰色其他）
    lines.append(_trow(
        _col("族群", 32, "left", DIM), _col("平均漲跌", 9, "right", DIM),
        _col("成交金額", 10, "right", DIM), _col("佔比", 8, "right", DIM),
    ))
    for r in rows:
        chg = r["avg_chg"]
        ccol = UP if chg > 0 else (DOWN if chg < 0 else DIM)
        ratio_txt = f"{r['ratio']:.1f}%" if r["ratio"] is not None else "—"
        # 可點擊列：點擊開啟該族群成分股 modal
        lines.append(html.Div([
            _col(r["group"], 32, "left", TEXT),
            _col(f"{chg:+.2f}%", 9, "right", ccol, bold=True),
            _col(f"{r['amount'] / 1e8:,.0f}億", 10, "right", TEXT),
            _col(ratio_txt, 8, "right", TEXT),
        ], id={"type": "grp-row", "index": r["group"]}, n_clicks=0,
           className="grp-row",
           style={"whiteSpace": "nowrap", "paddingLeft": "2px"}))
    return lines


# ── 模組十 modal：族群成分股明細 ──────────────────────────
def render_group_modal_body(group: str, period: str = "day"):
    detail = {
        "day":      MARKET_STATE.industry_detail,
        "week":     MARKET_STATE.industry_detail_week,
        "lastweek": MARKET_STATE.industry_detail_lastweek,
    }.get(period, MARKET_STATE.industry_detail)
    stocks = detail.get(group, [])
    if not stocks:
        return [html.Div("資料載入中…", style={"color": DIM})]
    price_label = "上週收盤" if period == "lastweek" else "現價"
    chg_label = "漲跌幅" if period == "day" else "週漲跌幅"
    amt_label = "成交金額" if period == "day" else "週成交金額"
    lines = [_trow(
        _col("代號", 7, "left", DIM), _col("股名", 13, "left", DIM),
        _col(price_label, 10, "right", DIM), _col(chg_label, 10, "right", DIM),
        _col(amt_label, 12, "right", DIM),
    )]
    for s in stocks:
        ccol = UP if s["chg"] > 0 else (DOWN if s["chg"] < 0 else DIM)
        lines.append(_trow(
            _col(s["code"], 7, "left", CODE),
            _col(s["name"], 13, "left", TEXT),
            _col(f"{s['close']:,.2f}", 10, "right", TEXT),
            _col(f"{s['chg']:+.2f}%", 10, "right", ccol, bold=True),
            _col(f"{s['amount'] / 1e8:,.1f}億", 12, "right", TEXT),
        ))
    return lines


# ── OFFLINE 占位（備用）──────────────────────────────────
def render_offline_lines(tag, title, hint):
    return [
        _modhead(tag, title, "OFFLINE", DIM),
        _line(_sp("  " + hint, FAINT)),
    ]


def _cell(lines):
    return html.Div(lines, className="cell")


def _col_cap(text):
    return html.Div(text, className="col-cap")


def render_screen_body(period="day"):
    return [
        # 警示：任一資料來源抓取失敗時置頂顯示
        *render_alert_banner(),
        # 流量：整列
        html.Div(render_usage_lines(), className="traffic"),
        # 左：持股狀態（MOD.01/03/05）｜ 右：大盤盤況（MOD.02/04）
        html.Div(className="cols", children=[
            html.Div(className="col-left", children=[
                _col_cap("持股狀態 · MY HOLDINGS"),
                _cell(render_module1_lines()),
                _cell(render_module3_lines()),
                _cell(render_module5_lines()),
                _cell(render_module7_lines()),
                _cell(render_module9_lines()),
                _cell(render_module12_lines()),
            ]),
            html.Div(className="col-right", children=[
                _col_cap("大盤盤況 · MARKET"),
                _cell(render_breadth_lines()),
                _cell(render_limit_lines()),
                _cell(render_module8_lines()),
                _cell(render_module6_lines()),
                _cell(render_module11_lines()),
                _cell(render_module10_lines(period)),
            ]),
        ]),
    ]


# ── 背景動畫：流動波浪 + 跳動 K 棒（canvas，見 _INDEX 的 <script>）──


# ── 版面 ──────────────────────────────────────────────────
def serve_layout():
    return html.Div(className="page", children=[
        dcc.Interval(id="tick", interval=60_000, n_intervals=0),   # 畫面每分鐘重繪
        dcc.Location(id="reloader", refresh=True),                 # 舊分頁自動重載用
        dcc.Store(id="boot-id", data=_BOOT_ID),                    # 載入當下的伺服器識別碼
        dcc.Store(id="grp-open", data=None),
        dcc.Store(id="flow-period", data="day"),   # 模組十：當日/本週切換
        # 族群成分股 modal（點族群列開啟）
        html.Div(id="grp-modal", className="modal-bg", n_clicks=0,
                 style={"display": "none"}, children=[
            html.Div(className="modal-panel", children=[
                html.Div(className="modal-head", children=[
                    html.Span(id="grp-modal-title", className="mh-title"),
                    html.Span("✕", id="grp-modal-close", n_clicks=0,
                              className="modal-close"),
                ]),
                html.Div(id="grp-modal-body"),
            ]),
        ]),
        html.Div(className="card", children=[
            html.Div(className="header", children=[
                html.Span("●", className="live-dot"),
                html.Span("盤中量化監看", className="h-title"),
                html.Span(id="clock", className="h-clock"),
            ]),
            html.Div(id="screen-body", className="screen-body"),
        ]),
    ])


_INDEX = """<!DOCTYPE html>
<html>
<head>
{%metas%}<title>{%title%}</title>{%favicon%}{%css%}
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&display=swap');
html,body{margin:0;padding:0;background:#0a0e14;}
*{box-sizing:border-box;}
#bgcv{position:fixed;top:0;left:0;width:100vw;height:100vh;z-index:0;pointer-events:none;}
.page{position:relative;z-index:1;min-height:100vh;background:transparent;padding:22px 10px;
  font-family:'JetBrains Mono','Fira Code',Menlo,Consolas,monospace;}
.card{position:relative;z-index:1;max-width:1440px;margin:0 auto;
  background:rgba(18,23,30,0.78);border:1px solid #24304a;backdrop-filter:blur(3px);
  -webkit-backdrop-filter:blur(3px);
  border-radius:12px;padding:20px 26px 24px;box-shadow:0 10px 40px rgba(0,0,0,0.55);
  color:#e6edf3;font-size:13.5px;}
.header{display:flex;align-items:center;gap:10px;padding-bottom:14px;
  border-bottom:1px solid #21262d;margin-bottom:6px;}
.live-dot{color:#5fb37a;font-size:11px;animation:pulse 2.4s ease-in-out infinite;}
@keyframes pulse{0%,100%{opacity:1;}50%{opacity:.35;}}
.h-title{color:#e6edf3;font-weight:700;font-size:16px;letter-spacing:0.03em;}
.h-clock{margin-left:auto;color:#8b949e;font-size:13px;}
.modhead{display:flex;align-items:baseline;margin:18px 0 9px;padding-bottom:7px;
  border-bottom:1px solid #21262d;}
.mh-tag{color:#7aa2c4;font-size:12px;font-weight:700;letter-spacing:0.06em;}
.mh-title{font-size:17px;font-weight:800;letter-spacing:0.05em;
  background:linear-gradient(90deg,#5ff0d0,#4db5ff 55%,#b07bff);
  -webkit-background-clip:text;background-clip:text;
  color:transparent;-webkit-text-fill-color:transparent;
  filter:drop-shadow(0 0 6px rgba(77,181,255,0.35));}
.mh-badge{margin-left:auto;font-size:10px;padding:2px 9px;border-radius:11px;
  border:1px solid;letter-spacing:0.05em;align-self:center;}
/* 背景動畫實作於 canvas（#bgcv）+ 模板底部的 <script> */
.screen-body div{line-height:1.75;}
.traffic{margin-bottom:10px;}
.alert-banner{background:rgba(214,74,64,0.14);border:1px solid #b8433b;border-radius:8px;
  padding:8px 14px;margin:10px 0 4px;color:#f0716b;font-size:13px;line-height:1.7;
  animation:alertblink 2.2s ease-in-out infinite;}
.alert-banner .alert-title{font-weight:700;letter-spacing:0.05em;}
@keyframes alertblink{0%,100%{border-color:#b8433b;}50%{border-color:#f0716b;}}
.cols{display:flex;gap:40px;}
.col-left{flex:1.1;min-width:0;}
.col-right{flex:1;min-width:0;}
.col-cap{color:#8b949e;font-size:11px;letter-spacing:0.16em;
  border-bottom:1px solid #21262d;padding-bottom:7px;margin:2px 0 4px;}
.cell{min-width:0;overflow-x:auto;}
.cell .modhead{margin-top:16px;}
.grp-row{cursor:pointer;border-radius:4px;}
.grp-row:hover{background:rgba(121,192,255,0.10);}
.flow-toggle{color:#e6edf3;font-size:12.5px;}
.flow-toggle input{accent-color:#5fb37a;cursor:pointer;}
/* 族群成分股 modal */
.modal-bg{position:fixed;inset:0;background:rgba(0,0,0,0.62);z-index:50;
  display:flex;align-items:center;justify-content:center;}
.modal-panel{background:#161b22;border:1px solid #2b3a55;border-radius:12px;
  padding:18px 22px;max-height:78vh;overflow-y:auto;min-width:560px;
  box-shadow:0 18px 60px rgba(0,0,0,0.7);font-size:13.5px;color:#e6edf3;
  font-family:'JetBrains Mono','Fira Code',Menlo,Consolas,monospace;line-height:1.8;}
.modal-head{display:flex;align-items:center;margin-bottom:10px;
  border-bottom:1px solid #21262d;padding-bottom:8px;}
.modal-close{margin-left:auto;cursor:pointer;color:#8b949e;font-size:16px;
  padding:2px 8px;border-radius:6px;}
.modal-close:hover{color:#e6edf3;background:#21262d;}
@media(max-width:900px){.cols{flex-direction:column;gap:0;}}
::selection{background:#2f4a38;color:#fff;}
::-webkit-scrollbar{width:9px;}::-webkit-scrollbar-thumb{background:#21262d;border-radius:5px;}
</style>
</head>
<body>
<canvas id="bgcv"></canvas>
{%app_entry%}<footer>{%config%}{%scripts%}{%renderer%}</footer>
<script>
(function(){
  var cv=document.getElementById('bgcv'); if(!cv) return;
  var ctx=cv.getContext('2d'), t=0, dpr=Math.min(window.devicePixelRatio||1,2);
  function rs(){cv.width=innerWidth*dpr; cv.height=innerHeight*dpr; ctx.setTransform(dpr,0,0,dpr,0,0);}
  rs(); window.addEventListener('resize', rs);
  var waves=[
    {a:44,l:0.0055,s:0.30,y:0.50,c:'rgba(240,113,107,0.09)'},
    {a:58,l:0.0038,s:0.22,y:0.60,c:'rgba(86,184,119,0.09)'},
    {a:34,l:0.0082,s:0.42,y:0.46,c:'rgba(121,192,255,0.06)'},
    {a:74,l:0.0026,s:0.16,y:0.70,c:'rgba(255,215,106,0.05)'}
  ];
  var N=70, cd=[], pv=0;
  function newp(prev){ return prev + (Math.random()*1.05 - 0.32); }   /* 淨上升趨勢 */
  for(var i=0;i<N+3;i++){ pv=newp(pv); cd.push({p:pv, up:Math.random()>0.42, wl:4+Math.random()*12}); }
  var off=0, speed=0.14, lo=0, hi=1;
  function draw(){
    var W=innerWidth, H=innerHeight; ctx.clearRect(0,0,W,H);
    for(var w=0;w<waves.length;w++){
      var o=waves[w]; ctx.beginPath();
      for(var x=0;x<=W;x+=6){
        var y=H*o.y + Math.sin(x*o.l + t*o.s*0.03)*o.a + Math.sin(x*o.l*0.5 - t*o.s*0.02)*o.a*0.5;
        if(x===0){ctx.moveTo(x,y);} else {ctx.lineTo(x,y);}
      }
      ctx.lineTo(W,H); ctx.lineTo(0,H); ctx.closePath(); ctx.fillStyle=o.c; ctx.fill();
    }
    var cw=W/N;
    off+=speed;
    if(off>=cw){ off-=cw; cd.shift(); cd.push({p:newp(cd[cd.length-1].p), up:Math.random()>0.42, wl:4+Math.random()*12}); }
    cd[cd.length-1].p += (Math.random()-0.5)*0.25;      /* 最後一根即時小跳動 */
    var mn=1e9, mx=-1e9;
    for(var i=0;i<cd.length;i++){ var p=cd[i].p; if(p<mn){mn=p;} if(p>mx){mx=p;} }
    lo+=(mn-lo)*0.04; hi+=(mx-hi)*0.04; var span=(hi-lo)||1;
    var topY=H*0.30, botY=H*0.82;
    function ym(v){ return botY-(v-lo)/span*(botY-topY); }
    for(var i=0;i<cd.length;i++){
      var cx=i*cw-off+cw*0.5; if(cx<-cw||cx>W+cw){ continue; }
      var c=cd[i], yc=ym(c.p), yo=ym(i>0?cd[i-1].p:c.p);
      var col=c.up?'rgba(240,113,107,0.42)':'rgba(86,184,119,0.42)';
      var bt=Math.min(yo,yc), bh=Math.max(Math.abs(yo-yc),2.5);
      ctx.strokeStyle=col; ctx.lineWidth=1.2;
      ctx.beginPath(); ctx.moveTo(cx, bt-c.wl); ctx.lineTo(cx, bt+bh+c.wl); ctx.stroke();
      ctx.fillStyle=col; ctx.fillRect(cx-cw*0.28, bt, cw*0.56, bh);
    }
    t++; requestAnimationFrame(draw);
  }
  draw();
})();
</script>
</body>
</html>"""


def create_app() -> Dash:
    # grp-pie 為動態產生元件（首次重繪才出現），需關閉 callback 驗證
    app = Dash(__name__, title="盤中量化監看", suppress_callback_exceptions=True)

    # 防護：重啟伺服器後，未關閉的「舊瀏覽器分頁」會用舊 callback 規格輪詢，
    # 參數數量對不上 → dash._prepare_grouping IndexError。靜默回 204（無更新），
    # 避免 500 traceback 刷屏；舊分頁重新整理後即恢復正常。
    @app.server.errorhandler(IndexError)
    def _stale_tab_request(e):
        from flask import request
        if request.path == "/_dash-update-component":
            print("[dash] 忽略舊分頁的過期請求（若持續出現，請重新整理舊的瀏覽器分頁）")
            return "", 204
        raise e
    app.index_string = _INDEX
    app.layout = serve_layout

    @app.callback(
        Output("clock", "children"),
        Output("screen-body", "children"),
        Input("tick", "n_intervals"),
        Input("flow-period", "data"),   # 切換當日/本週時立即重繪
    )
    def _refresh(_, period):
        s = get_limit_stats()
        clock = f"現在 {datetime.now():%H:%M:%S}　·　資料更新 {s['last_update']:%H:%M:%S}"
        return clock, render_screen_body(period or "day")

    @app.callback(
        Output("flow-period", "data"),
        Input("flow-radio", "value"),
        prevent_initial_call=True,
    )
    def _flow_toggle(value):
        return value or "day"

    @app.callback(
        Output("reloader", "href"),
        Input("tick", "n_intervals"),
        State("boot-id", "data"),
    )
    def _check_boot(_, page_boot):
        # 分頁的 boot-id 與伺服器不符 = 伺服器重啟過 → 自動重新整理
        if page_boot != _BOOT_ID:
            print("[dash] 偵測到舊分頁，自動重新整理")
            return "/"
        return no_update

    # 開/關 modal 拆成三個獨立 callback（allow_duplicate 同寫 grp-open）。
    # 不可合併：grp-row / grp-pie 是動態元件，合併時 WAIT↔LIVE 切換瞬間
    # 瀏覽器請求會缺少未掛載元件的值 → Dash `_prepare_grouping` IndexError 500。
    @app.callback(
        Output("grp-open", "data", allow_duplicate=True),
        Input({"type": "grp-row", "index": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def _grp_click_row(rows):
        trig = ctx.triggered_id
        if isinstance(trig, dict) and trig.get("type") == "grp-row":
            # 注意：pattern 輸入的 ctx.triggered[...]["value"] 恆為 None（Dash 4.x），
            # 必須從 ctx.inputs_list 對出被點那列的實際 n_clicks；
            # 表格重繪會以 n_clicks=0 觸發，只吃真點擊（>0）。
            for spec, val in zip(ctx.inputs_list[0], rows):
                if spec["id"]["index"] == trig["index"] and val:
                    return trig["index"]
        return no_update

    @app.callback(
        Output("grp-open", "data", allow_duplicate=True),
        Input({"type": "ma-row", "index": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def _ma_click_row(rows):
        # 模組十一均線列 → 開啟該均線乖離前 20 modal（grp-open 存 "ma:<n>"）
        trig = ctx.triggered_id
        if isinstance(trig, dict) and trig.get("type") == "ma-row":
            for spec, val in zip(ctx.inputs_list[0], rows):
                if spec["id"]["index"] == trig["index"] and val:
                    return f"ma:{trig['index']}"
        return no_update

    @app.callback(
        Output("grp-open", "data", allow_duplicate=True),
        Input("grp-modal-close", "n_clicks"),
        prevent_initial_call=True,
    )
    def _grp_close(n):
        return None if n else no_update

    @app.callback(
        Output("grp-open", "data", allow_duplicate=True),
        Input("grp-pie", "clickData"),
        prevent_initial_call=True,
    )
    def _grp_click_pie(pie_click):
        # 點圓餅切片開 modal（「其他」不開）
        try:
            label = pie_click["points"][0]["label"]
        except (TypeError, KeyError, IndexError):
            return no_update
        return label if label in MARKET_STATE.industry_detail else no_update

    @app.callback(
        Output("grp-modal", "style"),
        Output("grp-modal-title", "children"),
        Output("grp-modal-body", "children"),
        Input("grp-open", "data"),
        Input("tick", "n_intervals"),      # modal 開著時內容跟著重繪刷新
        Input("flow-period", "data"),      # 切換當日/本週時 modal 同步換口徑
    )
    def _grp_modal(group, _, period):
        if not group:
            return {"display": "none"}, "", []
        if isinstance(group, str) and group.startswith("ma:"):
            n = int(group[3:])
            title = f"{_MA_NAME.get(n, '')}({n}MA) 乖離最大前 20 · 成值前200"
            return {"display": "flex"}, title, render_ma_bias_modal_body(n)
        period = period or "day"
        title = group if period == "day" else f"{group} · {_PERIOD_LABEL.get(period, '')}"
        return {"display": "flex"}, title, render_group_modal_body(group, period)

    return app


# ── 啟動 ──────────────────────────────────────────────────
def _refresh_market() -> None:
    update_scanners()
    update_index_quotes()
    update_holdings_volume()   # 模組一：股期標的個股今日量
    update_industry_flow()     # 模組十：族群金流
    update_usage()


def run(port: int = 8050, open_browser: bool = True) -> None:
    watchlist = get_stock_futures_watchlist()   # 正式環境唯讀讀部位 → 對應個股
    init_api()                                  # simulation：行情 / 帳務皆真實
    prefetch_all(watchlist)                     # 盤前歷史（均量、MACD、指數昨收）
    build_high_price_universe()
    update_regulatory()                         # 處置/注意股（公告類，一日一更）
    build_week_baseline()                       # 模組十「本週」基準
    _refresh_market()

    sched = BackgroundScheduler(timezone="Asia/Taipei")
    sched.add_job(_refresh_market, "interval", seconds=SCANNER_INTERVAL_SEC,
                  id="market", max_instances=1, coalesce=True)
    sched.add_job(sync_holdings, "interval", seconds=HOLDINGS_SYNC_SEC,
                  id="holdings_sync", max_instances=1, coalesce=True)
    # 模組十二：盤中輕量更新今日點；收盤後快照真實淨值 + 重建曲線
    sched.add_job(refresh_today_point, "interval", seconds=PERF_REFRESH_SEC,
                  id="perf_today", max_instances=1, coalesce=True)
    sched.add_job(daily_perf_job, "cron",
                  hour=PERF_SNAPSHOT_TIME.hour, minute=PERF_SNAPSHOT_TIME.minute,
                  id="perf_daily", max_instances=1, coalesce=True)
    sched.start()
    print(f"[dash] 排程啟動，行情每 {SCANNER_INTERVAL_SEC}s、部位同步每 "
          f"{HOLDINGS_SYNC_SEC}s。開啟 http://127.0.0.1:{port}")

    # 模組六：背景建立全市場 52 週高點表（獨立連線，不卡啟動）
    threading.Thread(target=build_high52w, daemon=True, name="high52w").start()
    # 模組十二：背景重建淨值績效曲線（首次抓平倉明細約 1~2 分鐘，不卡啟動）
    threading.Thread(target=build_performance, daemon=True, name="performance").start()

    app = create_app()
    if open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()

    try:
        app.run(debug=False, use_reloader=False, port=port)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        sched.shutdown(wait=False)
        logout_prod()
        logout_api()
        print("[dash] 已結束 ✓")


if __name__ == "__main__":
    run()
