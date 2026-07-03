import shioaji as sj
from shioaji import TickSTKv1, Exchange
from datetime import datetime
from store.memory_store import STOCK_STORE, MARKET_STATE
from config import WATCHLIST, TAIEX_CODE, OTC_CODE
from data.session import get_api


def setup_callbacks() -> None:
    """設定 tick callback（必須在 subscribe 前呼叫，且全程只註冊這一個）"""
    api = get_api()

    @api.on_tick_stk_v1()
    def on_tick(exchange: Exchange, tick: TickSTKv1) -> None:
        """
        持股 + 指數共用 callback — 只寫入 store，不做計算。
        加權 code="001"(TSE) / 櫃買 code="101"(OTC)，用 code + exchange 雙重判斷分流。
        """
        code = tick.code

        # ── 指數分流（加權 TSE / 櫃買 OTC）────────────
        if code == TAIEX_CODE and exchange == Exchange.TSE:
            MARKET_STATE.taiex_close = float(tick.close)
            return
        if code == OTC_CODE and exchange == Exchange.OTC:
            MARKET_STATE.otc_close = float(tick.close)
            return

        # ── 持股 ───────────────────────────────────────
        if code in STOCK_STORE:
            state = STOCK_STORE[code]
            state.today_total_vol = tick.total_volume   # 今日累積量（張）
            state.latest_close    = float(tick.close)
            state.chg_type        = tick.chg_type
            state.last_tick_time  = datetime.now()


def subscribe_all() -> None:
    """訂閱持股 + 加權指數 + 櫃買指數"""
    api = get_api()

    # 持股
    for code in WATCHLIST:
        contract = api.Contracts.Stocks[code]
        api.subscribe(contract, quote_type=sj.QuoteType.Tick)
        print(f"[subscriber] 訂閱 {code}")

    # 加權指數（TSE, code=001）
    api.subscribe(
        api.Contracts.Indexs.TSE[TAIEX_CODE],
        quote_type=sj.QuoteType.Tick,
    )
    # 櫃買指數（OTC, code=101）
    api.subscribe(
        api.Contracts.Indexs.OTC[OTC_CODE],
        quote_type=sj.QuoteType.Tick,
    )
    print("[subscriber] 訂閱完成")


def unsubscribe_all() -> None:
    """收盤後取消所有訂閱"""
    api = get_api()
    for code in WATCHLIST:
        contract = api.Contracts.Stocks[code]
        api.unsubscribe(contract, quote_type=sj.QuoteType.Tick)
    api.unsubscribe(
        api.Contracts.Indexs.TSE[TAIEX_CODE],
        quote_type=sj.QuoteType.Tick,
    )
    api.unsubscribe(
        api.Contracts.Indexs.OTC[OTC_CODE],
        quote_type=sj.QuoteType.Tick,
    )
    print("[subscriber] 已取消所有訂閱")
