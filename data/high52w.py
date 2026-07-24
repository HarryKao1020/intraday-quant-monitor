# data/high52w.py
"""
建立全市場「52 週（前一年）最高價」表 + 各檔近 N 日收盤序列。
供模組六（創年新高）與模組十一（破月／季／半年線家數）使用。

- 用 daily_quotes 逐日抓全市場 OHLC，取每檔在 [今天-365, 昨天] 的最高 High
  及該高點「最近一次出現的日期」（供計算隔幾個交易日創高）。
- 同一份資料順便留下每檔最近 CLOSE_TAIL_LEN 個日收（升冪，不含今天），
  模組十一據此算 20/60/120 日均線，不需額外 API 呼叫。
- 另存年內全部交易日列表（升冪），計算間隔用。
- 每日快取到 cache/high52w3_<date>.json：當天首次建約 1~2 分鐘，之後秒載入。
- 以獨立 sim session 於背景執行，不與主連線／排程搶用 API。
"""
import datetime as dt
import json
import os
import time
from pathlib import Path

import pandas as pd
import shioaji as sj
from dotenv import load_dotenv

from store.memory_store import MARKET_STATE
from config import CLOSE_TAIL_LEN

_ROOT = Path(__file__).resolve().parent.parent
_CACHE_DIR = _ROOT / "cache"

_FETCH_RETRIES = 3
_FETCH_RETRY_DELAY = 2.0   # 秒，每次重試前等待


def _cache_path(today: dt.date) -> Path:
    return _CACHE_DIR / f"high52w3_{today.isoformat()}.json"


def _build(api, today: dt.date) -> tuple[dict, list, dict]:
    """
    逐日抓 daily_quotes。
    回傳 ({code: [前一年最高High, 該高點最近日期ISO]},
          [交易日ISO 升冪],
          {code: [近 CLOSE_TAIL_LEN 個日收，升冪]})。
    """
    start = today - dt.timedelta(days=365)
    frames = []
    failed_dates = []
    d = today - dt.timedelta(days=1)   # 從昨天往回（排除今天）
    while d >= start:
        df = None
        last_err = None
        for attempt in range(1, _FETCH_RETRIES + 1):
            try:
                dq = api.daily_quotes(date=d)
                df = pd.DataFrame(dq.dict())
                break
            except Exception as e:
                last_err = e
                if attempt < _FETCH_RETRIES:
                    time.sleep(_FETCH_RETRY_DELAY)
        if df is not None and not df.empty and "High" in df and "Close" in df:
            frames.append(df[["Code", "Date", "High", "Close"]])
        elif last_err is not None:
            failed_dates.append(d.isoformat())
            print(f"[high52w] {d.isoformat()} daily_quotes 抓取失敗（重試 {_FETCH_RETRIES} 次）: {last_err}")
        d -= dt.timedelta(days=1)

    if failed_dates:
        print(f"[high52w] 共 {len(failed_dates)} 個交易日抓取失敗，"
              f"52 週高點表可能不完整: {failed_dates}")

    if not frames:
        return {}, [], {}
    alld = pd.concat(frames, ignore_index=True)
    alld["Date"] = alld["Date"].astype(str)

    hi = alld[alld["High"] > 0]
    mx = hi.groupby("Code")["High"].max().rename("mx")
    merged = hi.merge(mx, on="Code")
    # 高點若出現多次，取「最近一次」日期（今天突破的就是最近那次前高）
    hit_date = merged[merged["High"] == merged["mx"]].groupby("Code")["Date"].max()

    highs = {c: [float(v), hit_date.get(c, "")] for c, v in mx.items()}
    dates = sorted(hi["Date"].unique().tolist())

    # 每檔最近 CLOSE_TAIL_LEN 個日收（升冪），供均線用
    cl = alld[alld["Close"] > 0].sort_values("Date")
    tails = {
        code: [float(v) for v in g["Close"].tail(CLOSE_TAIL_LEN)]
        for code, g in cl.groupby("Code")
    }
    return highs, dates, tails


def build_high52w() -> None:
    """背景建立/載入 52 週高點表 + 日收序列，寫入 MARKET_STATE（設 high52w_ready）。"""
    today = dt.date.today()
    path = _cache_path(today)

    # 當日快取存在 → 秒載入
    if path.exists():
        try:
            data = json.loads(path.read_text())
            MARKET_STATE.high52w = data["highs"]
            MARKET_STATE.high52w_dates = data["dates"]
            MARKET_STATE.close_tails = data.get("tails", {})
            MARKET_STATE.high52w_ready = True
            print(f"[high52w] 由快取載入 {len(MARKET_STATE.high52w)} 檔 52 週高點、"
                  f"{len(MARKET_STATE.close_tails)} 檔日收序列")
            return
        except Exception:
            pass

    load_dotenv(_ROOT / ".env")
    print("[high52w] 建立全市場 52 週高點表 + 日收序列（約 1~2 分鐘，僅每日首次）…")
    api = sj.Shioaji(simulation=True)   # 獨立行情連線
    try:
        api.login(os.environ["SJ_API_KEY"], os.environ["SJ_SECRET_KEY"],
                  contracts_timeout=10_000)
        highs, dates, tails = _build(api, today)
    except Exception as e:
        print(f"[high52w] 建立失敗: {e}")
        return
    finally:
        try:
            api.logout()
        except Exception:
            pass

    if not highs:
        print("[high52w] 無資料，略過")
        return

    MARKET_STATE.high52w = highs
    MARKET_STATE.high52w_dates = dates
    MARKET_STATE.close_tails = tails
    MARKET_STATE.high52w_ready = True
    try:
        _CACHE_DIR.mkdir(exist_ok=True)
        path.write_text(json.dumps({"highs": highs, "dates": dates, "tails": tails}))
        for old in _CACHE_DIR.glob("high52w*.json"):   # 清舊快取（含舊版格式）
            if old != path:
                old.unlink()
    except Exception:
        pass
    print(f"[high52w] 完成，{len(highs)} 檔高點、{len(tails)} 檔日收序列")
