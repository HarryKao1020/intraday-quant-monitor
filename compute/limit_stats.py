# compute/limit_stats.py
from store.memory_store import MARKET_STATE
from config import LIMIT_PRICE_THRESHOLD


def get_limit_stats() -> dict:
    """讀取 MARKET_STATE 中已由 scanner 更新的漲跌停統計"""
    lu_low  = MARKET_STATE.limit_up_low
    lu_high = MARKET_STATE.limit_up_high
    ld_low  = MARKET_STATE.limit_down_low
    ld_high = MARKET_STATE.limit_down_high

    return {
        # 全市場（漲幅榜 / 跌幅榜各前 200）
        "limit_up_total":    lu_low + lu_high,
        "limit_up_low":      lu_low,    # 漲停 且 < 50 元（低價投機）
        "limit_up_high":     lu_high,   # 漲停 且 >= 50 元（中高價強勢）
        "limit_down_total":  ld_low + ld_high,
        "limit_down_low":    ld_low,    # 跌停 且 < 50 元（低價地雷）
        "limit_down_high":   ld_high,   # 跌停 且 >= 50 元（系統性賣壓）
        # 成交值前 200 檔
        "top200_limit_up":   MARKET_STATE.top200_limit_up,
        "top200_limit_down": MARKET_STATE.top200_limit_down,
        # 高價前 100 檔
        "highprice_limit_up":   MARKET_STATE.highprice_limit_up,
        "highprice_limit_down": MARKET_STATE.highprice_limit_down,
        "price_threshold":   LIMIT_PRICE_THRESHOLD,
        "last_update":       MARKET_STATE.last_scanner_update,
    }
