# 盤中量化觀察系統 — 需求規格與實作指引

> **專案名稱**：`intraday-futures-monitor`
> **版本**：v4.1
> **更新日期**：2026-06-26
> **資料來源**：Shioaji（永豐金）API `>=1.2.5`
> **儲存策略**：全記憶體（in-memory），盤中即用即棄
> **Python**：3.10+
> **排程機制**：`APScheduler` (BackgroundScheduler)
> **前端呈現**：Dash（本地網頁，`dcc.Interval` 定時拉取）

---

## 實作調整摘要（2026-06-30 更新，相對原規格）

以下為實際實作時相對原始規格的調整，原規格各章節之程式碼僅作設計參考：

- **資料更新**：以 30 秒排程（`_refresh_market`）統一更新 scanner、指數現價、持股量能、流量；Dash `dcc.Interval` 每 1 秒重繪。
- **模組一（爆量）監控對象改為「股期標的」**：開盤以**正式環境唯讀**讀取期貨部位（`data/holdings.py::get_stock_futures_watchlist`，獨立連線、查完登出），取合約代碼**前三碼**對 `stock_fut_map.json` 抓標的個股，作為動態 watchlist（取代靜態 `config.WATCHLIST`）。
- **盤中自動同步部位**：`sync_holdings()` 每 `HOLDINGS_SYNC_SEC`(180s) 於交易時段（08:45–13:35）重讀部位，新買股期自動預取加入（`prefetch_all(new, fetch_index=False)`）、平倉自動移除，免重啟。`STOCK_STORE` 由背景執行緒增減，故讀取端（volume_surge / macd / bias）皆以 `list(STOCK_STORE.items())` 快照迭代。今日量能改用 `snapshots` 每 30 秒輪詢 `total_volume`（免 streaming）。其餘行情仍走 simulation session。
  - **動態預估法**：盤前用過去 `HIST_WEIGHT_DAYS`(20) 天分鐘 K（重用既有 `raw_df`，免加 API）建立「歷史累積成交比例曲線」`cum_ratio_curve = [(當日分鐘數, 平均累積比例)]`（單調遞增、末端≈1.0，存於 `StockState`）。
  - **盤中欄位**：`5日均`(全日五日均量，昨天當第一天 rolling 5 交易日)、`現在量`(即時累計)、`預估今量`＝即時累計 ÷ 當下時間 T 的歷史累積比例、`預估比`＝預估今量 ÷ 五日均量。
  - **判定**：`預估比 > VOLUME_SURGE_RATIO`(1.5) → 爆量；`< VOLUME_SHRINK_RATIO`(1.0) → 量縮；盤前無量顯示「—」。此法時間無關（早盤晚盤判斷一致）。
  - **模組更名「量能監控」**，並比照 MOD.03 於最上方加入**加權/櫃買指數**列（分隔線下為股期標的）：指數 5 日均量與累積比例曲線在 `_fetch_index_history` 重用同一份分鐘 K 計算（存 `MARKET_STATE.index_vol`），當日累計量/漲跌幅由 `update_index_quotes()` 的 snapshot 取得（已驗證指數 kbars Volume 與 snapshot.total_volume 口徑一致，比值 1.000）。
  - `snapshot.total_volume` 與 `kbars` 同為普通交易盤口徑（分子分母一致；皆不含盤後定價／鉅額，故較 TWSE 官方日量低 5~25%）。
- **模組四（漲跌停）擴充三種口徑**：全市場（漲/跌幅榜各前 200）、**成交值前 200 檔**、**高價前 100 檔**（盤前以 `daily_quotes` 取前一交易日收盤價最高 100 檔，盤中以 `snapshots` 統計漲跌停）。
- **模組二（廣度）指數現價**：以 `update_index_quotes()` 用 `snapshots` 每 30 秒輪詢加權/櫃買現價（取代 tick 訂閱）。**更名為「成值前200超額報酬」**：移除意義不大的「樣本漲跌家數」（漲/跌幅榜各前200去重，永遠≈200/200），新增 `excess_vs_taiex` ＝前200成交值加權漲跌幅 − 加權指數漲跌幅（主流股相對大盤超額）。
- **模組三（MACD）**：擴充加權指數、櫃買指數；改為盤中即時重算；新增「柱狀體前一日狀態比較」欄（見第 11 節）。
- **新增模組五（乖離率）**：`compute/bias.py::get_bias_display`，對**加權指數、櫃買指數 + 持股個股**顯示兩類乖離：
  - **股價對均線乖離**（`BIAS_PERIODS = 5/10/20`）＝ (現價 − N日均) / N日均 × 100%
  - **月季線乖離**（`MA_BIAS = (20, 60)`）＝ (20MA − 60MA) / 60MA × 100%，即月線對季線乖離
  - 均線用「過去日收 + 今日現價」（含今日 SMA），重用 `daily_closes`/指數 `*_daily_closes` + 即時價，免加 API。為算 60MA，個股 kbars 抓取增為 100 天（≈68 交易日）、指數 `_fetch_index_history` 增為 100 天。正乖離紅、負乖離綠；儀表板置於 2×2 網格下方整列（指數在上、分隔線、個股在下）。
- **新增 API 流量監控**：`api.usage()` → `compute/usage_stats.py`，顯示已用/上限/剩餘/連線數，🟢<80% 🟡≥80% 🔴≥95%。
- **儀表板（第 13 節）改為深色網頁風**：文字以白/灰為主，紅綠僅用於漲跌幅、量比、MACD 柱狀體；版面為 **2×2 網格**（第一列 MOD.01｜MOD.03，第二列 MOD.02｜MOD.04，流量整列於上）；移除 CRT 特效。
- **新增模組六（成值前200創年新高）**：`data/high52w.py` 背景以獨立 sim session 逐日 `daily_quotes` 建全市場「前一年最高 High」表（[今天-365, 昨天]，約 250 次、1~2 分鐘），每日快取 `cache/high52w_<date>.json`（當日重啟秒載入）。`compute/new_high.py::get_new_highs` 取成交值前 200（`top200_df`）逐檔比對「今日 high ≥ 前一年最高」→ 創年新高；`幅度`＝收盤 vs 前一年高（正＝站穩、負＝盤中摸高收黑）。置於右列（大盤盤況）。
- **新增模組七（庫存帳務）**：讀部位時（啟動 + 盤中每 180s 同步）由 `data/holdings.py::_collect_positions` 一併整理期貨部位明細寫入 `MARKET_STATE.fut_positions`。欄位：股票期貨名稱、口數（賣方為負）、庫存均價、現價（`pos.last_price`）、曝險金額（現價×口數×乘數）、損益（`pos.pnl`）。乘數判斷沿用 `shioaji_account.py::get_contract_info`（大台200/小台50/微台10/小型股期100股/一般股期2000股/選擇權50）。含合計列；置於左列 MOD.05 下方。
- **新增模組八/九（處置與注意股）**：`data/regulatory.py::update_regulatory`（啟動時抓一次，公告類一日一更）。
  - **MOD.09 庫存處置清單**（左列）：持股標的（股期對應現股）比對 `api.punish()` 生效中名單，欄位：代號、股名、處置起迄日、撮合時間；無命中顯示「安全」。
  - **MOD.08 處置/注意股數量**（右列）：處置股與注意股各列 今日/昨日/近五日均。punish 只回「生效中」名單 → 過去日以 start/end 區間回推（近似）；notice 只回最新一天 → 每日將數量落地 `cache/regulatory_history.json` 累積，未累積的天數顯示「—」。
