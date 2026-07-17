# data/performance.py
"""
模組十二：淨值績效曲線（PERF_START_DATE 起，每個交易日）。

淨值口徑 = 年初本金（config.PERF_INITIAL_CAPITAL）+ 每日損益正向累計。
不採帳戶權益數水準，出入金、期貨↔銀行互轉都不影響曲線；期間若有新的
外部入金要參與績效，才填 cache/capital_flows.json（{"YYYY-MM-DD": 金額}，
入金為正），該筆會加進淨值但從當日報酬率排除（TWR）。

歷史每日損益 shioaji 查不到彙總，用平倉明細重建：
1. 平倉損益明細（list_profit_loss_detail，含 entry_date）→ 每筆持有期間
   逐日以標的股票日收盤攤提浮動損益（首日錨進場價、末日錨平倉價，
   合計恆等於回報的平倉損益；乘數由 pnl 反推，不需查已到期合約）。
2. 目前未平倉部位（list_position_detail）同法攤提到今日。
3. 證券已實現損益記在發生日（證券未實現不計）。
4. 淨值 = 年初本金逐日加上當日損益；盤中最後一點以權益數的
   「建置後增量」（排除當日存提）更新今日損益，不用權益數水準。

平倉明細查詢有速率限制（25 req/5s），逐日快取到 cache/fut_pnl_details.json，
歷史日期只抓一次。
"""
import json
import time as _sleep_time
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from config import PERF_START_DATE, PERF_INITIAL_CAPITAL, TAIEX_CODE, OTC_CODE
from store.memory_store import MARKET_STATE, STOCK_STORE, report_data_error, clear_data_error
from data.session import get_api
from data.holdings import _get_prod_api, load_stock_fut_map, _contract_info

_ROOT = Path(__file__).resolve().parent.parent
_DETAIL_CACHE_FILE = _ROOT / "cache" / "fut_pnl_details.json"
_SNAPSHOT_FILE = _ROOT / "cache" / "networth_history.json"
_FLOWS_FILE = _ROOT / "cache" / "capital_flows.json"

_INDEX_FUT_PREFIX = {"TXF", "MXF", "TMF"}      # 台指期系列 → 以加權指數日收攤提
_STD_MULTS = (10, 50, 100, 200, 500, 1000, 2000)
_ACCT_SLEEP = 0.22                             # 帳務查詢間隔（限 25 req/5s）


# ── 小工具 ────────────────────────────────────────────────
def _pdate(s) -> date:
    """'20260713' / '2026-07-13' → date。"""
    s = str(s).strip()[:10]
    if "-" in s:
        return date.fromisoformat(s)
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def _dirsign(direction) -> int:
    return -1 if "Sell" in str(direction) else 1


def _derive_mult(pnl, entry_px, cover_px, qty, sign) -> int | None:
    """由 pnl = (平倉價-進場價)×口數×乘數×方向 反推乘數（免查已到期合約）。"""
    denom = (cover_px - entry_px) * qty * sign
    if abs(denom) < 1e-9 or not pnl:
        return None
    raw = pnl / denom
    best = min(_STD_MULTS, key=lambda m: abs(m - raw))
    return best if abs(best - raw) / best < 0.2 else None


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path: Path, obj) -> None:
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


# ── 平倉損益明細（逐日快取）──────────────────────────────
def _fetch_closed_lots(api, today: date) -> list[dict]:
    """
    期貨平倉明細 → 逐口 lot：{code, entry_date, exit_date, qty, entry_px,
    cover_px, pnl, fee, tax, sign}。歷史日期讀快取，今日與新日期才查 API。
    """
    rows = api.list_profit_loss(
        api.futopt_account,
        begin_date=PERF_START_DATE.isoformat(),
        end_date=today.isoformat(),
    )
    by_date: dict[str, list] = defaultdict(list)
    for r in rows:
        by_date[str(r.date)].append(r)

    cache = _load_json(_DETAIL_CACHE_FILE, {})
    lots: list[dict] = []
    dirty = False
    for dkey in sorted(by_date):
        group = by_date[dkey]
        cached = cache.get(dkey)
        is_today = _pdate(dkey) >= today          # 今日紀錄盤中會增加，不吃快取
        if cached and cached.get("n") == len(group) and not is_today:
            for lot_rows in cached["rows"]:
                lots.extend(lot_rows)
            continue
        day_rows = []
        for r in group:
            _sleep_time.sleep(_ACCT_SLEEP)
            details = api.list_profit_loss_detail(api.futopt_account, r.id)
            lot_rows = []
            for d in details:
                lot_rows.append({
                    "code":       d.code,
                    "entry_date": _pdate(d.entry_date).isoformat(),
                    "exit_date":  _pdate(d.date).isoformat(),
                    "qty":        int(d.quantity),
                    "entry_px":   float(d.entry_price),
                    "cover_px":   float(d.cover_price),
                    "pnl":        float(d.pnl),
                    "fee":        float(d.fee),
                    "tax":        float(d.tax),
                    "sign":       _dirsign(d.direction),
                })
            day_rows.append(lot_rows)
            lots.extend(lot_rows)
        cache[dkey] = {"n": len(group), "rows": day_rows}
        dirty = True
    if dirty:
        _save_json(_DETAIL_CACHE_FILE, cache)
    return lots


