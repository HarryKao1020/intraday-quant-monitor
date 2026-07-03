# compute/industry_flow.py
from store.memory_store import MARKET_STATE


def get_market_total(period: str = "day") -> float:
    """
    大盤+櫃買成交金額（元）。
    day=今日盤中；week=本週已完成日+今日盤中；lastweek=上週整週（固定）。
    """
    total = 0.0
    for key in ("TAIEX", "OTC"):
        v = MARKET_STATE.index_vol.get(key, {})
        if period == "lastweek":
            total += float(v.get("lastweek_amt", 0) or 0)
            continue
        total += float(v.get("today_vol", 0) or 0)
        if period == "week":
            total += float(v.get("week_amt_past", 0) or 0)
    return total


_ROWS_BY_PERIOD = {
    "day":      lambda: MARKET_STATE.industry_rows,
    "week":     lambda: MARKET_STATE.industry_rows_week,
    "lastweek": lambda: MARKET_STATE.industry_rows_lastweek,
}


def get_industry_display(period: str = "day") -> list[dict]:
    """
    族群金流監控：每族群 平均漲跌幅 / 成交金額總和 / 佔大盤+櫃買總額比例。
    day=當日；week=本週累計（漲跌幅=現價 vs 上週收）；lastweek=上週整週。
    依平均漲跌幅降冪排序。
    """
    rows = _ROWS_BY_PERIOD.get(period, _ROWS_BY_PERIOD["day"])()
    if not rows:
        return []

    market_total = get_market_total(period)
    out = []
    for r in rows:
        ratio = (r["amount"] / market_total * 100.0) if market_total > 0 else None
        out.append({**r, "ratio": ratio})

    return sorted(out, key=lambda x: x["avg_chg"], reverse=True)
