# compute/momentum.py
import pandas as pd

from store.memory_store import MARKET_STATE
from config import (
    MOM_HIGH_DAYS, MOM_MIN_CHG, MOM_MAX_MA_GAP, MOM_BASE_DAYS, MOM_RET_DAYS,
)


def _ma(series: list, n: int):
    return sum(series[-n:]) / n if len(series) >= n else None


def get_momentum_picks() -> dict:
    """
    動能創高股（momentum.ipynb 的 ranked，盤中版）。
    候選：近 20 日均額 > 10 億（盤前篩好的 mom_hist）且出現在成交值前 200。
    條件（均線用「過去日收 + 今日現價」）：
      - 當日最高價創 120 日新高（盤中只看最高價）
      - 現價 > 20MA、現價 > 60MA、10MA > 20MA
      - 60MA > 120MA，且 60/120MA 乖離 <= 15%
      - 長紅 K：現價 > 開盤，且漲幅 >= 5%
    排序：起漲分數＝候選股內各指標百分位平均（平台寬度、突破幅度、
    月季乖離、10MA乖離、近一年漲幅），越高越前面。
    回傳 {ready, count, items[{code, name, gap_60_120, ma_bias, bias10, score}]}，乖離為 %。
    """
    df = MARKET_STATE.top200_df
    hist = MARKET_STATE.mom_hist
    if not MARKET_STATE.high52w_ready:
        return {"ready": False, "count": 0, "items": []}
    if df is None or df.empty or not hist:
        return {"ready": True, "count": 0, "items": []}

    rows = []
    for _, r in df.iterrows():
        h = hist.get(r.get("code"))
        price = float(r.get("close", 0) or 0)
        open_ = float(r.get("open", 0) or 0)
        high = float(r.get("high", 0) or 0)
        if h is None or price <= 0 or high <= 0:
            continue
        closes, highs = h["close"], h["high"]
        if len(highs) < MOM_HIGH_DAYS - 1 or len(closes) < MOM_BASE_DAYS:
            continue
        series = closes + [price]
        ma10, ma20, ma60, ma120 = (_ma(series, n) for n in (10, 20, 60, 120))
        if ma120 is None:
            continue
        gap = (ma60 / ma120 - 1) * 100
        chg = (price / closes[-1] - 1) * 100
        if not (high >= max(highs[-(MOM_HIGH_DAYS - 1):])
                and price > ma20 and price > ma60 and ma10 > ma20
                and ma60 > ma120 and gap <= MOM_MAX_MA_GAP
                and price > open_ and chg >= MOM_MIN_CHG):
            continue
        base = closes[-MOM_BASE_DAYS:]
        rows.append({
            "code":       r.get("code"),
            "name":       r.get("name", ""),
            "gap_60_120": gap,
            "ma_bias":    (ma20 / ma60 - 1) * 100,
            "bias10":     (price / ma10 - 1) * 100,
            "_base":      max(base) / min(base) - 1,                  # 平台寬度（越窄越好）
            "_break":     price / max(highs) - 1,                     # 突破前 120 日高（越大越好）
            "_ret1y":     (price / closes[-MOM_RET_DAYS] - 1)         # 近一年漲幅（越小越好）
                          if len(closes) >= MOM_RET_DAYS else None,
        })

    if not rows:
        return {"ready": True, "count": 0, "items": []}

    feat = pd.DataFrame(rows)
    feat["_ma_abs"] = feat["ma_bias"].abs()
    pct = pd.DataFrame({
        "base":  feat["_base"].rank(pct=True),
        "break": feat["_break"].rank(pct=True, ascending=False),
        "ma":    feat["_ma_abs"].rank(pct=True),
        "b10":   feat["bias10"].rank(pct=True),
        "ret":   pd.to_numeric(feat["_ret1y"]).rank(pct=True),
    })
    feat["score"] = 1 - pct.mean(axis=1)
    feat = feat.sort_values("score", ascending=False)
    items = feat[["code", "name", "gap_60_120", "ma_bias", "bias10", "score"]].to_dict("records")
    return {"ready": True, "count": len(items), "items": items}