def _fetch_open_lots(api) -> list[dict]:
    """未平倉部位逐口 lot（含進場日），乘數優先由 pnl 反推、否則查合約。"""
    positions = api.list_positions(api.futopt_account)
    lots = []
    for pos in positions:
        _sleep_time.sleep(_ACCT_SLEEP)
        details = api.list_position_detail(api.futopt_account, pos.id)
        for d in details:
            qty = int(d.quantity)
            if qty <= 0:
                continue
            sign = _dirsign(d.direction)
            entry_px = float(d.price)
            last_px = float(d.last_price) or entry_px
            pnl = float(getattr(d, "pnl", 0) or 0)
            mult = _derive_mult(pnl, entry_px, last_px, qty, sign)
            if mult is None:
                _, mult = _contract_info(api, d.code)
            lots.append({
                "code":       d.code,
                "entry_date": _pdate(d.date).isoformat(),
                "qty":        qty,
                "entry_px":   entry_px,
                "last_px":    last_px,
                "sign":       sign,
                "mult":       mult,
            })
    return lots


# ── 證券帳戶 ──────────────────────────────────────────────
def _stock_market_value(api) -> float:
    """證券庫存市值（零股以 Share 單位計）。"""
    from shioaji.constant import Unit
    total = 0.0
    for unit in (Unit.Share,):
        try:
            for pos in api.list_positions(api.stock_account, unit=unit):
                total += float(pos.last_price) * float(pos.quantity)
            return total
        except Exception as e:
            print(f"[performance] 證券庫存查詢失敗: {e}")
    return total


def _stock_realized(api, today: date) -> list[tuple[date, float]]:
    try:
        rows = api.list_profit_loss(
            api.stock_account,
            begin_date=PERF_START_DATE.isoformat(),
            end_date=today.isoformat(),
        )
        return [(_pdate(r.date), float(r.pnl)) for r in rows]
    except Exception as e:
        print(f"[performance] 證券損益查詢失敗: {e}")
        return []


# ── 指數 / 個股日收序列 ──────────────────────────────────
def _fetch_daily_closes(contract, start: date, end: date) -> dict[date, float]:
    """分鐘 K → 每日收盤（kbars 每段上限 30 天）。"""
    api = get_api()
    frames = []
    seg_end = end
    while seg_end >= start:
        seg_start = max(seg_end - timedelta(days=29), start)
        kb = api.kbars(contract=contract,
                       start=seg_start.strftime("%Y-%m-%d"),
                       end=seg_end.strftime("%Y-%m-%d"),
                       timeout=60_000)
        frames.append(pd.DataFrame(kb.dict()))
        seg_end = seg_start - timedelta(days=1)
    df = pd.concat(frames, ignore_index=True)
    if df.empty:
        return {}
    df["ts"] = pd.to_datetime(df["ts"])
    df["d"] = df["ts"].dt.date
    daily = df.sort_values("ts").groupby("d")["Close"].last()
    return {d: float(c) for d, c in daily.items()}


def _index_series(base_date: date, today: date) -> tuple[dict, dict]:
    """加權 / 櫃買日收 {date: close}（不含今日）。優先重用 MARKET_STATE。"""
    out = []
    for dates_attr, closes_attr, exch, code in [
        ("taiex_daily_dates", "taiex_daily_closes", "TSE", TAIEX_CODE),
        ("otc_daily_dates",   "otc_daily_closes",   "OTC", OTC_CODE),
    ]:
        dts = getattr(MARKET_STATE, dates_attr)
        cls = getattr(MARKET_STATE, closes_attr)
        if dts and dts[0] <= base_date:
            out.append({d: float(c) for d, c in zip(dts, cls)})
        else:
            api = get_api()
            contract = getattr(api.Contracts.Indexs, exch)[code]
            series = _fetch_daily_closes(contract, base_date - timedelta(days=10), today)
            series.pop(today, None)   # 與 MARKET_STATE 口徑一致：不含今日
            out.append(series)
    return out[0], out[1]


