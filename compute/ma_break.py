# compute/ma_break.py
from store.memory_store import MARKET_STATE
from config import MA_BREAK_PERIODS


def get_ma_break() -> dict:
    """
    成交值前 200 檔中，現價站上月線(20MA) / 季線(60MA) / 半年線(120MA) 的家數。

    均線用「過去日收 + 今日現價」（與模組五乖離率同一口徑）：
      今日 MA(n) = (最近 n-1 個日收 + 現價) / n
      昨日 MA(n) = (最近 n 個日收) / n
    above（線上）：現價 > 今日 MA。
    newly（今日站上）：昨收 ≤ 昨日 MA 且現價 > 今日 MA，即今天由下而上站上。
    bias（平均乖離）：有效檔數的 (現價/今日MA − 1)×100 平均，正值 = 整體在線上方。

    回傳 {ready, total, rows[{n, label, above, newly, valid, pct, bias}]}；
    valid 為該均線有足夠日收可算的檔數（= 該列的分母）。
    """
    df = MARKET_STATE.top200_df
    tails = MARKET_STATE.close_tails

    if not MARKET_STATE.high52w_ready or not tails:
        return {"ready": False, "total": 0, "rows": []}
    if df is None or df.empty:
        return {"ready": True, "total": 0, "rows": []}

    stat = {n: {"above": 0, "newly": 0, "valid": 0, "bias_sum": 0.0}
            for n, _ in MA_BREAK_PERIODS}
    for _, r in df.iterrows():
        price = float(r.get("close", 0) or 0)
        tail = tails.get(r.get("code"))
        if price <= 0 or not tail:
            continue
        prev_close = tail[-1]
        for n, _label in MA_BREAK_PERIODS:
            if len(tail) < n:
                continue                      # 上市未滿 n 日 → 不計入分母
            s = stat[n]
            s["valid"] += 1
            ma_today = (sum(tail[-(n - 1):]) + price) / n
            ma_prev = sum(tail[-n:]) / n
            s["bias_sum"] += (price / ma_today - 1) * 100
            if price > ma_today:
                s["above"] += 1
                if prev_close <= ma_prev:     # 昨天還在線下 → 今日站上
                    s["newly"] += 1

    rows = []
    for n, label in MA_BREAK_PERIODS:
        s = stat[n]
        rows.append({
            "n":     n,
            "label": label,
            "above": s["above"],
            "newly": s["newly"],
            "valid": s["valid"],
            "pct":   (s["above"] / s["valid"] * 100) if s["valid"] else 0.0,
            "bias":  (s["bias_sum"] / s["valid"]) if s["valid"] else 0.0,
        })

    return {"ready": True, "total": int(len(df)), "rows": rows}


def get_ma_bias_top(n: int, top: int = 20) -> list[dict]:
    """
    成交值前 200 檔中，對 n 日均線乖離最大的前 top 檔（正乖離在前）。
    均線口徑與 get_ma_break 相同（今日 MA 含現價）。
    回傳 [{code, name, close, ma, bias}]，bias 為 %。
    """
    df = MARKET_STATE.top200_df
    tails = MARKET_STATE.close_tails
    if df is None or df.empty or not tails:
        return []
    items = []
    for _, r in df.iterrows():
        price = float(r.get("close", 0) or 0)
        tail = tails.get(r.get("code"))
        if price <= 0 or not tail or len(tail) < n:
            continue
        ma_today = (sum(tail[-(n - 1):]) + price) / n
        items.append({
            "code":  r.get("code"),
            "name":  r.get("name", ""),
            "close": price,
            "ma":    ma_today,
            "bias":  (price / ma_today - 1) * 100,
        })
    items.sort(key=lambda x: x["bias"], reverse=True)
    return items[:top]
