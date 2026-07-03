# data/high52w.py
"""
建立全市場「52 週（前一年）最高價」表，供模組六判定是否創年新高。

- 用 daily_quotes 逐日抓全市場 OHLC，取每檔在 [今天-365, 昨天] 的最高 High
  及該高點「最近一次出現的日期」（供計算隔幾個交易日創高）。
- 另存年內全部交易日列表（升冪），計算間隔用。
- 每日快取到 cache/high52w2_<date>.json：當天首次建約 1~2 分鐘，之後秒載入。
- 以獨立 sim session 於背景執行，不與主連線／排程搶用 API。
"""
import datetime as dt
import json
import os
from pathlib import Path

import pandas as pd
import shioaji as sj
from dotenv import load_dotenv

from store.memory_store import MARKET_STATE

_ROOT = Path(__file__).resolve().parent.parent
_CACHE_DIR = _ROOT / "cache"


def _cache_path(today: dt.date) -> Path:
    return _CACHE_DIR / f"high52w2_{today.isoformat()}.json"


def _build(api, today: dt.date) -> tuple[dict, list]:
    """
    逐日抓 daily_quotes。
    回傳 ({code: [前一年最高High, 該高點最近日期ISO]}, [交易日ISO 升冪])。
    """
    start = today - dt.timedelta(days=365)
    frames = []
    d = today - dt.timedelta(days=1)   # 從昨天往回（排除今天）
    while d >= start:
        try:
            dq = api.daily_quotes(date=d)
            df = pd.DataFrame(dq.dict())
            if not df.empty and "High" in df:
                frames.append(df[["Code", "Date", "High"]])
        except Exception:
            pass
        d -= dt.timedelta(days=1)

    if not frames:
        return {}, []
    alld = pd.concat(frames, ignore_index=True)
    alld = alld[alld["High"] > 0]
    alld["Date"] = alld["Date"].astype(str)

    mx = alld.groupby("Code")["High"].max().rename("mx")
    merged = alld.merge(mx, on="Code")
    # 高點若出現多次，取「最近一次」日期（今天突破的就是最近那次前高）
    hit_date = merged[merged["High"] == merged["mx"]].groupby("Code")["Date"].max()

    highs = {c: [float(v), hit_date.get(c, "")] for c, v in mx.items()}
    dates = sorted(alld["Date"].unique().tolist())
    return highs, dates


def build_high52w() -> None:
    """背景建立/載入 52 週高點表，寫入 MARKET_STATE（設 high52w_ready）。"""
    today = dt.date.today()
    path = _cache_path(today)

    # 當日快取存在 → 秒載入
    if path.exists():
        try:
            data = json.loads(path.read_text())
            MARKET_STATE.high52w = data["highs"]
            MARKET_STATE.high52w_dates = data["dates"]
            MARKET_STATE.high52w_ready = True
            print(f"[high52w] 由快取載入 {len(MARKET_STATE.high52w)} 檔 52 週高點")
            return
        except Exception:
            pass

    load_dotenv(_ROOT / ".env")
    print("[high52w] 建立全市場 52 週高點表（約 1~2 分鐘，僅每日首次）…")
    api = sj.Shioaji(simulation=True)   # 獨立行情連線
    try:
        api.login(os.environ["SJ_API_KEY"], os.environ["SJ_SECRET_KEY"],
                  contracts_timeout=10_000)
        highs, dates = _build(api, today)
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
    MARKET_STATE.high52w_ready = True
    try:
        _CACHE_DIR.mkdir(exist_ok=True)
        path.write_text(json.dumps({"highs": highs, "dates": dates}))
        for old in _CACHE_DIR.glob("high52w*.json"):   # 清舊快取（含 v1）
            if old != path:
                old.unlink()
    except Exception:
        pass
    print(f"[high52w] 完成，{len(highs)} 檔")