- **新增模組十（族群金流監控）**：`data/industry.py` 讀 `data/Industry.json`（25 族群、~222 檔，`{族群: [{company_name, stock_code}]}`），每 30 秒一次 `snapshots` 抓全部成分股（<500 檔上限），聚合每族群：平均漲跌幅（成分股 change_rate 平均）、成交金額總和（total_amount）、佔比＝族群總額 ÷（加權+櫃買 total_amount）×100%。依平均漲跌幅降冪排序；不存在的代碼（如 2664）自動略過。置於右列 MOD.06 下方。**當日/本週/上週切換**（`flow-radio` → `flow-period` Store，切換即重繪，選項白字）：本週＝累計至現在（週漲跌幅=現價 vs 上週收；金額=本週已完成日 `daily_quotes` 累計＋今日盤中 snapshot）；上週＝整週固定值（週漲跌幅=上週收 vs 上上週收；金額=上週五日累計），於 `build_week_baseline()` 啟動時一次算好存 `industry_rows_lastweek`；佔比分母對應指數同口徑金額（`week_amt_past` / `lastweek_amt`）。modal 欄位隨口徑切換（上週顯示上週收盤/週漲跌幅/週成交金額）。**Plotly 圓餅圖**：切片=各族群成交金額，加灰色「其他(未分類)」補到全市場 100%（全市場=加權+櫃買 total_amount；其他負值防呆歸零）；小切片自動隱字（uniformtext hide），hover 看明細；表格保留於下方。**族群列與圓餅切片皆可點擊**：pattern-matching callback（`{"type":"grp-row","index":族群}`）與 `grp-pie.clickData` 開啟自製 modal（點「其他」不開），顯示成分股明細（代號/股名/現價/漲跌幅/成交金額，依成交金額降冪）；`grp-pie` 為動態元件需 `suppress_callback_exceptions=True`；表格重繪會以 n_clicks=0 觸發 callback；**注意 Dash 4.x 的 pattern 輸入 `ctx.triggered[...]["value"]` 恆為 None**，須改從 `ctx.inputs_list` 對出被點列的實際 n_clicks（>0 才是真點擊）。
- **更新頻率**：資料排程與畫面重繪（`dcc.Interval`）皆為每 60 秒一次（`SCANNER_INTERVAL_SEC = 60`）。
- **執行環境**：以 miniforge `finlab` conda 環境（Python 3.14、shioaji 1.5.4）執行；啟動入口為 `output/dash_app.py` 的 `run()`。

---

## 目錄

