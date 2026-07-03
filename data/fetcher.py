import pandas as pd
from datetime import date, time, timedelta
from store.memory_store import STOCK_STORE, StockState, MARKET_STATE
from compute.macd import calc_macd
from config import (
    WATCHLIST,
    MACD_FAST, MACD_SLOW, MACD_SIGNAL, MACD_MIN_BARS,
    MORNING_START, MORNING_END, HIST_WEIGHT_DAYS,
    TAIEX_CODE, OTC_CODE,
)
from data.session import get_api


def _fetch_kbars_raw(code: str, days: int = 80) -> pd.DataFrame:
    """
    抓近 N 日分鐘 K（kbars 每段上限 30 天，分多段抓再合併）。
    回傳原始分鐘 K DataFrame（含 ts, date, time 欄位）。
    """
    api = get_api()
    contract = api.Contracts.Stocks[code]
    today = date.today()

    frames = []
    seg_start = days
    while seg_start > 0:
        seg_end = max(seg_start - 29, 1)   # 每段最多 30 天（含頭尾）
        kb = api.kbars(
            contract=contract,
            start=(today - timedelta(days=seg_start)).strftime("%Y-%m-%d"),
            end=(today - timedelta(days=seg_end)).strftime("%Y-%m-%d"),
            timeout=60_000,   # 盤中 80 天分鐘 K 較慢，預設 5s 會逾時
        )
        frames.append(pd.DataFrame(kb.dict()))
        seg_start = seg_end - 1

    df = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["ts"])
    df["ts"] = pd.to_datetime(df["ts"])
    df["date"] = df["ts"].dt.date
    df["time"] = df["ts"].dt.time
    return df.sort_values("ts").reset_index(drop=True)


def _build_daily(df: pd.DataFrame) -> pd.DataFrame:
    """從分鐘 K 重建日線 OHLCV。"""
    return df.groupby("date").agg(
        Open=("Open", "first"),
        High=("High", "max"),
        Low=("Low", "min"),
        Close=("Close", "last"),
        Volume=("Volume", "sum"),
        Amount=("Amount", "sum"),
    ).reset_index()


def _build_cum_ratio_curve(raw_df: pd.DataFrame, today, n_days: int) -> list:
    """
    用過去 n_days 個交易日的分鐘 K，建立「歷史累積成交比例曲線」。
    回傳 [(當日分鐘數, 平均累積比例), ...]（升冪）；
    當日分鐘數 = 該分鐘 K 收盤時間的 hour*60+minute。

    每一天：各分鐘累計量 ÷ 當日總量 → 累積比例；再對齊分鐘網格、
    以最近值前補後，跨日取平均。曲線末端 ≈ 1.0。
    """
    past = raw_df[raw_df["date"] < today]
    days = sorted(past["date"].unique())[-n_days:]
    sub = past[past["date"].isin(days)].copy()
    if sub.empty:
        return []
    sub["mod"] = sub["time"].map(lambda t: t.hour * 60 + t.minute)

    curves = []
    for _, g in sub.groupby("date"):
        total = g["Volume"].sum()
        if total <= 0:
            continue
        cr = g.groupby("mod")["Volume"].sum().sort_index().cumsum() / total
        curves.append(cr)
    if not curves:
        return []

    grid = sorted(set().union(*[c.index for c in curves]))
    aligned = [c.reindex(grid).ffill().fillna(0.0) for c in curves]
    avg = pd.concat(aligned, axis=1).mean(axis=1)
    return [(int(m), float(r)) for m, r in avg.items()]


