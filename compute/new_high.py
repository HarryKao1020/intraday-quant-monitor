# compute/new_high.py
from bisect import bisect_right

from store.memory_store import MARKET_STATE


def get_new_highs() -> dict:
    """
    成交值前 200 檔中，今日盤中最高價 >= 前一年最高價者 → 創一年新高。
    days_gap：距離前高「最近一次出現」隔了幾個交易日（昨天也是高點=1，
    100 個交易日前的高點今天才突破=100）。
    回傳 {ready, total, count, items[]}；items 依成交值排序（top200_df 原順序）。
    """
    df = MARKET_STATE.top200_df
    highs = MARKET_STATE.high52w
    dates = MARKET_STATE.high52w_dates   # 年內交易日 ISO 升冪（不含今天）

    if not MARKET_STATE.high52w_ready:
        return {"ready": False, "total": 0, "count": 0, "items": []}
    if df is None or df.empty:
        return {"ready": True, "total": 0, "count": 0, "items": []}

    items = []
    for _, r in df.iterrows():
        code = r.get("code")
        today_high = float(r.get("high", 0) or 0)
        close = float(r.get("close", 0) or 0)
        rec = highs.get(code)
        if rec is None or today_high <= 0:
            continue
        prior, prior_date = float(rec[0]), rec[1]
        if today_high >= prior:        # 今日高點突破前一年最高
            pct = (close / prior - 1) * 100 if prior > 0 else 0.0
            # 前高日期之後的交易日數 + 今天 = 隔幾個交易日創高
            days_gap = (len(dates) - bisect_right(dates, prior_date) + 1) if prior_date else 0
            items.append({
                "code":           code,
                "name":           r.get("name", ""),
                "close":          close,
                "today_high":     today_high,
                "prior_high":     prior,
                "pct":            round(pct, 2),      # 收盤（現價）相對前高
                "close_new_high": close >= prior,     # 收盤（現價）是否也站上前高
                "days_gap":       days_gap,           # 隔幾個交易日創高
            })

    return {"ready": True, "total": int(len(df)), "count": len(items), "items": items}
