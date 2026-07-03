# data/regulatory.py
"""
處置股（api.punish）與注意股（api.notice）— 模組八/九資料來源。

- punish 回傳「目前生效中」名單：今日數 = start<=今日<=end；過去日期以
  start/end 區間回推（已結束的處置會從名單消失，過去值為近似下限）。
- notice 只回最新一天公告：歷史無法回查，故每天把數量落地到
  cache/regulatory_history.json 累積；累積不足的天數顯示 None（—）。
- 公告類資料一日一更，啟動時抓一次即可。
"""
import datetime as dt
import json
from pathlib import Path

import pandas as pd

from store.memory_store import MARKET_STATE
from data.session import get_api

_ROOT = Path(__file__).resolve().parent.parent
_HIST_PATH = _ROOT / "cache" / "regulatory_history.json"


def _load_hist() -> dict:
    try:
        return json.loads(_HIST_PATH.read_text())
    except Exception:
        return {}


def _save_hist(hist: dict) -> None:
    try:
        _HIST_PATH.parent.mkdir(exist_ok=True)
        _HIST_PATH.write_text(json.dumps(hist, ensure_ascii=False, indent=1))
    except Exception:
        pass


def _prev_trading_days(n: int) -> list[dt.date]:
    """昨日往回的 n 個交易日。優先用 high52w_dates，未就緒時退回平日推算。"""
    dates = MARKET_STATE.high52w_dates
    if dates:
        return [dt.date.fromisoformat(x) for x in dates[-n:]][::-1]   # 新到舊
    out, d = [], dt.date.today()
    while len(out) < n:
        d -= dt.timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return out


def update_regulatory() -> None:
    """抓處置/注意股，更新 MARKET_STATE.punish_list 與 reg_stats，落地當日數量。"""
    api = get_api()
    today = dt.date.today()

    try:
        pdf = pd.DataFrame(api.punish().dict())
        ndf = pd.DataFrame(api.notice().dict())
    except Exception as e:
        print(f"[regulatory] 抓取失敗: {e}")
        return

    # ── 生效中處置清單（模組九用）───────────────────────
    if not pdf.empty:
        active = pdf[(pdf["start_date"] <= today) & (pdf["end_date"] >= today)]
        MARKET_STATE.punish_list = [
            {"code": r["code"], "start": str(r["start_date"]), "end": str(r["end_date"]),
             "interval": r["interval"]}
            for _, r in active.iterrows()
        ]
    else:
        active = pdf
        MARKET_STATE.punish_list = []

    punish_today = len(MARKET_STATE.punish_list)
    notice_today = 0
    if not ndf.empty:
        latest = ndf["announced_date"].max()
        notice_today = int((ndf["announced_date"] == latest).sum())

    # ── 落地今日數量（累積歷史）─────────────────────────
    hist = _load_hist()
    hist[today.isoformat()] = {"punish": punish_today, "notice": notice_today}
    _save_hist(hist)

    def _punish_on(day: dt.date) -> int:
        """某日處置數：優先用歷史，否則以現行名單區間回推（近似）。"""
        rec = hist.get(day.isoformat())
        if rec is not None:
            return rec["punish"]
        if pdf.empty:
            return 0
        return int(((pdf["start_date"] <= day) & (pdf["end_date"] >= day)).sum())

    def _notice_on(day: dt.date):
        """某日注意數：只能靠累積歷史，沒有則 None。"""
        rec = hist.get(day.isoformat())
        return rec["notice"] if rec is not None else None

    prev5 = _prev_trading_days(5)          # 昨日往回 5 個交易日（新到舊）
    yesterday = prev5[0] if prev5 else None

    # 近五日平均：含今日的最近 5 個交易日
    p_days = [punish_today] + [_punish_on(d) for d in prev5[:4]]
    p_avg5 = sum(p_days) / len(p_days)
    n_known = [notice_today] + [v for d in prev5[:4] if (v := _notice_on(d)) is not None]
    n_avg5 = sum(n_known) / len(n_known) if n_known else None

    MARKET_STATE.reg_stats = {
        "punish": {
            "today": punish_today,
            "yesterday": _punish_on(yesterday) if yesterday else None,
            "avg5": round(p_avg5, 1),
        },
        "notice": {
            "today": notice_today,
            "yesterday": _notice_on(yesterday) if yesterday else None,
            "avg5": round(n_avg5, 1) if n_avg5 is not None else None,
        },
    }
    print(f"[regulatory] 處置 {punish_today} 檔、注意 {notice_today} 檔")