def _underlying_closes(needs: dict[str, tuple[date, date]]) -> dict[str, dict[date, float]]:
    """
    多日持倉攤提所需的標的股票日收。needs = {stock_code: (min_date, max_date)}。
    已在 STOCK_STORE（現有持股）且涵蓋區間者直接用，其餘抓該區間 kbars。
    """
    api = get_api()
    out: dict[str, dict[date, float]] = {}
    for code, (d0, d1) in needs.items():
        st = STOCK_STORE.get(code)
        if st is not None and st.daily_dates and st.daily_dates[0] <= d0:
            out[code] = {d: float(c) for d, c in zip(st.daily_dates, st.daily_closes)}
            continue
        try:
            contract = api.Contracts.Stocks[code]
            if contract is None:
                raise KeyError(code)
            out[code] = _fetch_daily_closes(contract, d0, d1)
        except Exception as e:
            print(f"[performance] {code} 日收抓取失敗（{e}），該標的多日持倉改記平倉日")
            out[code] = {}
    return out


# ── 逐日攤提 ─────────────────────────────────────────────
def _spread(daily_pnl, cal: list[date], entry_d: date, exit_d: date,
            entry_px: float, exit_px: float, qty: int, sign: int, mult: int,
            closes: dict[date, float]) -> None:
    """
    一口部位的損益逐日攤提：首日 = 標的收盤 − 進場價、中間日 = 收盤差、
    末日 = 平倉價 − 前日收盤；合計恆等於 (平倉-進場)×口數×乘數×方向。
    entry 早於曲線起點時剪裁到起點（以起點收盤當進場錨）。
    """
    total = (exit_px - entry_px) * qty * mult * sign
    if entry_d < cal[0]:
        entry_d = cal[0]
        entry_px = closes.get(entry_d, entry_px)
    i0 = bisect_left(cal, entry_d)
    i1 = bisect_right(cal, exit_d) - 1
    if i0 >= len(cal) or i1 < i0:
        daily_pnl[min(exit_d, cal[-1])] += total
        return
    days = cal[i0:i1 + 1]
    if len(days) == 1:
        daily_pnl[days[0]] += total
        return
    # 建立每日錨價：進場價 → 各日收盤（缺值前補）→ 平倉價
    px = [entry_px]
    prev = None
    for d in days[:-1]:          # 首日與中間日用標的收盤
        c = closes.get(d, prev)
        prev = c if c is not None else prev
        px.append(prev if prev is not None else entry_px)
    px.append(exit_px)
    # px[0]=進場價、px[1..n-1]=首日/中間日收盤、px[n]=平倉價
    for k, d in enumerate(days):
        daily_pnl[d] += (px[k + 1] - px[k]) * qty * mult * sign


def _prefix_underlying(code: str, fut_map: dict) -> str | None:
    """期貨合約代碼 → 標的股票代碼；台指期回 '__TAIEX__'；其餘 None。"""
    prefix = code[:3]
    if prefix in fut_map:
        return fut_map[prefix]["code"]
    if prefix in _INDEX_FUT_PREFIX:
        return "__TAIEX__"
    return None


# ── 主流程 ────────────────────────────────────────────────
def build_performance() -> None:
    """完整重建績效曲線（啟動時背景執行、每日收盤後排程執行）。"""
    try:
        _build()
        clear_data_error("淨值績效")
        p = MARKET_STATE.perf
        print(f"[performance] 績效曲線完成：{p['dates'][0]} ~ {p['dates'][-1]}"
              f"（{len(p['dates'])} 點），今年 {p['mine'][-1]:+.2f}%")
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[performance] 績效曲線建立失敗: {e}")
        report_data_error("淨值績效", e)


