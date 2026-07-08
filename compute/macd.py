import pandas as pd
from datetime import date

from store.memory_store import STOCK_STORE, MARKET_STATE
from config import MACD_FAST, MACD_SLOW, MACD_SIGNAL, MACD_MIN_BARS


def calc_macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """
    計算 MACD。回傳 DataFrame，欄位：dif, macd_signal, histogram
    使用 EMA（adjust=False，與多數交易軟體一致）
    """
    ema_fast   = close.ewm(span=fast,   adjust=False).mean()
    ema_slow   = close.ewm(span=slow,   adjust=False).mean()
    dif        = ema_fast - ema_slow
    macd_sig   = dif.ewm(span=signal,   adjust=False).mean()
    histogram  = dif - macd_sig
    return pd.DataFrame({
        "dif":        dif,
        "macd_signal": macd_sig,
        "histogram":  histogram,
    })


def _hist_status(hist: float, prev: float) -> str:
    """
    柱狀體相對前一日的狀態：
    翻紅 / 翻綠（穿越零軸）、紅柱增長 / 紅柱縮短、綠柱增長 / 綠柱縮短。
    """
    if prev <= 0 < hist:
        return "翻紅"
    if prev >= 0 > hist:
        return "翻綠"
    if hist > 0:                      # 紅柱
        return "紅柱增長" if hist > prev else "紅柱縮短"
    if hist < 0:                      # 綠柱（越負越長）
        return "綠柱增長" if hist < prev else "綠柱縮短"
    return "—"


def _weekly_closes(daily_dates: list, daily_closes: list, current_price: float) -> list:
    """
    日收盤（升冪、含日期）轉週收盤：每週取最後一個交易日收盤。
    盤中最新價（>0）接為本週（成形中）的收盤，序列最後一根即本週。
    """
    if not daily_dates or len(daily_dates) != len(daily_closes):
        return []
    s = pd.Series(daily_closes, index=pd.to_datetime(daily_dates), dtype=float)
    if current_price and current_price > 0:
        s.loc[pd.Timestamp(date.today())] = float(current_price)
    return s.resample("W-FRI").last().dropna().tolist()


def _macd_row(kind: str, symbol: str, name: str,
              daily_closes: list, daily_dates: list, current_price: float) -> dict:
    """
    用「過去日收 + 當前即時價」即時算一檔/一指數的 MACD 狀態。
    daily_closes / daily_dates 為升冪過去交易日收盤與日期；
    current_price 為盤中最新價（>0 才接上去）。日線之外同步算週線柱狀體。
    """
    base = {
        "kind": kind, "symbol": symbol, "name": name,
        "dif": None, "macd": None, "histogram": None, "prev_histogram": None,
        "bar": None, "cross": "", "hist_status": "", "ok": False,
        "w_histogram": None, "w_prev_histogram": None,
        "w_bar": None, "w_hist_status": "", "w_ok": False,
    }

    closes = list(daily_closes)
    if current_price and current_price > 0:
        closes = closes + [float(current_price)]   # 接上今日即時價
    if len(closes) < MACD_MIN_BARS:
        return base   # 日線根數不足，標記資料不足

    df = calc_macd(pd.Series(closes), fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL)
    hist = float(df["histogram"].iloc[-1])
    prev = float(df["histogram"].iloc[-2])
    dif  = float(df["dif"].iloc[-1])
    macd = float(df["macd_signal"].iloc[-1])

    cross = ""
    if prev <= 0 < hist:
        cross = "golden"   # 黃金交叉
    elif prev >= 0 > hist:
        cross = "death"    # 死亡交叉

    base.update({
        "dif": dif, "macd": macd, "histogram": hist, "prev_histogram": prev,
        "bar": "red" if hist > 0 else "green",   # 紅柱（多）/ 綠柱（空）
        "cross": cross,
        "hist_status": _hist_status(hist, prev),   # 柱狀體前一日狀態比較
        "ok": True,
    })

    # ── 週 MACD：本週（成形中）柱狀體 vs 前一完整週 ─────────
    w_closes = _weekly_closes(daily_dates, daily_closes, current_price)
    if len(w_closes) >= MACD_MIN_BARS:
        wdf = calc_macd(pd.Series(w_closes), fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL)
        w_hist = float(wdf["histogram"].iloc[-1])
        w_prev = float(wdf["histogram"].iloc[-2])
        base.update({
            "w_histogram": w_hist, "w_prev_histogram": w_prev,
            "w_bar": "red" if w_hist > 0 else "green",
            "w_hist_status": _hist_status(w_hist, w_prev),   # 柱狀體前一週狀態比較
            "w_ok": True,
        })
    return base


def get_macd_display() -> list[dict]:
    """
    回傳 MACD 顯示清單：加權指數、櫃買指數，接著由股期部位推導的持股。
    每筆含 dif、macd（訊號線）、histogram（柱狀體）與紅綠/交叉旗標，盤中即時。
    """
    rows = [
        _macd_row("index", "TAIEX", "加權指數",
                  MARKET_STATE.taiex_daily_closes, MARKET_STATE.taiex_daily_dates,
                  MARKET_STATE.taiex_close),
        _macd_row("index", "OTC", "櫃買指數",
                  MARKET_STATE.otc_daily_closes, MARKET_STATE.otc_daily_dates,
                  MARKET_STATE.otc_close),
    ]
    for code, state in list(STOCK_STORE.items()):   # 快照：sync 可能同時增減
        rows.append(_macd_row("stock", code, state.name,
                              state.daily_closes, state.daily_dates,
                              state.latest_close))
    return rows
