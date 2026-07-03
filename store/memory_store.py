from dataclasses import dataclass, field
from datetime import datetime
import pandas as pd


@dataclass
class StockState:
    """單一持股的盤中狀態"""
    symbol: str
    name: str = ""

    # 模組一：爆量預估
    today_total_vol: int = 0
    v5d_avg_vol: float = 0.0
    v5d_morning_avg_vol: float = 0.0
    # 歷史累積成交比例曲線：[(當日分鐘數, 平均累積比例), ...]（升冪，盤前建立）
    cum_ratio_curve: list = field(default_factory=list)

    # 模組三：MACD（dif 等為盤前快照；daily_closes 供盤中即時重算）
    dif: float = 0.0
    macd_signal: float = 0.0
    histogram: float = 0.0
    prev_histogram: float = 0.0
    daily_closes: list = field(default_factory=list)   # 過去完整交易日收盤（升冪）

    # 共用
    latest_close: float = 0.0
    change_rate: float = 0.0   # 今日漲跌幅 %（snapshot.change_rate）
    chg_type: int = 3
    last_tick_time: datetime = field(default_factory=datetime.now)


@dataclass
class MarketState:
    """全市場盤中狀態"""
    # 模組二：市場廣度（由 scanner 更新）
    top200_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    taiex_close: float = 0.0
    taiex_prev_close: float = 0.0   # 昨收（盤前 kbars 取）
    otc_close: float = 0.0
    otc_prev_close: float = 0.0
    # 模組三：指數 MACD 用的過去日收（升冪，盤前抓）
    taiex_daily_closes: list = field(default_factory=list)
    otc_daily_closes: list = field(default_factory=list)

    # 模組一：指數量能 {"TAIEX"/"OTC": {v5d, curve, today_vol, change_rate}}
    index_vol: dict = field(default_factory=dict)
    # 樣本漲跌平家數（已停用，保留欄位）
    sample_up: int = 0
    sample_down: int = 0
    sample_flat: int = 0

    # 模組六：全市場 52 週高點表 {code: [high, 高點日期ISO]} + 年內交易日列表
    high52w: dict = field(default_factory=dict)
    high52w_dates: list = field(default_factory=list)
    high52w_ready: bool = False

    # 模組七：期貨庫存帳務（讀部位時一併更新，180 秒同步）
    fut_positions: list = field(default_factory=list)   # [{name,qty,avg,last,exposure,pnl},...]
    last_positions_update: datetime = field(default_factory=datetime.now)

    # 模組八/九：處置與注意股（啟動時抓，公告類一日一更）
    punish_list: list = field(default_factory=list)   # 生效中處置 [{code,start,end,interval}]
    reg_stats: dict = field(default_factory=dict)     # {"punish":{today,yesterday,avg5},"notice":{...}}

    # 模組十：族群金流（60 秒更新；day=當日、week=本週累計口徑）
    industry_rows: list = field(default_factory=list)  # [{group,avg_chg,amount,n,n_total}]
    industry_detail: dict = field(default_factory=dict) # {group: [{code,name,close,chg,amount}]}
    industry_rows_week: list = field(default_factory=list)
    industry_detail_week: dict = field(default_factory=dict)
    industry_rows_lastweek: list = field(default_factory=list)   # 上週（靜態）
    industry_detail_lastweek: dict = field(default_factory=dict)
    last_industry_update: datetime = field(default_factory=datetime.now)

    # 模組四：漲跌停統計
    limit_up_low: int = 0
    limit_up_high: int = 0
    limit_down_low: int = 0
    limit_down_high: int = 0

    # 模組四擴充：不同 universe 的漲跌停家數
    top200_limit_up: int = 0       # 成交值前 200 檔內的漲停家數
    top200_limit_down: int = 0     # 成交值前 200 檔內的跌停家數
    highprice_limit_up: int = 0    # 高價前 100 檔內的漲停家數
    highprice_limit_down: int = 0  # 高價前 100 檔內的跌停家數
    high_price_codes: list = field(default_factory=list)  # 高價 universe（盤前建立、整日固定）

    last_scanner_update: datetime = field(default_factory=datetime.now)

    # 流量用量（api.usage）— 監控當日 API 流量是否逼近上限
    usage_bytes: int = 0            # 今日已用 bytes
    usage_limit_bytes: int = 0      # 今日上限 bytes（依交易量 500MB~10GB）
    usage_remaining_bytes: int = 0  # 今日剩餘 bytes
    usage_connections: int = 0      # 目前連線數（上限 5）
    last_usage_update: datetime = field(default_factory=datetime.now)


# ── 全域單例 ─────────────────────────────────────────────
STOCK_STORE: dict[str, StockState] = {}
MARKET_STATE = MarketState()