def _build() -> None:
    today = date.today()
    prod = _get_prod_api()

    # 1. 指數日收與交易日曆（基準 = 起算日前最後一個交易日收盤）
    taiex, otc = _index_series(PERF_START_DATE - timedelta(days=14), today)
    base_candidates = [d for d in taiex if d < PERF_START_DATE]
    if not base_candidates:
        raise RuntimeError("指數歷史不足，無法定出績效基準日")
    base_date = max(base_candidates)
    cal = sorted(d for d in taiex if base_date <= d < today)
    is_trading_today = today.weekday() < 5
    if is_trading_today:
        cal.append(today)

    # 2. 帳務：平倉明細（快取）、未平倉、證券已實現；
    #    權益數只留作盤中增量更新的參考點，不當淨值水準
    closed = _fetch_closed_lots(prod, today)
    open_lots = _fetch_open_lots(prod)
    stk_realized = _stock_realized(prod, today)
    _sleep_time.sleep(_ACCT_SLEEP)
    margin = prod.margin(prod.futopt_account)

    # 3. 多日持倉所需的標的日收
    fut_map = load_stock_fut_map()
    needs: dict[str, tuple[date, date]] = {}

    def _note_need(code, d0, d1):
        und = _prefix_underlying(code, fut_map)
        if und in (None, "__TAIEX__"):
            return
        d0 = min(d0, needs[und][0]) if und in needs else d0
        d1 = max(d1, needs[und][1]) if und in needs else d1
        needs[und] = (d0, d1)

    for lot in closed:
        e, x = _pdate(lot["entry_date"]), _pdate(lot["exit_date"])
        if e < x:
            _note_need(lot["code"], min(e, base_date), x)
    for lot in open_lots:
        e = _pdate(lot["entry_date"])
        if e < today:
            _note_need(lot["code"], min(e, base_date), today)
    und_closes = _underlying_closes(needs)
    und_closes["__TAIEX__"] = taiex

    # 4. 逐日損益
    daily_pnl: dict[date, float] = defaultdict(float)
    for lot in closed:
        e, x = _pdate(lot["entry_date"]), _pdate(lot["exit_date"])
        mult = _derive_mult(lot["pnl"], lot["entry_px"], lot["cover_px"],
                            lot["qty"], lot["sign"])
        und = _prefix_underlying(lot["code"], fut_map)
        closes = und_closes.get(und, {}) if und else {}
        if e == x or mult is None or not closes:
            daily_pnl[min(x, cal[-1])] += lot["pnl"]
        else:
            _spread(daily_pnl, cal, e, x, lot["entry_px"], lot["cover_px"],
                    lot["qty"], lot["sign"], mult, closes)
        daily_pnl[min(x, cal[-1])] -= lot["fee"] + lot["tax"]
    for lot in open_lots:
        e = _pdate(lot["entry_date"])
        und = _prefix_underlying(lot["code"], fut_map)
        closes = und_closes.get(und, {}) if und else {}
        _spread(daily_pnl, cal, e, cal[-1], lot["entry_px"], lot["last_px"],
                lot["qty"], lot["sign"], lot["mult"], closes)
    for d, pnl in stk_realized:
        daily_pnl[min(d, cal[-1])] += pnl

    # 5. 外部資金流（手動維護；期間新入金才需要填）
    flows_raw = _load_json(_FLOWS_FILE, {})
    flows = {date.fromisoformat(k): float(v) for k, v in flows_raw.items()}

    # 6. 淨值 = 年初本金 + 每日損益正向累計（+外部入金）
    nv = {cal[0]: float(PERF_INITIAL_CAPITAL)}
    for i in range(1, len(cal)):
        d, prev = cal[i], cal[i - 1]
        nv[d] = nv[prev] + daily_pnl.get(d, 0.0) + flows.get(d, 0.0)
    nv_today = nv[cal[-1]]

    # 7. 累計報酬（TWR：逐日排除外部資金流）
    mine = [0.0]
    for i in range(1, len(cal)):
        d, prev = cal[i], cal[i - 1]
        r = (nv[d] - flows.get(d, 0.0)) / nv[prev] - 1 if nv[prev] > 0 else 0.0
        mine.append(((1 + mine[-1] / 100) * (1 + r) - 1) * 100)

    def _year_open(series: dict[date, float]) -> tuple[str, float]:
        """今年第一個交易日的 (日期, 收盤)；序列不足時回 ("", 0.0)。"""
        ds = sorted(d for d in series if d.year == today.year)
        return (ds[0].isoformat(), series[ds[0]]) if ds else ("", 0.0)

    def _index_base(series: dict[date, float]) -> float:
        return series.get(base_date) or next(
            series[d] for d in sorted(series, reverse=True) if d <= base_date)

    def _index_line(series: dict[date, float], live: float) -> list[float]:
        base = _index_base(series)
        vals, prev_v = [], 0.0
        for d in cal:
            if d in series:
                prev_v = (series[d] / base - 1) * 100
            elif d == cal[-1] and is_trading_today and live > 0:
                prev_v = (live / base - 1) * 100
            vals.append(prev_v)
        return vals

    taiex_y0_date, taiex_y0 = _year_open(taiex)
    otc_y0_date, otc_y0 = _year_open(otc)

    MARKET_STATE.perf = {
        "ready":     True,
        "dates":     [d.isoformat() for d in cal],
        "mine":      mine,
        "taiex":     _index_line(taiex, MARKET_STATE.taiex_close),
        "otc":       _index_line(otc, MARKET_STATE.otc_close),
        "nv":        [nv[d] for d in cal],
        "nv_today":  nv_today,
        "base_nv":   nv[cal[0]],
        # 盤中增量更新參考點：今日損益變化 = 權益數增量 − 存提增量
        "nv_built":  nv_today,
        "equity_ref": float(margin.equity_amount),
        "dw_ref":     float(margin.deposit_withdrawal),
        "base_date": base_date.isoformat(),
        "asof":      datetime.now(),
        # 指數年初第一個交易日收盤（MOD.12 摘要列顯示用）
        "taiex_y0_date": taiex_y0_date, "taiex_y0": taiex_y0,
        "otc_y0_date":   otc_y0_date,   "otc_y0":   otc_y0,
        # 供盤中輕量更新最後一點
        "taiex_base": _index_base(taiex),
        "otc_base":   _index_base(otc),
        "today_is_last": is_trading_today,
    }


