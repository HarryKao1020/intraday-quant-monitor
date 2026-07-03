# data/scanner.py
import datetime as dt

import pandas as pd
import shioaji as sj

from store.memory_store import MARKET_STATE
from config import (
    SCANNER_COUNT, LIMIT_PRICE_THRESHOLD, HIGH_PRICE_COUNT,
    TAIEX_CODE, OTC_CODE,
)
from data.session import get_api


def build_high_price_universe() -> None:
    """
    盤前建立「高價排行」universe：抓全市場前一交易日 daily_quotes，
    依收盤價由高到低取前 HIGH_PRICE_COUNT 檔代碼，整日固定。
    結果寫入 MARKET_STATE.high_price_codes。
    """
    api = get_api()

    # 往前找最近一個有資料的交易日（跳過週末 / 假日）
    day = dt.date.today()
    for _ in range(10):
        day = day - dt.timedelta(days=1)
        try:
            dq = api.daily_quotes(date=day)
        except Exception as e:
            print(f"[scanner] daily_quotes {day} 失敗: {e}")
            continue
        df = pd.DataFrame(dq.dict())
        if not df.empty and df["Close"].notna().any():
            break
    else:
        print("[scanner] 找不到可用的前一交易日 daily_quotes，高價 universe 未建立")
        return

    # 只取 4 碼代碼（一般上市股票 / ETF），排除權證等長代碼
    df = df[df["Code"].str.len() == 4]
    df = df[df["Close"] > 0].sort_values("Close", ascending=False)

    codes = df["Code"].head(HIGH_PRICE_COUNT).tolist()
    MARKET_STATE.high_price_codes = codes
    top = df.head(3)[["Code", "Close"]].to_dict("records")
    print(f"[scanner] 高價 universe 已建立（基準日 {day}）"
          f" 共 {len(codes)} 檔，最高價範例: {top}")


def _count_highprice_limits() -> tuple[int, int]:
    """對高價 universe 取一次 snapshots，回傳 (漲停數, 跌停數)。"""
    codes = MARKET_STATE.high_price_codes
    if not codes:
        return 0, 0

    api = get_api()
    contracts = [api.Contracts.Stocks[c] for c in codes]
    contracts = [c for c in contracts if c is not None]

    snaps = api.snapshots(contracts)
    # snapshot.change_type 是 ChangeType 列舉（字串值），與 scanner 的 int 不同
    up = sum(1 for s in snaps if s.change_type == sj.ChangeType.LimitUp)
    dn = sum(1 for s in snaps if s.change_type == sj.ChangeType.LimitDown)
    return up, dn


def update_index_quotes() -> None:
    """
    用 snapshots 取加權（TSE 001）/ 櫃買（OTC 101）指數現價，寫入 MARKET_STATE。
    供模組二指數對照使用；以 30 秒輪詢取代 tick 訂閱（不需 streaming）。
    """
    api = get_api()
    try:
        taiex = api.Contracts.Indexs.TSE[TAIEX_CODE]
        otc   = api.Contracts.Indexs.OTC[OTC_CODE]
        snaps = api.snapshots([taiex, otc])
        by_code = {s.code: s for s in snaps}
        if TAIEX_CODE in by_code:
            s = by_code[TAIEX_CODE]
            MARKET_STATE.taiex_close = float(s.close)
            entry = MARKET_STATE.index_vol.setdefault("TAIEX", {})
            entry["today_vol"] = int(s.total_amount)     # 模組一：當日累計成交金額（元）
            entry["change_rate"] = float(s.change_rate)
        if OTC_CODE in by_code:
            s = by_code[OTC_CODE]
            MARKET_STATE.otc_close = float(s.close)
            entry = MARKET_STATE.index_vol.setdefault("OTC", {})
            entry["today_vol"] = int(s.total_amount)     # 成交金額（元）
            entry["change_rate"] = float(s.change_rate)
    except Exception as e:
        print(f"[scanner] 指數現價更新失敗: {e}")


def update_scanners() -> None:
    """
    呼叫一次 AmountRank + 兩次 ChangePercentRank + 一次高價 snapshots，
    更新 top200 廣度指標、漲跌停統計（全市場 / 成交值前200 / 高價前100）、樣本漲跌家數。
    由排程每 30 秒呼叫一次。

    change_type（scanner .dict() 給 int）：1=漲停 2=漲 3=平 4=跌 5=跌停
    """
    api = get_api()

    try:
        # ── 成交值前 200（模組二 + 模組四成交值榜漲跌停）──
        top200 = api.scanners(
            scanner_type=sj.ScannerType.AmountRank,
            count=SCANNER_COUNT,
            ascending=True,   # 金額由大到小
        )
        df_top = pd.DataFrame([s.dict() for s in top200])
        MARKET_STATE.top200_df = df_top
        MARKET_STATE.top200_limit_up   = int((df_top["change_type"] == 1).sum())
        MARKET_STATE.top200_limit_down = int((df_top["change_type"] == 5).sum())

        # ── 漲幅榜（模組四漲停 + 樣本漲家數）─────────────
        gainers = api.scanners(
            scanner_type=sj.ScannerType.ChangePercentRank,
            count=SCANNER_COUNT,
            ascending=True,   # 漲幅由大到小
        )
        df_g = pd.DataFrame([s.dict() for s in gainers])
        lu = df_g[df_g["change_type"] == 1]  # change_type 1 = 漲停
        MARKET_STATE.limit_up_low  = int((lu["close"] < LIMIT_PRICE_THRESHOLD).sum())
        MARKET_STATE.limit_up_high = int((lu["close"] >= LIMIT_PRICE_THRESHOLD).sum())

        # ── 跌幅榜（模組四跌停 + 樣本跌家數）─────────────
        losers = api.scanners(
            scanner_type=sj.ScannerType.ChangePercentRank,
            count=SCANNER_COUNT,
            ascending=False,  # 跌幅由大到小
        )
        df_l = pd.DataFrame([s.dict() for s in losers])
        ld = df_l[df_l["change_type"] == 5]  # change_type 5 = 跌停
        MARKET_STATE.limit_down_low  = int((ld["close"] < LIMIT_PRICE_THRESHOLD).sum())
        MARKET_STATE.limit_down_high = int((ld["close"] >= LIMIT_PRICE_THRESHOLD).sum())

        # ── 高價前 100（snapshots 統計漲跌停）────────────
        MARKET_STATE.highprice_limit_up, MARKET_STATE.highprice_limit_down = \
            _count_highprice_limits()

        MARKET_STATE.last_scanner_update = dt.datetime.now()
        print(f"[scanner] 更新完成 {MARKET_STATE.last_scanner_update:%H:%M:%S}")

    except Exception as e:
        print(f"[scanner] 更新失敗: {e}")