1. [專案結構](#1-專案結構)
2. [環境設定](#2-環境設定)
3. [config.py 定義](#3-configpy-定義)
4. [store/memory_store.py](#4-storememory_storepy)
5. [data/session.py](#5-datasessionpy)
6. [data/fetcher.py — 盤前歷史資料](#6-datafetcherpy--盤前歷史資料)
7. [data/subscriber.py — 即時 tick 訂閱](#7-datasubscriberpy--即時-tick-訂閱)
8. [data/scanner.py — 定時排行快照](#8-datascannerpy--定時排行快照)
9. [compute/volume_surge.py — 模組一爆量預估](#9-computevolume_surgepy--模組一爆量預估)
10. [compute/market_breadth.py — 模組二市場廣度](#10-computemarket_breadthpy--模組二市場廣度)
11. [compute/macd.py — 模組三持股 MACD](#11-computemacdpy--模組三持股-macd)
12. [compute/limit_stats.py — 模組四漲跌停統計](#12-computelimit_statspy--模組四漲跌停統計)
13. [output/dash_app.py — 本地網頁儀表板](#13-outputdash_apppy--本地網頁儀表板)
14. [main.py — 進入點與排程](#14-mainpy--進入點與排程)
15. [Shioaji API 速查與限制](#15-shioaji-api-速查與限制)
16. [Claude Code 啟動指引](#16-claude-code-啟動指引)

---

## 1. 專案結構

```
intraday-futures-monitor/
├── .env                     # API 金鑰（不 commit）
├── .env.example             # 範本
├── main.py                  # 進入點：初始化、APScheduler、啟動 Dash
├── config.py                # 持股清單、所有參數常數
│
├── store/
│   └── memory_store.py      # 全域 in-memory 狀態（dataclass）
│
├── data/
│   ├── session.py           # Shioaji 登入/登出
│   ├── fetcher.py           # 盤前批量抓取（kbars → 日線重建）
│   ├── subscriber.py        # 即時 tick callback 訂閱
│   └── scanner.py           # 定時 api.scanners() 呼叫
│
├── compute/
│   ├── volume_surge.py      # 模組一：爆量預估
│   ├── market_breadth.py    # 模組二：市場廣度
│   ├── macd.py              # 模組三：MACD 計算
│   └── limit_stats.py       # 模組四：漲跌停統計
│
├── output/
│   └── dash_app.py          # Dash 本地網頁儀表板
│
├── requirements.txt
└── README.md
```

---

## 2. 環境設定

### `.env.example`

```dotenv
SJ_API_KEY=your_api_key_here
SJ_SECRET_KEY=your_secret_key_here
SJ_SIMULATION=true         # 開發/測試用 true，正式盤用 false
```

### `requirements.txt`

```
shioaji>=1.2.5
pandas>=2.0
python-dotenv>=1.0
apscheduler>=3.10
dash>=2.17
dash-bootstrap-components>=1.6
```

---

## 3. `config.py` 定義

Claude Code 必須完整實作此檔，所有常數都在這裡，其他模組 import 使用，不得硬編碼。

```python
# config.py
from datetime import time

# ── 持股監控清單 ──────────────────────────────────────────
# 格式：{"代碼": "名稱"}
WATCHLIST: dict[str, str] = {
    "2330": "台積電",
    "2454": "聯發科",
    "2317": "鴻海",
    # 新增持股在此加入
}

# ── 爆量預估參數（模組一）────────────────────────────────
VOLUME_SURGE_THRESHOLD = 0.5     # 10:30 前超過前五日均量的比例 → 判定爆量
VOLUME_CHECK_TIME      = time(10, 30)   # 爆量判斷觸發時間
MORNING_START          = time(9, 0)     # 同時段均量起點
MORNING_END            = time(10, 30)   # 同時段均量終點
# 採用「同時段均量」做比較基準：今日 09:00~10:30 量 vs 前五日同段均量
# 比「全日均量」更公平，不受下午盤影響

# ── MACD 參數（模組三）───────────────────────────────────
MACD_FAST   = 12
MACD_SLOW   = 26
MACD_SIGNAL = 9
MACD_MIN_BARS = 40  # 日線最少需要幾根才計算 MACD

# ── 漲跌停價格分界（模組四）──────────────────────────────
LIMIT_PRICE_THRESHOLD = 50.0     # 低價 < 50 / 高價 >= 50

# ── 市場廣度 Scanner 設定（模組二、四）──────────────────
SCANNER_COUNT = 200              # api.scanners count 上限
SCANNER_INTERVAL_SEC = 30        # scanner 定時更新間隔（秒）

# ── 排程時間 ──────────────────────────────────────────────
PREFETCH_TIME  = time(8, 45)     # 盤前資料抓取時間
MARKET_OPEN    = time(9, 0)      # 開盤
MARKET_CLOSE   = time(13, 30)    # 收盤（取消訂閱）

# ── 指數合約代碼 ──────────────────────────────────────────
# 加權與櫃買的 code 都是 "001"，必須靠 exchange（TSE / OTC）區分
TAIEX_CODE = "001"               # api.Contracts.Indexs.TSE["001"] = 加權指數
OTC_CODE   = "001"               # api.Contracts.Indexs.OTC["001"] = 櫃買指數
```

---

## 4. `store/memory_store.py`

所有模組共用的全域狀態。**所有 write 操作只在 callback / scanner thread 發生，read 操作在 compute 模組發生。**

```python
# store/memory_store.py
from dataclasses import dataclass, field
from datetime import datetime
import pandas as pd


@dataclass
class StockState:
    """單一持股的盤中狀態"""
    symbol: str
    name: str = ""

    # 模組一：爆量預估
    today_total_vol: int = 0         # tick.total_volume（今日累積量，張）
    v5d_avg_vol: float = 0.0         # 前五日全日均量（張）
    v5d_morning_avg_vol: float = 0.0 # 前五日同時段（09:00~10:30）均量（張）

    # 模組三：MACD（盤前算好，dict 存最新值）
    dif: float = 0.0
    macd_signal: float = 0.0
    histogram: float = 0.0
    prev_histogram: float = 0.0

    # 共用
    latest_close: float = 0.0
    chg_type: int = 3                # 1=漲停 2=漲 3=平 4=跌 5=跌停
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
    # 樣本漲跌平家數（漲幅榜+跌幅榜各200去重後，非真正全市場）
    sample_up: int = 0
    sample_down: int = 0
    sample_flat: int = 0

    # 模組四：漲跌停統計（由 scanner 更新）
    limit_up_low: int = 0            # 漲停 且 close < 50
    limit_up_high: int = 0           # 漲停 且 close >= 50
    limit_down_low: int = 0          # 跌停 且 close < 50
    limit_down_high: int = 0         # 跌停 且 close >= 50

    last_scanner_update: datetime = field(default_factory=datetime.now)


# ── 全域單例 ─────────────────────────────────────────────
STOCK_STORE: dict[str, StockState] = {}   # key = 股票代碼
MARKET_STATE = MarketState()
```

---

## 5. `data/session.py`

```python
# data/session.py
import os
import shioaji as sj
from dotenv import load_dotenv

load_dotenv()

_api: sj.Shioaji | None = None


def get_api() -> sj.Shioaji:
    """取得已登入的 api 單例，未初始化則拋出 RuntimeError"""
    if _api is None:
        raise RuntimeError("API 尚未初始化，請先呼叫 init_api()")
    return _api


def init_api() -> sj.Shioaji:
    """登入並初始化 Shioaji API，回傳 api 物件"""
    global _api
    simulation = os.environ.get("SJ_SIMULATION", "true").lower() == "true"
    _api = sj.Shioaji(simulation=simulation)
    _api.login(
        api_key=os.environ["SJ_API_KEY"],
        secret_key=os.environ["SJ_SECRET_KEY"],
        contracts_timeout=10_000,   # 等商品檔下載最多 10 秒
    )
    print(f"[session] 登入成功 | simulation={simulation}")
    return _api


def logout_api() -> None:
    global _api
    if _api is not None:
        _api.logout()
        _api = None
        print("[session] 已登出")
```

---

## 6. `data/fetcher.py` — 盤前歷史資料

**職責**：開盤前（08:45）一次性呼叫，計算所有持股的前五日均量與 MACD 所需日線，寫入 `STOCK_STORE`。

### 重要限制
- `api.kbars` 每次查詢區間最多 30 天 → 抓 80 天需分多段（迴圈每段 ≤30 天）
- `api.kbars` 回傳的是**分鐘 K**，不是日 K → 需自行 groupby 重建日線
- 盤中查詢 `kbars` 不得超過 270 次（流量限制）
- 持股數 × 分段數 + 指數 2 檔，要控制在盤前一次抓完，避免盤中重複抓

> **注意**：MACD 需要 ≥40 根日線，而 80 個日曆日（含週末假日）才約有 52 個交易日，預留足夠 buffer。因此抓 80 天分三段（每段 ≤30 天）。

```python
# data/fetcher.py
import pandas as pd
from datetime import date, time, timedelta
from store.memory_store import STOCK_STORE, StockState, MARKET_STATE
from compute.macd import calc_macd
from config import (
    WATCHLIST,
    MACD_FAST, MACD_SLOW, MACD_SIGNAL, MACD_MIN_BARS,
    MORNING_START, MORNING_END,
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


def _fetch_index_prev_close() -> None:
    """抓加權 / 櫃買指數昨收，寫入 MARKET_STATE（供盤中算漲跌幅）。"""
    api = get_api()
    today = date.today()
    for code, exch, attr in [
        (TAIEX_CODE, "TSE", "taiex_prev_close"),
        (OTC_CODE,   "OTC", "otc_prev_close"),
    ]:
        try:
            contract = getattr(api.Contracts.Indexs, exch)[code]
            kb = api.kbars(
                contract=contract,
                start=(today - timedelta(days=10)).strftime("%Y-%m-%d"),
                end=today.strftime("%Y-%m-%d"),
            )
            idx_df = pd.DataFrame(kb.dict())
            idx_df["date"] = pd.to_datetime(idx_df["ts"]).dt.date
            daily = idx_df.groupby("date")["Close"].last().sort_index()
            past = daily[daily.index < today]
            if not past.empty:
                setattr(MARKET_STATE, attr, float(past.iloc[-1]))
        except Exception as e:
            print(f"[fetcher] 指數 {exch}{code} 昨收抓取失敗: {e}")


def prefetch_all() -> None:
    """
    盤前（08:45）呼叫：抓所有持股歷史資料，計算均量與 MACD，寫入 STOCK_STORE。
    並抓指數昨收寫入 MARKET_STATE。
    """
    today = date.today()

    for code, name in WATCHLIST.items():
        print(f"[fetcher] 抓取 {code} {name}...")

        if code not in STOCK_STORE:
            STOCK_STORE[code] = StockState(symbol=code, name=name)
        state = STOCK_STORE[code]

        try:
            raw_df = _fetch_kbars_raw(code, days=80)
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

        # ── MACD（日線 Close）────────────────────────────
        if len(past_daily) >= MACD_MIN_BARS:
            # past_daily 是降冪，calc_macd 需要時間升冪
            close_asc = past_daily.sort_values("date")["Close"].reset_index(drop=True)
            macd_df = calc_macd(close_asc, fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL)
            state.histogram      = float(macd_df["histogram"].iloc[-1])
            state.prev_histogram = float(macd_df["histogram"].iloc[-2])
            state.dif            = float(macd_df["dif"].iloc[-1])
            state.macd_signal    = float(macd_df["macd_signal"].iloc[-1])
        else:
            print(f"[fetcher] {code} 日線根數不足（{len(past_daily)}），跳過 MACD")

    # ── 指數昨收 ─────────────────────────────────────────
    _fetch_index_prev_close()

    print("[fetcher] 盤前資料預取完成")
```

---

## 7. `data/subscriber.py` — 即時 tick 訂閱

### 重要規則
- **callback 內絕對不做計算**，只更新 `STOCK_STORE`
- `tick.total_volume` 是今日累積量（張），直接存入，不需手動累加
- 訂閱數上限 200 個，持股超過 200 檔需分批
- **加權與櫃買指數的 `code` 都是 `"001"`**，靠 `exchange`（`Exchange.TSE` / `Exchange.OTC`）區分
- **只能註冊一個 `@api.on_tick_stk_v1()` callback**，重複註冊後者會覆蓋前者，所以持股與指數共用同一個 callback，內部分流

```python
# data/subscriber.py
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
        指數 code 同為 "001"，用 exchange 區分加權 / 櫃買。
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

    # 加權指數（TSE）
    api.subscribe(
        api.Contracts.Indexs.TSE[TAIEX_CODE],
        quote_type=sj.QuoteType.Tick,
    )
    # 櫃買指數（OTC）
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
```

---

## 8. `data/scanner.py` — 定時排行快照

**職責**：每 30 秒呼叫兩次 `api.scanners`（AmountRank + ChangePercentRank），更新 `MARKET_STATE`。由 APScheduler 排程呼叫。

### 重要限制
- `api.scanners` count 上限 200
- 10 秒內所有行情查詢總次數上限 50 次（含 kbars/ticks）→ scanner 每 30 秒最多 2 次即可
- **全市場漲跌家數**：`scanners` 最多 200 筆，無法涵蓋全市場（約 1700+ 檔）。
  本系統的「全市場家數」改以 `ChangePercentRank` 的 200 筆樣本中各 change_type 計數呈現，
  代表的是「漲跌幅最極端的 200 檔」分布，非真正全市場。若要真正全市場需改用 `api.snapshots` 全掃（成本高），列為後續優化。

```python
# data/scanner.py
import pandas as pd
import shioaji as sj
from store.memory_store import MARKET_STATE
from config import SCANNER_COUNT, LIMIT_PRICE_THRESHOLD
from data.session import get_api
from datetime import datetime


def update_scanners() -> None:
    """
    呼叫一次 AmountRank + 兩次 ChangePercentRank，
    更新 top200 廣度指標、漲跌停統計、樣本漲跌家數。
    由排程每 30 秒呼叫一次。
    """
    api = get_api()

    try:
        # ── 成交值前 200（模組二）───────────────────────
        top200 = api.scanners(
            scanner_type=sj.ScannerType.AmountRank,
            count=SCANNER_COUNT,
            ascending=True,   # 金額由大到小
        )
        MARKET_STATE.top200_df = pd.DataFrame([s.dict() for s in top200])

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

        # ── 樣本漲跌平家數（漲幅榜 + 跌幅榜合併去重）─────
        combined = pd.concat([df_g, df_l], ignore_index=True).drop_duplicates(subset=["code"])
        MARKET_STATE.sample_up   = int(combined["change_type"].isin([1, 2]).sum())
        MARKET_STATE.sample_down = int(combined["change_type"].isin([4, 5]).sum())
        MARKET_STATE.sample_flat = int((combined["change_type"] == 3).sum())

        MARKET_STATE.last_scanner_update = datetime.now()
        print(f"[scanner] 更新完成 {MARKET_STATE.last_scanner_update:%H:%M:%S}")

    except Exception as e:
        print(f"[scanner] 更新失敗: {e}")
```

---

## 9. `compute/volume_surge.py` — 模組一爆量預估

**觸發時間**：盤中持續計算，10:30 為關鍵判斷點（Dash 每秒讀取，10:30 後 `vol_ratio` 才有完整意義）

### 爆量邏輯
```
基準：前五日同時段（09:00~10:30）均量 v5d_morning_avg_vol
比較：今日 10:30 前累積量 today_total_vol
爆量條件：vol_ratio = today_total_vol / v5d_morning_avg_vol > 0.5

註：用「同時段均量」而非「全日均量」，比較基準才公平。
若 10:30 前的量已達同時段均量一半以上 → 今日大概率爆量。
預估全日量：以同時段量回推（同時段量 ÷ 同時段歷史占全日比例）
```

```python
# compute/volume_surge.py
from store.memory_store import STOCK_STORE
from config import VOLUME_SURGE_THRESHOLD

# 同時段（09:00~10:30）歷史上約占全日成交量的比例
# 台股早盤量通常較大，實務上 09:00~10:30 約占全日 40%
MORNING_TO_FULLDAY_RATIO = 0.40


def calc_volume_surge() -> list[dict]:
    """
    計算所有持股的爆量預估結果。
    Dash 每秒呼叫，10:30 後 vol_ratio 才具完整判斷意義。
    回傳結果列表（依量比降冪排序）。
    """
    results = []
    for code, state in STOCK_STORE.items():
        # 優先用同時段均量，沒有才退回全日均量
        base = state.v5d_morning_avg_vol or state.v5d_avg_vol
        if base <= 0:
            continue  # 盤前資料未取得，跳過

        vol_ratio = state.today_total_vol / base
        est_full_day = int(state.today_total_vol / MORNING_TO_FULLDAY_RATIO)

        results.append({
            "symbol":             code,
            "name":               state.name,
            "today_vol_10h30":    state.today_total_vol,
            "v5d_avg":            round(base, 0),          # 顯示用的比較基準
            "vol_ratio":          round(vol_ratio, 3),
            "is_potential_surge": vol_ratio > VOLUME_SURGE_THRESHOLD,
            "est_full_day_vol":   est_full_day,
        })

    return sorted(results, key=lambda x: x["vol_ratio"], reverse=True)
```

---

## 10. `compute/market_breadth.py` — 模組二市場廣度

**職責**：從 `MARKET_STATE.top200_df` 計算廣度指標，Dash callback 每秒呼叫。

### 漲跌幅計算說明
- ScannerItem 有 `change_price`（漲跌價）與 `close`（現價）
- 昨收 = `close - change_price`
- 漲跌幅% = `change_price / (close - change_price) * 100`
- **邊界情況**：若昨收（分母）為 0 則跳過該筆，避免除以零

```python
# compute/market_breadth.py
from store.memory_store import MARKET_STATE


def calc_market_breadth() -> dict:
    """
    從 top200_df 與樣本家數計算市場廣度指標。
    回傳 dict，供 Dash 儀表板顯示。
    """
    df = MARKET_STATE.top200_df
    if df.empty:
        return {}

    # 計算漲跌幅%（避免昨收為 0）
    df = df.copy()
    df["prev_close"] = df["close"] - df["change_price"]
    mask = df["prev_close"] != 0
    df.loc[mask, "chg_pct"] = df.loc[mask, "change_price"] / df.loc[mask, "prev_close"] * 100
    df["chg_pct"] = df["chg_pct"].fillna(0)

    # 前200 漲跌家數
    top200_up   = int(df["change_type"].isin([1, 2]).sum())
    top200_down = int(df["change_type"].isin([4, 5]).sum())

    # 簡單平均漲跌幅
    avg_chg = float(df["chg_pct"].mean())

    # 成交值加權漲跌幅
    total_amt = df["total_amount"].sum()
    wavg_chg = (
        float((df["chg_pct"] * df["total_amount"]).sum() / total_amt)
        if total_amt > 0 else 0.0
    )

    # 加權 / 櫃買漲跌幅
    taiex_prev = MARKET_STATE.taiex_prev_close
    otc_prev   = MARKET_STATE.otc_prev_close
    taiex_chg_pct = (
        (MARKET_STATE.taiex_close - taiex_prev) / taiex_prev * 100
        if taiex_prev > 0 else 0.0
    )
    otc_chg_pct = (
        (MARKET_STATE.otc_close - otc_prev) / otc_prev * 100
        if otc_prev > 0 else 0.0
    )

    return {
        # 樣本漲跌平家數（漲幅榜+跌幅榜去重，非真正全市場）
        "sample_up":          MARKET_STATE.sample_up,
        "sample_down":        MARKET_STATE.sample_down,
        "sample_flat":        MARKET_STATE.sample_flat,
        # 前200 成交值
        "top200_up":          top200_up,
        "top200_down":        top200_down,
        "top200_avg_chg_pct": round(avg_chg, 2),
        "top200_wavg_chg_pct": round(wavg_chg, 2),
        # 指數對照
        "taiex_chg_pct":      round(taiex_chg_pct, 2),
        "otc_chg_pct":        round(otc_chg_pct, 2),
    }
```

---

## 11. `compute/macd.py` — 模組三持股 MACD

```python
# compute/macd.py
import pandas as pd
from store.memory_store import STOCK_STORE, MARKET_STATE
from config import MACD_FAST, MACD_SLOW, MACD_SIGNAL, MACD_MIN_BARS


def calc_macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """
    計算 MACD。回傳 DataFrame，欄位：dif, macd_signal, histogram
    使用 EMA（adjust=False，與多數交易軟體一致）
    """
    ema_fast   = close.ewm(span=fast,   adjust=False).mean()
    ema_slow   = close.ewm(span=slow,   adjust=False).mean()
    dif        = ema_fast - ema_slow
    macd_sig   = dif.ewm(span=signal,   adjust=False).mean()
    histogram  = dif - macd_sig
    return pd.DataFrame({
        "dif":        dif,
        "macd_signal": macd_sig,
        "histogram":  histogram,
    })


def _hist_status(hist: float, prev: float) -> str:
    """
    柱狀體相對前一日的狀態：
    翻紅 / 翻綠（穿越零軸）、紅柱增長 / 紅柱縮短、綠柱增長 / 綠柱縮短。
    """
    if prev <= 0 < hist:
        return "翻紅"
    if prev >= 0 > hist:
        return "翻綠"
    if hist > 0:                      # 紅柱
        return "紅柱增長" if hist > prev else "紅柱縮短"
    if hist < 0:                      # 綠柱（越負越長）
        return "綠柱增長" if hist < prev else "綠柱縮短"
    return "—"


def _macd_row(kind, symbol, name, daily_closes, current_price) -> dict:
    """用「過去日收 + 當前即時價」即時算一檔/一指數的 MACD 狀態。"""
    base = {
        "kind": kind, "symbol": symbol, "name": name,
        "dif": None, "macd": None, "histogram": None, "prev_histogram": None,
        "bar": None, "cross": "", "hist_status": "", "ok": False,
    }
    closes = list(daily_closes)
    if current_price and current_price > 0:
        closes = closes + [float(current_price)]      # 接上今日即時價
    if len(closes) < MACD_MIN_BARS:
        return base                                   # 日線根數不足

    df = calc_macd(pd.Series(closes), fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL)
    hist = float(df["histogram"].iloc[-1]); prev = float(df["histogram"].iloc[-2])
    dif  = float(df["dif"].iloc[-1]);        macd = float(df["macd_signal"].iloc[-1])

    cross = ""
    if prev <= 0 < hist:
        cross = "golden"
    elif prev >= 0 > hist:
        cross = "death"

    base.update({
        "dif": dif, "macd": macd, "histogram": hist, "prev_histogram": prev,
        "bar": "red" if hist > 0 else "green",        # 紅柱（多）/ 綠柱（空）
        "cross": cross,
        "hist_status": _hist_status(hist, prev),      # 柱狀體前一日狀態比較
        "ok": True,
    })
    return base


def get_macd_display() -> list[dict]:
    """
    回傳 MACD 顯示清單：加權指數、櫃買指數，接著由股期部位推導的持股。
    每筆含 dif、macd（訊號線）、histogram（柱狀體）、紅綠柱、交叉與
    「柱狀體前一日狀態比較」（hist_status），盤中即時重算。
    """
    rows = [
        _macd_row("index", "TAIEX", "加權指數",
                  MARKET_STATE.taiex_daily_closes, MARKET_STATE.taiex_close),
        _macd_row("index", "OTC", "櫃買指數",
                  MARKET_STATE.otc_daily_closes, MARKET_STATE.otc_close),
    ]
    for code, state in STOCK_STORE.items():
        rows.append(_macd_row("stock", code, state.name,
                              state.daily_closes, state.latest_close))
    return rows
```

**模組三重點（實作版）**
- 監控對象：**加權指數、櫃買指數 + 由股期部位推導的持股**（非靜態 WATCHLIST）。
- **盤中即時**：以「過去交易日收盤（盤前抓，指數 ~60 根 / 個股 ~55 根）＋ 當前即時價」每次重算，柱狀體會隨盤中價格變動。指數現價由 `update_index_quotes()` 每 30 秒輪詢；個股現價由 `update_holdings_volume()` 的 snapshot 取得。
- 儀表板顯示欄位：`DIF`、`MACD`（訊號線）、`柱狀體`（紅綠上色）、**`柱狀體前一日狀態比較`**（翻紅／翻綠／紅柱增長／紅柱縮短／綠柱增長／綠柱縮短）。

---

## 12. `compute/limit_stats.py` — 模組四漲跌停統計

```python
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
        "limit_up_total":    lu_low + lu_high,
        "limit_up_low":      lu_low,    # 漲停 且 < 50 元（低價投機）
        "limit_up_high":     lu_high,   # 漲停 且 >= 50 元（中高價強勢）
        "limit_down_total":  ld_low + ld_high,
        "limit_down_low":    ld_low,    # 跌停 且 < 50 元（低價地雷）
        "limit_down_high":   ld_high,   # 跌停 且 >= 50 元（系統性賣壓）
        "price_threshold":   LIMIT_PRICE_THRESHOLD,
        "last_update":       MARKET_STATE.last_scanner_update,
    }
```

---

## 13. `output/dash_app.py` — 本地網頁儀表板

### 架構說明

- Dash app 作為獨立 thread 執行（`threading.Thread`），與 APScheduler 並行
- `dcc.Interval` 每 **1 秒**觸發 callback，從 `STOCK_STORE` / `MARKET_STATE` 讀取最新資料更新畫面
- **不需要 WebSocket**，全部 in-process 讀取記憶體，無網路延遲
- 啟動後自動在瀏覽器開啟 `http://127.0.0.1:8050`

### 視覺設計規格

```
背景色：#0a0a0f（近黑深藍）
面板色：#0f0f1a
邊框色：#1a1a30

漲 / 紅柱：#ff3366（螢光粉紅）
跌 / 綠柱：#00e676（螢光草綠）
爆量觸發：#00ff88（螢光綠）
黃金交叉：#ffcc00（螢光金）
代碼文字：#88ccff（淡藍）
一般數值：#c0c0e0（近白）
標題文字：#e0e8ff（亮白）
次要文字：#7070aa（灰紫）
靜態標籤：#6666aa（暗紫）
```

### 佈局結構

```
Header（標題 + 即時時鐘）
│
├── 模組一：持股爆量預估（全寬 Table）
│     爆量行整列螢光綠底色，量比進度條
│
├── 模組二 + 模組四（左右各半）
│   ├── 左：市場廣度（漲跌比例條 + 六個數值）
│   └── 右：漲跌停統計（兩張卡片，漲停/跌停）
│
└── 模組三：持股 MACD（全寬 Table）
      柱狀體用小色塊 + 數值，黃金交叉用金色 badge
```

### 程式碼

```python
# output/dash_app.py
import threading
from datetime import datetime
import dash
from dash import dcc, html, dash_table, Input, Output, callback
import dash_bootstrap_components as dbc

from store.memory_store import STOCK_STORE, MARKET_STATE
from compute.volume_surge   import calc_volume_surge
from compute.market_breadth import calc_market_breadth
from compute.macd           import get_macd_display
from compute.limit_stats    import get_limit_stats
from config                 import LIMIT_PRICE_THRESHOLD

# ── 色彩常數 ─────────────────────────────────────────────
C = {
    "bg":       "#0a0a0f",
    "panel":    "#0f0f1a",
    "border":   "#1a1a30",
    "up":       "#ff3366",   # 漲
    "dn":       "#00e676",   # 跌
    "surge":    "#00ff88",   # 爆量
    "gold":     "#ffcc00",   # 黃金交叉
    "code":     "#88ccff",   # 代碼
    "text":     "#c0c0e0",   # 一般數值
    "title":    "#e0e8ff",   # 標題
    "muted":    "#7070aa",   # 次要
    "dim":      "#6666aa",   # 靜態標籤
    "row_hit":  "#0a180e",   # 爆量行背景
}

FONT = "'JetBrains Mono', 'Fira Code', 'Consolas', monospace"


def _panel(children, style=None):
    base = {
        "background": C["panel"],
        "border": f"1px solid {C['border']}",
        "borderRadius": "8px",
        "padding": "12px 14px",
    }
    if style:
        base.update(style)
    return html.Div(children, style=base)


def _section_label(text):
    return html.Div(text, style={
        "fontSize": "10px",
        "color": C["dim"],
        "letterSpacing": "0.12em",
        "textTransform": "uppercase",
        "marginBottom": "8px",
        "fontFamily": FONT,
    })


# ── Dash app 初始化 ──────────────────────────────────────
app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.BOOTSTRAP],
    suppress_callback_exceptions=True,
)
app.title = "Intraday Monitor"

# ── Layout ───────────────────────────────────────────────
app.layout = html.Div(
    style={"background": C["bg"], "minHeight": "100vh", "padding": "16px", "fontFamily": FONT},
    children=[
        dcc.Interval(id="interval", interval=1_000, n_intervals=0),   # 每 1 秒觸發

        # Header
        html.Div(
            style={"display": "flex", "justifyContent": "space-between", "alignItems": "center",
                   "borderBottom": f"1px solid {C['border']}", "paddingBottom": "10px", "marginBottom": "14px"},
            children=[
                html.Div([
                    html.Span("● ", style={"color": C["surge"], "fontSize": "10px"}),
                    html.Span("INTRADAY MONITOR", style={"color": C["title"], "fontSize": "13px",
                                                          "fontWeight": "500", "letterSpacing": "0.1em"}),
                ]),
                html.Div(id="clock", style={"color": C["surge"], "fontSize": "11px",
                                             "background": "#0a1a12", "border": f"1px solid #1a3a28",
                                             "borderRadius": "4px", "padding": "3px 10px",
                                             "letterSpacing": "0.05em"}),
            ]
        ),

        # 模組一：爆量預估
        _section_label("▸ 持股爆量預估（10:30 觸發）"),
        _panel(html.Div(id="surge-table"), style={"marginBottom": "12px"}),

        # 模組二 + 模組四
        html.Div(
            style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "12px", "marginBottom": "12px"},
            children=[
                html.Div([_section_label("▸ 市場廣度（前200成交值）"), _panel(html.Div(id="breadth-panel"))]),
                html.Div([_section_label(f"▸ 漲跌停（分界 {LIMIT_PRICE_THRESHOLD} 元）"), _panel(html.Div(id="limit-panel"))]),
            ]
        ),

        # 模組三：MACD
        _section_label("▸ 持股 MACD 狀態（日線）"),
        _panel(html.Div(id="macd-table")),

        html.Div(id="scan-update", style={"color": C["dim"], "fontSize": "10px",
                                           "textAlign": "right", "marginTop": "10px",
                                           "letterSpacing": "0.04em"}),
    ]
)


# ── Callbacks ────────────────────────────────────────────

@callback(Output("clock", "children"), Input("interval", "n_intervals"))
def update_clock(_):
    return datetime.now().strftime("%H:%M:%S TWN")


@callback(Output("surge-table", "children"), Input("interval", "n_intervals"))
def update_surge(_):
    results = calc_volume_surge()
    if not results:
        return html.Div("盤前資料未就緒", style={"color": C["muted"], "fontSize": "12px"})

    rows = []
    for r in results:
        is_hit = r["is_potential_surge"]
        pct = min(r["vol_ratio"] / 1.0 * 100, 100)   # 進度條：以 100% 均量為滿格
        bar_color = C["up"] if is_hit else C["surge"]
        row_bg = C["row_hit"] if is_hit else "transparent"

        rows.append(html.Tr(
            style={"background": row_bg, "borderTop": f"1px solid {C['border']}"},
            children=[
                html.Td(r["symbol"], style={"color": C["surge"] if is_hit else C["code"], "fontWeight": "500" if is_hit else "400", "padding": "6px 8px"}),
                html.Td(r["name"],   style={"color": C["muted"], "padding": "6px 8px"}),
                html.Td(f"{r['today_vol_10h30']:,}", style={"color": C["text"], "textAlign": "right", "padding": "6px 8px", "fontVariantNumeric": "tabular-nums"}),
                html.Td(f"{int(r['v5d_avg']):,}",   style={"color": C["text"], "textAlign": "right", "padding": "6px 8px", "fontVariantNumeric": "tabular-nums"}),
                html.Td(
                    html.Div(style={"background": "#111120", "borderRadius": "2px", "height": "4px", "width": "60px", "overflow": "hidden"},
                             children=[html.Div(style={"height": "100%", "width": f"{pct:.0f}%", "background": bar_color, "boxShadow": f"0 0 6px {bar_color}", "borderRadius": "2px"})]),
                    style={"padding": "6px 8px", "verticalAlign": "middle"}
                ),
                html.Td(f"{r['vol_ratio']:.3f}", style={"color": C["surge"] if is_hit else C["text"], "fontWeight": "500" if is_hit else "400", "textAlign": "right", "padding": "6px 8px", "fontVariantNumeric": "tabular-nums"}),
                html.Td(
                    html.Span("爆量" if is_hit else "觀察", style={
                        "background": "#0a180e" if is_hit else "#111120",
                        "color": C["surge"] if is_hit else "#555588",
                        "border": f"1px solid {'#1a4028' if is_hit else C['border']}",
                        "fontSize": "10px", "padding": "2px 6px", "borderRadius": "3px",
                    }),
                    style={"padding": "6px 8px"}
                ),
                html.Td(f"{r['est_full_day_vol']:,}", style={"color": C["surge"] if is_hit else C["text"], "textAlign": "right", "padding": "6px 8px", "fontVariantNumeric": "tabular-nums"}),
            ]
        ))

    return html.Table(
        style={"width": "100%", "borderCollapse": "collapse", "fontSize": "11px"},
        children=[
            html.Thead(html.Tr([
                html.Th(h, style={"color": C["dim"], "fontWeight": "400", "fontSize": "10px",
                                   "padding": "0 8px 6px", "letterSpacing": "0.05em",
                                   "textAlign": "right" if i > 1 else "left"})
                for i, h in enumerate(["代碼", "名稱", "今日量", "五日均量", "進度", "量比", "狀態", "預估全日"])
            ])),
            html.Tbody(rows),
        ]
    )


@callback(Output("breadth-panel", "children"), Input("interval", "n_intervals"))
def update_breadth(_):
    data = calc_market_breadth()
    if not data:
        return html.Div("Scanner 資料未就緒", style={"color": C["muted"], "fontSize": "12px"})

    # 比例條用樣本漲跌平家數
    s_up, s_dn, s_fl = data["sample_up"], data["sample_down"], data["sample_flat"]
    total = s_up + s_dn + s_fl or 1
    up_pct = s_up / total * 100
    dn_pct = s_dn / total * 100

    rows = [
        ("前200 漲 / 跌",     f"{data['top200_up']}",           f"{data['top200_down']}",      True),
        ("前200 均漲跌幅",    f"{data['top200_avg_chg_pct']:+.2f}%",  None,                   data["top200_avg_chg_pct"] >= 0),
        ("前200 加權漲跌幅",  f"{data['top200_wavg_chg_pct']:+.2f}%", None,                   data["top200_wavg_chg_pct"] >= 0),
        ("加權指數",          f"{data['taiex_chg_pct']:+.2f}%",       None,                   data["taiex_chg_pct"] >= 0),
        ("櫃買指數",          f"{data['otc_chg_pct']:+.2f}%",         None,                   data["otc_chg_pct"] >= 0),
    ]

    chg_rows = []
    for label, val, val2, is_pos in rows:
        if val2 is not None:
            # 漲/跌家數：分開著色
            val_node = html.Span([
                html.Span(val,  style={"color": C["up"]}),
                html.Span(" / ", style={"color": "#333360"}),
                html.Span(val2, style={"color": C["dn"]}),
            ])
        else:
            val_node = html.Span(val, style={"color": C["up"] if is_pos else C["dn"], "fontWeight": "500"})

        chg_rows.append(html.Div(
            style={"display": "flex", "justifyContent": "space-between", "padding": "4px 0",
                   "borderTop": f"1px solid {C['border']}", "fontSize": "11px"},
            children=[
                html.Span(label, style={"color": C["muted"]}),
                val_node,
            ]
        ))

    return html.Div([
        # 樣本家數說明
        html.Div("極端漲跌幅樣本（漲跌榜各200去重）", style={"fontSize": "10px", "color": C["dim"], "marginBottom": "6px"}),
        # 比例條
        html.Div(style={"height": "6px", "borderRadius": "3px", "overflow": "hidden",
                        "background": "#111120", "display": "flex", "marginBottom": "8px"},
                 children=[
                     html.Div(style={"width": f"{up_pct:.0f}%", "background": C["up"], "boxShadow": f"0 0 4px {C['up']}"}),
                     html.Div(style={"width": f"{100 - up_pct - dn_pct:.0f}%", "background": "#2a2a3a"}),
                     html.Div(style={"width": f"{dn_pct:.0f}%", "background": C["dn"], "boxShadow": f"0 0 4px {C['dn']}"}),
                 ]),
        # 家數摘要
        html.Div(style={"display": "flex", "gap": "10px", "marginBottom": "10px", "fontSize": "11px"},
                 children=[
                     html.Span(f"▲ {s_up}", style={"color": C["up"], "fontVariantNumeric": "tabular-nums"}),
                     html.Span(f"— {s_fl}", style={"color": C["dim"], "fontVariantNumeric": "tabular-nums"}),
                     html.Span(f"▼ {s_dn}", style={"color": C["dn"], "fontVariantNumeric": "tabular-nums"}),
                 ]),
        # 各項數值
        html.Div(chg_rows),
    ])


@callback(Output("limit-panel", "children"), Input("interval", "n_intervals"))
def update_limit(_):
    data = get_limit_stats()

    def _card(title, total, low_val, high_val, low_note, high_note, color, border_color):
        return html.Div(
            style={"background": "#0f0f1a", "borderRadius": "6px", "padding": "10px 12px",
                   "border": f"1px solid {border_color}"},
            children=[
                html.Div(title, style={"fontSize": "10px", "color": C["dim"], "marginBottom": "6px", "letterSpacing": "0.08em"}),
                html.Div(f"{total}", style={"fontSize": "26px", "fontWeight": "500", "color": color,
                                             "textShadow": f"0 0 14px {color}88", "marginBottom": "8px",
                                             "fontVariantNumeric": "tabular-nums"}),
                html.Div(style={"display": "flex", "gap": "6px"}, children=[
                    html.Div(style={"flex": "1", "background": "#0a0a14", "border": f"1px solid {C['border']}",
                                     "borderRadius": "4px", "padding": "6px 8px"},
                             children=[
                                 html.Div(f"< {LIMIT_PRICE_THRESHOLD} 元", style={"fontSize": "9px", "color": C["dim"], "marginBottom": "3px", "letterSpacing": "0.04em"}),
                                 html.Div(str(low_val), style={"fontSize": "16px", "fontWeight": "500", "color": C["text"], "fontVariantNumeric": "tabular-nums"}),
                                 html.Div(low_note, style={"fontSize": "9px", "color": "#3a3a6a", "marginTop": "2px"}),
                             ]),
                    html.Div(style={"flex": "1", "background": "#0a0a14", "border": f"1px solid {C['border']}",
                                     "borderRadius": "4px", "padding": "6px 8px"},
                             children=[
                                 html.Div(f"≥ {LIMIT_PRICE_THRESHOLD} 元", style={"fontSize": "9px", "color": C["dim"], "marginBottom": "3px", "letterSpacing": "0.04em"}),
                                 html.Div(str(high_val), style={"fontSize": "16px", "fontWeight": "500", "color": C["text"], "fontVariantNumeric": "tabular-nums"}),
                                 html.Div(high_note, style={"fontSize": "9px", "color": "#3a3a6a", "marginTop": "2px"}),
                             ]),
                ]),
            ]
        )

    return html.Div(
        style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "10px"},
        children=[
            _card("漲停", data["limit_up_total"], data["limit_up_low"], data["limit_up_high"],
                  "低價投機", "中高價", C["up"], "#2a0a18"),
            _card("跌停", data["limit_down_total"], data["limit_down_low"], data["limit_down_high"],
                  "地雷", "系統風險", C["dn"], "#003320"),
        ]
    )


@callback(Output("macd-table", "children"), Input("interval", "n_intervals"))
def update_macd(_):
    results = get_macd_display()
    if not results:
        return html.Div("資料未就緒", style={"color": C["muted"], "fontSize": "12px"})

    rows = []
    for r in results:
        is_red = r["histogram"] > 0
        hist_color = C["up"] if is_red else C["dn"]
        bar_label = "紅柱" if is_red else "綠柱"
        bar_style = {
            "fontSize": "10px", "padding": "1px 6px", "borderRadius": "3px",
            "background": "#1a0510" if is_red else "#001a0e",
            "color": C["up"] if is_red else C["dn"],
            "border": f"1px solid {'#3a0a20' if is_red else '#004428'}",
        }
        momentum_color = C["up"] if "擴張" in r["momentum"] else C["dim"]

        cross_node = html.Span("—", style={"color": C["dim"], "fontSize": "10px"})
        if "黃金" in r["cross_signal"]:
            cross_node = html.Span("黃金交叉", style={
                "fontSize": "10px", "padding": "1px 6px", "borderRadius": "3px",
                "background": "#1a1200", "color": C["gold"], "border": f"1px solid #443200",
            })
        elif "死亡" in r["cross_signal"]:
            cross_node = html.Span("死亡交叉", style={
                "fontSize": "10px", "padding": "1px 6px", "borderRadius": "3px",
                "background": "#1a0510", "color": C["up"], "border": f"1px solid #3a0a20",
            })

        rows.append(html.Tr(
            style={"borderTop": f"1px solid {C['border']}"},
            children=[
                html.Td(r["symbol"], style={"color": C["code"], "padding": "6px 6px", "fontVariantNumeric": "tabular-nums"}),
                html.Td(r["name"],   style={"color": C["muted"], "padding": "6px 6px"}),
                html.Td(f"{r['dif']:+.4f}",         style={"color": C["up"] if r["dif"] > 0 else C["dn"], "textAlign": "right", "padding": "6px 6px", "fontVariantNumeric": "tabular-nums"}),
                html.Td(f"{r['macd_signal']:+.4f}",  style={"color": C["up"] if r["macd_signal"] > 0 else C["dn"], "textAlign": "right", "padding": "6px 6px", "fontVariantNumeric": "tabular-nums"}),
                html.Td(
                    html.Div(style={"display": "flex", "alignItems": "center", "gap": "4px", "justifyContent": "flex-end"},
                             children=[
                                 html.Div(style={"width": "28px", "height": "5px", "borderRadius": "1px",
                                                  "background": hist_color, "boxShadow": f"0 0 5px {hist_color}"}),
                                 html.Span(f"{r['histogram']:+.4f}", style={"color": hist_color, "fontVariantNumeric": "tabular-nums"}),
                             ]),
                    style={"padding": "6px 6px", "textAlign": "right"}
                ),
                html.Td(html.Span(bar_label, style=bar_style), style={"padding": "6px 6px"}),
                html.Td(r["momentum"], style={"color": momentum_color, "fontSize": "10px", "padding": "6px 6px"}),
                html.Td(cross_node, style={"padding": "6px 6px"}),
            ]
        ))

    return html.Table(
        style={"width": "100%", "borderCollapse": "collapse", "fontSize": "11px"},
        children=[
            html.Thead(html.Tr([
                html.Th(h, style={"color": C["dim"], "fontWeight": "400", "fontSize": "10px",
                                   "padding": "0 6px 6px", "letterSpacing": "0.05em",
                                   "textAlign": "right" if i > 1 else "left"})
                for i, h in enumerate(["代碼", "名稱", "DIF", "Signal", "柱狀體", "顏色", "動能", "交叉訊號"])
            ])),
            html.Tbody(rows),
        ]
    )


@callback(Output("scan-update", "children"), Input("interval", "n_intervals"))
def update_footer(_):
    t = MARKET_STATE.last_scanner_update
    return f"↻ scanner 最後更新 {t:%H:%M:%S} · 每 30 秒刷新"


# ── 啟動函式（供 main.py 呼叫）──────────────────────────
def run_dash() -> None:
    """在獨立 thread 中啟動 Dash server"""
    app.run(
        host="127.0.0.1",
        port=8050,
        debug=False,
        use_reloader=False,   # 必須關閉，否則 thread 模式下會 fork 衝突
    )


def start_dash_thread() -> threading.Thread:
    t = threading.Thread(target=run_dash, daemon=True, name="dash-server")
    t.start()
    print("[dash] 儀表板啟動 → http://127.0.0.1:8050")
    return t
```

---

## 14. `main.py` — 進入點與排程

**執行流程**：APScheduler 管後端資料更新，Dash 在獨立 thread 跑 HTTP server，主 thread 持有等待中斷。

```python
# main.py
import time
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from data.session    import init_api, logout_api
from data.fetcher    import prefetch_all
from data.subscriber import setup_callbacks, subscribe_all, unsubscribe_all
from data.scanner    import update_scanners
from output.dash_app import start_dash_thread
from config          import SCANNER_INTERVAL_SEC


def main() -> None:
    # 1. 登入 Shioaji
    init_api()

    # 2. 盤前預取歷史資料（均量 + 日線 MACD）
    prefetch_all()

    # 3. 設定 tick callback，訂閱持股 + 指數
    setup_callbacks()
    subscribe_all()

    # 4. 立即執行一次 scanner
    update_scanners()

    # 5. 啟動 Dash（獨立 thread）
    start_dash_thread()

    # 6. APScheduler
    scheduler = BackgroundScheduler(timezone="Asia/Taipei")

    # scanner 每 30 秒
    scheduler.add_job(
        update_scanners,
        IntervalTrigger(seconds=SCANNER_INTERVAL_SEC),
        id="scanner",
    )
    # 13:30 收盤取消訂閱
    scheduler.add_job(
        unsubscribe_all,
        CronTrigger(hour=13, minute=30, timezone="Asia/Taipei"),
        id="market_close",
    )

    scheduler.start()
    print("[main] 排程啟動 | 儀表板 → http://127.0.0.1:8050")
    print("[main] 按 Ctrl+C 結束")

    # 7. 主 thread 等待中斷
    try:
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        print("\n[main] 收到中斷，清理中...")
    finally:
        scheduler.shutdown(wait=False)
        unsubscribe_all()
        logout_api()
        print("[main] 程式結束")


if __name__ == "__main__":
    main()
```

---

## 15. Shioaji API 速查與限制

### API 對應一覽

| 需求 | API | 關鍵參數 | 備註 |
|------|-----|----------|------|
| 即時持股成交量 | `api.subscribe()` + `@api.on_tick_stk_v1()` | `QuoteType.Tick` | `tick.total_volume` = 今日累積張數 |
| 前五日均量 / 日線 MACD | `api.kbars()` × 2 段 | 各 30 天，分段抓合併 | 回傳分鐘 K，需自行 groupby 重建日線 |
| 成交值前 200 | `api.scanners()` | `ScannerType.AmountRank, count=200` | ScannerItem.change_type 判斷漲跌 |
| 漲停 / 跌停家數 | `api.scanners()` × 2 | `ChangePercentRank, ascending=True/False` | change_type==1 漲停，==5 跌停 |
| 加權 / 櫃買漲跌幅 | `api.subscribe(Indexs.TSE/OTC)` | `QuoteType.Tick` | tick.close 搭配昨收計算% |

### 重要限制

| 限制 | 數值 | 影響模組 |
|------|------|----------|
| `api.kbars` 查詢區間 | 最多 30 天 | 模組一、三（需分兩段抓） |
| 盤中 `kbars` 次數 | 每日 ≤ 270 次 | 模組一、三 |
| `api.scanners` count | 最大 200 | 模組二、四 |
| 行情查詢總次數 | 10 秒內 ≤ 50 次 | 全部 |
| 訂閱數上限 | 200 個 | 模組一（持股 ≤ 200 檔） |
| API 登入次數 | 每日 ≤ 1000 次 | session.py |

### `change_type` / `chg_type` 對照

| 數值 | 意義 |
|------|------|
| `1` | 漲停 |
| `2` | 漲 |
| `3` | 平盤 |
| `4` | 跌 |
| `5` | 跌停 |

---

## 16. Claude Code 啟動指引

### 第一步：安裝 Shioaji Skill

```bash
claude plugin marketplace add Sinotrade/Shioaji
claude plugin install shioaji
```

若無法安裝 plugin，改用以下方式讓 Claude Code 讀取官方完整文件：
在對話開頭貼上 `https://sinotrade.github.io/llms-full.txt`

### 第二步：建立專案，使用以下啟動提示詞

```
請閱讀這份 SPEC.md 並依照它逐一實作 intraday-futures-monitor 專案。

實作順序：
1. requirements.txt + .env.example
2. config.py
3. store/memory_store.py
4. data/session.py
5. data/fetcher.py
6. data/subscriber.py
7. data/scanner.py
8. compute/macd.py
9. compute/volume_surge.py
10. compute/market_breadth.py
11. compute/limit_stats.py
12. output/dash_app.py
13. main.py

每個檔案完成後請說明做了什麼，有任何 API 不確定的地方請參考 Shioaji 官方文件。
開發模式一律使用 simulation=True，正式環境由 .env 的 SJ_SIMULATION 控制。
```

### 注意事項

1. **絕對不要在 tick callback 做運算** — 只更新 `STOCK_STORE` / `MARKET_STATE`
2. **只能註冊一個 `@api.on_tick_stk_v1()`** — 持股與指數共用同一個 callback，內部用 `code` + `exchange` 分流
3. **加權與櫃買指數 code 都是 "001"** — 必須靠 `exchange`（TSE / OTC）區分
4. **`api.kbars` 每段最多 30 天** — 抓 80 天需迴圈分段（含週末假日，80 日曆日才夠 40 根日線）
5. **`api.scanners` 最多 200 筆** — 全市場家數只是「極端漲跌幅樣本」，非真正全市場
6. **指數昨收靠盤前 kbars 抓** — `taiex_prev_close` / `otc_prev_close` 沒抓到，盤中漲跌幅會顯示 0
7. **爆量用「同時段均量」** — `v5d_morning_avg_vol` 為主，沒有才退回 `v5d_avg_vol`
8. **`simulation=True` 時部分行情資料可能不完整** — 以正式環境測試為準
9. **Dash 用 `use_reloader=False`** — thread 模式下開 reloader 會 fork 衝突

---

*文件版本 v4.1 — 修正爆量同時段均量、指數 callback 分流、昨收抓取、樣本家數一致性*