def refresh_today_point() -> None:
    """盤中輕量更新曲線最後一點，不重建歷史。
    今日損益變化 = 權益數建置後增量 − 存提增量（同日差分，出入金與
    權益數水準失真都不影響），加回建置時的今日淨值。"""
    p = MARKET_STATE.perf
    if not p.get("ready") or not p.get("today_is_last") or len(p["mine"]) < 2:
        return
    try:
        prod = _get_prod_api()
        margin = prod.margin(prod.futopt_account)
        delta = ((float(margin.equity_amount) - p["equity_ref"])
                 - (float(margin.deposit_withdrawal) - p["dw_ref"]))
        nv_now = p["nv_built"] + delta
        nv_prev = p["nv"][-2]
        if nv_prev > 0:
            flows = _load_json(_FLOWS_FILE, {})
            flow_today = float(flows.get(date.today().isoformat(), 0.0))
            r = (nv_now - flow_today) / nv_prev - 1
            p["mine"][-1] = ((1 + p["mine"][-2] / 100) * (1 + r) - 1) * 100
        p["nv"][-1] = nv_now
        p["nv_today"] = nv_now
        if MARKET_STATE.taiex_close > 0:
            p["taiex"][-1] = (MARKET_STATE.taiex_close / p["taiex_base"] - 1) * 100
        if MARKET_STATE.otc_close > 0:
            p["otc"][-1] = (MARKET_STATE.otc_close / p["otc_base"] - 1) * 100
        p["asof"] = datetime.now()
        clear_data_error("淨值績效")
    except Exception as e:
        print(f"[performance] 今日點更新失敗: {e}")
        report_data_error("淨值績效", e)


def snapshot_networth() -> None:
    """收盤後記錄當日真實淨值（歷史段的錨點，累積越久越準）。"""
    try:
        prod = _get_prod_api()
        margin = prod.margin(prod.futopt_account)
        _sleep_time.sleep(_ACCT_SLEEP)
        bank = float(prod.account_balance().acc_balance)
        stk_value = _stock_market_value(prod)
        snaps = _load_json(_SNAPSHOT_FILE, {})
        snaps[date.today().isoformat()] = {
            "fut_equity": float(margin.equity_amount),
            "bank": bank,
            "stock_value": stk_value,
            "total": float(margin.equity_amount) + bank + stk_value,
            "deposit_withdrawal": float(margin.deposit_withdrawal),
        }
        _save_json(_SNAPSHOT_FILE, snaps)
        print(f"[performance] 已快照今日淨值 {snaps[date.today().isoformat()]['total']:,.0f}")
    except Exception as e:
        print(f"[performance] 淨值快照失敗: {e}")


def daily_perf_job() -> None:
    """每日收盤後排程：先快照真實淨值，再重建曲線。"""
    if date.today().weekday() >= 5:
        return
    snapshot_networth()
    build_performance()
