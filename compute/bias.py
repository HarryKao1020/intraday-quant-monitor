# compute/bias.py
from store.memory_store import STOCK_STORE, MARKET_STATE
from config import BIAS_PERIODS, MA_BIAS


def _bias(price: float, series: list, n: int):
    """
    乖離率 = (現價 - N日均線) / N日均線 × 100%
    N 日均線 = series 最後 N 個收盤的平均（series 已含今日現價）。
    """
    if price <= 0 or len(series) < n:
        return None
    ma = sum(series[-n:]) / n
    if ma <= 0:
        return None
    return (price - ma) / ma * 100.0


def _ma_bias(series: list, short: int, long: int):
    """
    均線乖離（月季線乖離）= (短均 - 長均) / 長均 × 100%
    例：月線(20MA) 對 季線(60MA)。正值＝短均在長均之上（多頭排列）。
    """
    if len(series) < long:
        return None
    ma_s = sum(series[-short:]) / short
    ma_l = sum(series[-long:]) / long
    if ma_l <= 0:
        return None
    return (ma_s - ma_l) / ma_l * 100.0


def _bias_row(kind: str, symbol: str, name: str, daily_closes: list, price: float) -> dict:
    """對單一標的算 BIAS_PERIODS 各均線乖離率。"""
    series = list(daily_closes)
    if price and price > 0:
        series = series + [float(price)]   # 今日現價當今日收盤併入均線
    bias = {n: _bias(price, series, n) for n in BIAS_PERIODS}
    ma_bias = _ma_bias(series, MA_BIAS[0], MA_BIAS[1])   # 月季線乖離 (20MA/60MA)
    return {
        "kind":    kind,
        "symbol":  symbol,
        "name":    name,
        "price":   price,
        "bias":    bias,                   # {5: %, 10: %, 20: %}
        "ma_bias": ma_bias,                # (20MA-60MA)/60MA %
        "ok":      price > 0 and any(v is not None for v in bias.values()),
    }


def get_bias_display() -> list[dict]:
    """
    回傳乖離率清單：加權指數、櫃買指數，接著持股個股。
    對 5/10/20/60 日均線；現價取即時值，均線用「過去日收 + 今日現價」。
    """
    rows = [
        _bias_row("index", "TAIEX", "加權指數",
                  MARKET_STATE.taiex_daily_closes, MARKET_STATE.taiex_close),
        _bias_row("index", "OTC", "櫃買指數",
                  MARKET_STATE.otc_daily_closes, MARKET_STATE.otc_close),
    ]
    for code, state in list(STOCK_STORE.items()):   # 快照：sync 可能同時增減
        rows.append(_bias_row("stock", code, state.name,
                              state.daily_closes, state.latest_close))
    return rows
