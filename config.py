from datetime import date, time

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
MACD_MIN_BARS = 40   # 日 / 週 MACD 共用的最少 K 棒根數

# 盤前 kbars 抓取的日曆天數。週 MACD 需 ≥MACD_MIN_BARS 根週 K（約 40 週），
# 320 天約 45 週，留有假日緩衝；拉長會等比增加盤前預取時間。
KBAR_FETCH_DAYS = 320

# ── 乖離率均線天數（模組五）──────────────────────────────
BIAS_PERIODS = (5, 10, 20)   # 股價對 5 / 10 / 20 日均線乖離率
MA_BIAS = (20, 60)           # 均線對均線乖離：月線(20MA) 對 季線(60MA)

# ── 均線上家數均線（模組十一）────────────────────────────
# 成交值前 200 檔中，現價站上各均線的家數。(天數, 顯示名稱)
MA_BREAK_PERIODS = ((20, "月線"), (60, "季線"), (120, "半年線"))
# 每檔要保留的過去日收根數（≥ 最長均線；含昨日，不含今日）
CLOSE_TAIL_LEN = max(n for n, _ in MA_BREAK_PERIODS)

# ── 漲跌停價格分界（模組四）──────────────────────────────
LIMIT_PRICE_THRESHOLD = 50.0
HIGH_PRICE_COUNT = 100   # 高價排行 universe：全市場前一日收盤價最高的前 N 檔

# ── 市場廣度 Scanner 設定（模組二、四）──────────────────
SCANNER_COUNT = 200
SCANNER_INTERVAL_SEC = 60   # 資料更新頻率（每分鐘一次，與畫面重繪一致）

# ── 盤中自動同步期貨部位 ─────────────────────────────────
HOLDINGS_SYNC_SEC = 60   # 每 N 秒重讀部位（常駐連線，含庫存帳務；僅交易時段）

# ── 淨值績效曲線（模組十二）──────────────────────────────
PERF_START_DATE  = date(2026, 1, 1)   # 績效起算日（基準 = 前一交易日收盤）
PERF_INITIAL_CAPITAL = 3_500_000      # 年初本金；淨值 = 本金 + 累計損益（不採權益數，出金不影響）
PERF_REFRESH_SEC = 300                # 盤中「今日點」輕量更新頻率
PERF_SNAPSHOT_TIME = time(14, 40)     # 收盤後快照真實淨值 + 重建曲線

# ── 排程時間 ──────────────────────────────────────────────
PREFETCH_TIME  = time(8, 45)
MARKET_OPEN    = time(9, 0)
MARKET_CLOSE   = time(13, 30)

# ── 指數合約代碼 ──────────────────────────────────────────
# 加權與櫃買的 code 都是 "001"，必須靠 exchange（TSE / OTC）區分
TAIEX_CODE = "001"               # api.Contracts.Indexs.TSE["001"] = 加權指數
OTC_CODE   = "101"               # api.Contracts.Indexs.OTC["101"] = 櫃買指數
