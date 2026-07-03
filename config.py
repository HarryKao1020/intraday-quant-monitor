from datetime import time

# ── 持股監控清單 ──────────────────────────────────────────
WATCHLIST: dict[str, str] = {
    "2330": "台積電",
    "2454": "聯發科",
    "2317": "鴻海",
}

# ── 爆量預估參數（模組一）────────────────────────────────
# 動態推估法：盤前用過去 N 日分鐘 K 建立「歷史累積成交比例曲線」，
# 盤中以「當下累計量 ÷ 當下時間的歷史累積比例」推估當日全量，
# 再比對五日均量判定爆量／量縮。
HIST_WEIGHT_DAYS   = 20      # 建立分鐘累積比例曲線用的歷史天數
VOLUME_SURGE_RATIO = 1.5     # 預估量 / 五日均量 > 此值 → 爆量
VOLUME_SHRINK_RATIO = 1.0    # 預估量 / 五日均量 < 此值 → 量縮

MORNING_START = time(9, 0)     # （保留）同時段均量起點
MORNING_END   = time(10, 30)   # （保留）同時段均量終點

# ── MACD 參數（模組三）───────────────────────────────────
MACD_FAST   = 12
MACD_SLOW   = 26
MACD_SIGNAL = 9
MACD_MIN_BARS = 40

# ── 乖離率均線天數（模組五）──────────────────────────────
BIAS_PERIODS = (5, 10, 20)   # 股價對 5 / 10 / 20 日均線乖離率
MA_BIAS = (20, 60)           # 均線對均線乖離：月線(20MA) 對 季線(60MA)

# ── 漲跌停價格分界（模組四）──────────────────────────────
LIMIT_PRICE_THRESHOLD = 50.0
HIGH_PRICE_COUNT = 100   # 高價排行 universe：全市場前一日收盤價最高的前 N 檔

# ── 市場廣度 Scanner 設定（模組二、四）──────────────────
SCANNER_COUNT = 200
SCANNER_INTERVAL_SEC = 60   # 資料更新頻率（每分鐘一次，與畫面重繪一致）

# ── 盤中自動同步期貨部位 ─────────────────────────────────
HOLDINGS_SYNC_SEC = 60   # 每 N 秒重讀部位（常駐連線，含庫存帳務；僅交易時段）

# ── 排程時間 ──────────────────────────────────────────────
PREFETCH_TIME  = time(8, 45)
MARKET_OPEN    = time(9, 0)
MARKET_CLOSE   = time(13, 30)

# ── 指數合約代碼 ──────────────────────────────────────────
# 加權與櫃買的 code 都是 "001"，必須靠 exchange（TSE / OTC）區分
TAIEX_CODE = "001"               # api.Contracts.Indexs.TSE["001"] = 加權指數
OTC_CODE   = "101"               # api.Contracts.Indexs.OTC["101"] = 櫃買指數
