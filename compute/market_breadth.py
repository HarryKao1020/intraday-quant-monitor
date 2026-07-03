# compute/market_breadth.py
from store.memory_store import MARKET_STATE


def calc_market_breadth() -> dict:
    """
    從 top200_df 與樣本家數計算市場廣度指標。
    回傳 dict，供 Dash 儀表板顯示。

    漲跌幅計算：昨收 = close - change_price；漲跌幅% = change_price / 昨收 * 100。
    昨收為 0 時跳過該筆，避免除以零。
    """
    df = MARKET_STATE.top200_df
    if df.empty:
        return {}

    # 計算漲跌幅%（避免昨收為 0）
    df = df.copy()
    df["prev_close"] = df["close"] - df["change_price"]
    mask = df["prev_close"] != 0
    df.loc[mask, "chg_pct"] = df.loc[mask, "change_price"] / df.loc[mask, "prev_close"] * 100
    df["chg_pct"] = df["chg_pct"].fillna(0)

    # 前200 漲跌家數
    top200_up   = int(df["change_type"].isin([1, 2]).sum())
    top200_down = int(df["change_type"].isin([4, 5]).sum())

    # 簡單平均漲跌幅
    avg_chg = float(df["chg_pct"].mean())

    # 成交值加權漲跌幅
    total_amt = df["total_amount"].sum()
    wavg_chg = (
        float((df["chg_pct"] * df["total_amount"]).sum() / total_amt)
        if total_amt > 0 else 0.0
    )

    # 加權 / 櫃買漲跌幅
    taiex_prev = MARKET_STATE.taiex_prev_close
    otc_prev   = MARKET_STATE.otc_prev_close
    taiex_chg_pct = (
        (MARKET_STATE.taiex_close - taiex_prev) / taiex_prev * 100
        if taiex_prev > 0 else 0.0
    )
    otc_chg_pct = (
        (MARKET_STATE.otc_close - otc_prev) / otc_prev * 100
        if otc_prev > 0 else 0.0
    )

    return {
        # 前200 成交值
        "top200_up":          top200_up,
        "top200_down":        top200_down,
        "top200_avg_chg_pct": round(avg_chg, 2),
        "top200_wavg_chg_pct": round(wavg_chg, 2),
        # 指數對照
        "taiex_chg_pct":      round(taiex_chg_pct, 2),
        "otc_chg_pct":        round(otc_chg_pct, 2),
        # 超額報酬：前200成交值加權漲跌幅 − 加權指數漲跌幅
        "excess_vs_taiex":    round(wavg_chg - taiex_chg_pct, 2),
    }
