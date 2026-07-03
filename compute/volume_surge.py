# compute/volume_surge.py
import datetime as dt

from store.memory_store import STOCK_STORE, MARKET_STATE
from config import VOLUME_SURGE_RATIO, VOLUME_SHRINK_RATIO


def _cum_ratio_at(curve: list, now_min: int) -> float:
    """曲線在「當下分鐘 now_min」的歷史累積成交比例（取 <= now_min 的最後一點）。"""
    r = 0.0
    for m, ratio in curve:
        if m <= now_min:
            r = ratio
        else:
            break
    return r


def _estimate(base: float, cur: int, curve: list, now_min: int) -> tuple[int, float, str]:
    """動態推估：回傳 (預估今量, 預估比, 狀態)。"""
    ratio_t = _cum_ratio_at(curve, now_min)
    if ratio_t > 0 and cur > 0 and base > 0:
        est_today = cur / ratio_t
        est_ratio = est_today / base
        if est_ratio >= VOLUME_SURGE_RATIO:
            status = "爆量"
        elif est_ratio < VOLUME_SHRINK_RATIO:
            status = "量縮"
        else:
            status = "正常"
        return int(est_today), round(est_ratio, 2), status
    return 0, 0.0, "—"      # 盤前 / 尚無成交 / 無曲線


def calc_volume_surge(now: dt.time | None = None) -> list[dict]:
    """
    模組一：量能監控（動態推估法）。加權/櫃買指數在前（kind="index"），
    股期標的在後（kind="stock"，依預估比降冪）。

    欄位：
      v5d_avg    ── 全日五日均量（昨天當第一天，rolling 5 個交易日）
      today_vol  ── 盤中即時累計量（snapshot.total_volume）
      est_today  ── 預估今量 = 即時累計量 ÷ 當下時間 T 的歷史累積比例
      est_ratio  ── 預估今量 / 五日均量
      status     ── est_ratio > 1.5 爆量、< 1.0 量縮、其餘 正常；盤前無法估為「—」
    """
    now = now or dt.datetime.now().time()
    now_min = now.hour * 60 + now.minute

    # ── 指數（加權 / 櫃買）───────────────────────────────
    index_rows = []
    for key, name, price in [
        ("TAIEX", "加權指數", MARKET_STATE.taiex_close),
        ("OTC",   "櫃買指數", MARKET_STATE.otc_close),
    ]:
        v = MARKET_STATE.index_vol.get(key, {})
        base = float(v.get("v5d", 0) or 0)
        cur = int(v.get("today_vol", 0) or 0)
        est_today, est_ratio, status = _estimate(base, cur, v.get("curve", []), now_min)
        index_rows.append({
            "kind":       "index",
            "symbol":     key,
            "name":       name,
            "price":      price,
            "change_rate": float(v.get("change_rate", 0) or 0),
            "v5d_avg":    round(base, 0),
            "today_vol":  cur,
            "est_today":  est_today,
            "est_ratio":  est_ratio,
            "status":     status,
        })

    # ── 股期標的 ─────────────────────────────────────────
    results = []
    for code, state in list(STOCK_STORE.items()):   # 快照：sync 可能同時增減
        base = state.v5d_avg_vol or state.v5d_morning_avg_vol
        if base <= 0:
            continue  # 盤前均量未取得，跳過

        cur = state.today_total_vol
        est_today, est_ratio, status = _estimate(base, cur, state.cum_ratio_curve, now_min)
        results.append({
            "kind":       "stock",
            "symbol":     code,
            "name":       state.name,
            "price":      state.latest_close,    # 現價
            "change_rate": state.change_rate,    # 今日漲跌幅 %
            "v5d_avg":    round(base, 0),        # 全日五日均量
            "today_vol":  cur,                   # 即時累計量
            "est_today":  est_today,             # 預估今量
            "est_ratio":  est_ratio,             # 預估量 / 五日均量
            "status":     status,
        })

    return index_rows + sorted(results, key=lambda x: x["est_ratio"], reverse=True)