def _fetch_index_history(days: int = 100) -> None:
    """
    抓加權 / 櫃買指數的過去日收（升冪）與昨收，寫入 MARKET_STATE。
    昨收供盤中算漲跌幅（模組二）；日收序列供盤中即時算 MACD（模組三）。
    kbars 單次上限 30 天，分段抓再合併。
    """
    api = get_api()
    today = date.today()
    for code, exch, prev_attr, series_attr, vol_key in [
        (TAIEX_CODE, "TSE", "taiex_prev_close", "taiex_daily_closes", "TAIEX"),
        (OTC_CODE,   "OTC", "otc_prev_close",   "otc_daily_closes",   "OTC"),
    ]:
        try:
            contract = getattr(api.Contracts.Indexs, exch)[code]
            frames = []
            seg_start = days
            while seg_start > 0:
                seg_end = max(seg_start - 29, 1)
                kb = api.kbars(
                    contract=contract,
                    start=(today - timedelta(days=seg_start)).strftime("%Y-%m-%d"),
                    end=(today - timedelta(days=seg_end)).strftime("%Y-%m-%d"),
                    timeout=60_000,   # 盤中較慢，避免預設 5s 逾時
                )
                frames.append(pd.DataFrame(kb.dict()))
                seg_start = seg_end - 1
            idx_df = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["ts"])
            idx_df["ts"] = pd.to_datetime(idx_df["ts"])
            idx_df["date"] = idx_df["ts"].dt.date
            idx_df["time"] = idx_df["ts"].dt.time
            daily = idx_df.groupby("date")["Close"].last().sort_index()
            past = daily[daily.index < today]
            if not past.empty:
                setattr(MARKET_STATE, prev_attr, float(past.iloc[-1]))
                setattr(MARKET_STATE, series_attr, [float(x) for x in past.tolist()])

            # ── 模組一：指數量能（成交金額，元）──────────────────
            # 5日均額 + 累積比例曲線都用 Amount（看盤慣例：大盤量能=成交金額）
            amt_by_day = idx_df[idx_df["date"] < today].groupby("date")["Amount"] \
                               .sum().sort_index(ascending=False)
            entry = MARKET_STATE.index_vol.setdefault(vol_key, {})
            if len(amt_by_day) >= 5:
                entry["v5d"] = float(amt_by_day.head(5).mean())
            idx_amt = idx_df.assign(Volume=idx_df["Amount"])   # 曲線改以金額計
            entry["curve"] = _build_cum_ratio_curve(idx_amt, today, HIST_WEIGHT_DAYS)
            # 本週/上週指數成交金額（模組十佔比分母用）
            monday = today - timedelta(days=today.weekday())
            wk = idx_df[(idx_df["date"] >= monday) & (idx_df["date"] < today)]
            entry["week_amt_past"] = float(wk["Amount"].sum())
            lw = idx_df[(idx_df["date"] >= monday - timedelta(days=7)) &
                        (idx_df["date"] < monday)]
            entry["lastweek_amt"] = float(lw["Amount"].sum())
        except Exception as e:
            print(f"[fetcher] 指數 {exch}{code} 歷史抓取失敗: {e}")


def prefetch_all(watchlist: dict[str, str] | None = None, fetch_index: bool = True) -> None:
    """
    盤前（08:45）呼叫：抓所有持股歷史資料，計算均量與 MACD，寫入 STOCK_STORE。
    並抓指數昨收寫入 MARKET_STATE。

    watchlist：要預取的個股 {code: name}。預設用 config.WATCHLIST；
    模組一會傳入由股期部位推導出的清單。
    fetch_index：是否一併抓指數歷史。盤中增量同步新股期時傳 False（指數已有）。
    """
    today = date.today()
    wl = watchlist if watchlist is not None else WATCHLIST

    for code, name in wl.items():
        print(f"[fetcher] 抓取 {code} {name}...")

        if code not in STOCK_STORE:
            STOCK_STORE[code] = StockState(symbol=code, name=name)
        state = STOCK_STORE[code]

        try:
            raw_df = _fetch_kbars_raw(code, days=100)   # 需 ≥60 交易日供 60MA 乖離
        except Exception as e:
            print(f"[fetcher] {code} kbars 失敗: {e}")
            continue

        daily_df = _build_daily(raw_df)
        past_daily = daily_df[daily_df["date"] < today].sort_values("date", ascending=False)

        # ── 前五日全日均量 ───────────────────────────────
        if len(past_daily) >= 5:
            state.v5d_avg_vol = float(past_daily.head(5)["Volume"].mean())

        # ── 前五日同時段（09:00~10:30）均量 ──────────────
        morning = raw_df[
            (raw_df["date"] < today) &
            (raw_df["time"] >= MORNING_START) &
            (raw_df["time"] <= MORNING_END)
        ]
        morning_by_day = morning.groupby("date")["Volume"].sum().sort_index(ascending=False)
        if len(morning_by_day) >= 5:
            state.v5d_morning_avg_vol = float(morning_by_day.head(5).mean())

        # ── 歷史累積成交比例曲線（動態預估用）────────────
        state.cum_ratio_curve = _build_cum_ratio_curve(raw_df, today, HIST_WEIGHT_DAYS)

        # ── MACD（日線 Close）────────────────────────────
        # past_daily 是降冪，calc_macd / 日收序列需要時間升冪
        close_asc = past_daily.sort_values("date")["Close"].reset_index(drop=True)
        state.daily_closes = [float(x) for x in close_asc.tolist()]   # 供盤中即時算 MACD
        if len(past_daily) >= MACD_MIN_BARS:
            macd_df = calc_macd(close_asc, fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL)
            state.histogram      = float(macd_df["histogram"].iloc[-1])
            state.prev_histogram = float(macd_df["histogram"].iloc[-2])
            state.dif            = float(macd_df["dif"].iloc[-1])
            state.macd_signal    = float(macd_df["macd_signal"].iloc[-1])
        else:
            print(f"[fetcher] {code} 日線根數不足（{len(past_daily)}），跳過 MACD")

    # ── 指數昨收 + MACD 日收序列 ─────────────────────────
    if fetch_index:
        _fetch_index_history()

    print("[fetcher] 盤前資料預取完成")
