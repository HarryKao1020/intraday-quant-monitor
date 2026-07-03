# data/industry.py
"""
族群金流與漲跌幅監控（模組十）資料層。

- Industry.json：{族群名: [{company_name, stock_code}, ...]}（25 族群、~222 檔）。
- update_industry_flow()：一次 snapshots 抓全部成分股（<500 檔限制內），
  聚合出每族群的平均漲跌幅與成交金額總和，寫入 MARKET_STATE.industry_rows。
  掛在 30 秒排程與 scanner 同步更新。
- 大盤總額（加權+櫃買 total_amount）由 index_vol 取得，佔比在 compute 端計算。
"""
import datetime as dt
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from store.memory_store import MARKET_STATE
from data.session import get_api

_ROOT = Path(__file__).resolve().parent.parent
_GROUPS: dict[str, list[tuple[str, str]]] | None = None   # {族群: [(code, name)]}
_CONTRACTS: list | None = None                             # 快取合約物件
_WEEK: dict | None = None   # 週基準 {"base_close":{code:上週收}, "amt_past":{code:本週已完成日金額}}


def build_week_baseline() -> None:
    """
    建立週口徑基準（啟動時呼叫一次）：
    - 本週：amt_past（週一~昨日金額累計，今日盤中由 snapshots 即時補）、
            base_close（上週最後交易日收盤 = 週漲跌幅分母）
    - 上週：整週固定值（金額累計 + 週漲跌幅 = 上週收 vs 上上週收），
            直接聚合成 industry_rows_lastweek / detail 寫入 MARKET_STATE。
    """
    global _WEEK
    api = get_api()
    today = dt.date.today()
    monday = today - dt.timedelta(days=today.weekday())
    groups = _load_groups()
    codes = {c for members in groups.values() for c, _ in members}

    def _day(d):
        try:
            df = pd.DataFrame(api.daily_quotes(date=d).dict())
            return df[df["Code"].isin(codes)] if not df.empty else None
        except Exception:
            return None

    # ── 本週已完成日金額 ─────────────────────────────────
    amt_past: dict[str, float] = {}
    n_days = 0
    d = monday
    while d < today:
        sub = _day(d)
        if sub is not None:
            n_days += 1
            for _, r in sub.iterrows():
                amt_past[r["Code"]] = amt_past.get(r["Code"], 0.0) + float(r["Amount"])
        d += dt.timedelta(days=1)

    # ── 上週：金額累計 + 最後交易日收盤 ──────────────────
    lw_amt: dict[str, float] = {}
    lw_close: dict[str, float] = {}
    d = monday - dt.timedelta(days=7)
    lw_days = 0
    while d < monday:
        sub = _day(d)
        if sub is not None:
            lw_days += 1
            for _, r in sub.iterrows():
                lw_amt[r["Code"]] = lw_amt.get(r["Code"], 0.0) + float(r["Amount"])
            lw_close = {r["Code"]: float(r["Close"])
                        for _, r in sub.iterrows() if r["Close"]}
        d += dt.timedelta(days=1)

    # ── 上上週最後收盤（上週漲跌幅分母）──────────────────
    p2_close: dict[str, float] = {}
    d = monday - dt.timedelta(days=8)
    for _ in range(7):
        sub = _day(d)
        if sub is not None:
            p2_close = {r["Code"]: float(r["Close"])
                        for _, r in sub.iterrows() if r["Close"]}
            break
        d -= dt.timedelta(days=1)

    _WEEK = {"base_close": lw_close, "amt_past": amt_past}

    # ── 上週族群聚合（靜態，一次算好）────────────────────
    rows_lw, detail_lw = [], {}
    for grp, members in groups.items():
        chgs, amount, stocks = [], 0.0, []
        for code, name in members:
            amt = lw_amt.get(code)
            close = lw_close.get(code)
            if amt is None or not close:
                continue
            base = p2_close.get(code)
            chg = ((close - base) / base * 100.0) if base else None
            if chg is not None:
                chgs.append(chg)
            amount += amt
            stocks.append({"code": code, "name": name, "close": close,
                           "chg": chg if chg is not None else 0.0,
                           "amount": int(amt)})
        if not stocks:
            continue
        rows_lw.append({
            "group":   grp,
            "avg_chg": (sum(chgs) / len(chgs)) if chgs else 0.0,
            "amount":  amount,
            "n":       len(stocks),
            "n_total": len(members),
        })
        detail_lw[grp] = sorted(stocks, key=lambda x: x["amount"], reverse=True)

    MARKET_STATE.industry_rows_lastweek = rows_lw
    MARKET_STATE.industry_detail_lastweek = detail_lw
    print(f"[industry] 週基準建立：本週已完成 {n_days} 日、上週 {lw_days} 日、"
          f"上週收盤 {len(lw_close)} 檔")


def _load_groups() -> dict:
    global _GROUPS
    if _GROUPS is None:
        raw = json.loads((_ROOT / "data" / "Industry.json").read_text(encoding="utf-8"))
        _GROUPS = {
            grp: [(m["stock_code"], m["company_name"]) for m in members]
            for grp, members in raw.items()
        }
    return _GROUPS


def _contracts(api) -> list:
    """全部成分股合約（去重、快取；找不到的代碼略過並提示一次）。"""
    global _CONTRACTS
    if _CONTRACTS is None:
        codes = {c for members in _load_groups().values() for c, _ in members}
        found, missing = [], []
        for c in sorted(codes):
            try:
                x = api.Contracts.Stocks[c]   # 不存在會拋 Contract not found
            except Exception:
                x = None
            (found if x is not None else missing).append(x or c)
        _CONTRACTS = found
        if missing:
            print(f"[industry] 找不到合約，略過: {missing}")
    return _CONTRACTS


def update_industry_flow() -> None:
    """抓成分股 snapshots，聚合每族群 平均漲跌幅 / 成交金額總和。"""
    api = get_api()
    groups = _load_groups()
    try:
        snaps = api.snapshots(_contracts(api))
    except Exception as e:
        print(f"[industry] snapshots 失敗: {e}")
        return

    by_code = {s.code: s for s in snaps}
    rows = []
    detail: dict[str, list] = {}
    for grp, members in groups.items():
        chgs, amount, stocks = [], 0, []
        for code, name in members:
            s = by_code.get(code)
            if s is None or not s.close:
                continue   # 無資料成分股略過
            chgs.append(float(s.change_rate))
            amount += int(s.total_amount)
            stocks.append({
                "code":   code,
                "name":   name,
                "close":  float(s.close),
                "chg":    float(s.change_rate),
                "amount": int(s.total_amount),
            })
        if not chgs:
            continue
        rows.append({
            "group":   grp,
            "avg_chg": sum(chgs) / len(chgs),   # 平均漲跌幅 %
            "amount":  amount,                  # 成交金額總和（元）
            "n":       len(chgs),               # 有效成分股數
            "n_total": len(members),
        })
        detail[grp] = sorted(stocks, key=lambda x: x["amount"], reverse=True)

    MARKET_STATE.industry_rows = rows
    MARKET_STATE.industry_detail = detail   # 模組十 modal：成分股明細

    # ── 本週口徑（週漲跌幅 = 現價 vs 上週收；週金額 = 本週已完成日 + 今日盤中）──
    week = _WEEK or {"base_close": {}, "amt_past": {}}
    rows_w, detail_w = [], {}
    for grp, members in groups.items():
        chgs, amount, stocks = [], 0.0, []
        for code, name in members:
            s = by_code.get(code)
            if s is None or not s.close:
                continue
            wamt = week["amt_past"].get(code, 0.0) + float(s.total_amount)
            base = week["base_close"].get(code)
            wchg = ((float(s.close) - base) / base * 100.0) if base else None
            if wchg is not None:
                chgs.append(wchg)
            amount += wamt
            stocks.append({"code": code, "name": name, "close": float(s.close),
                           "chg": wchg if wchg is not None else 0.0,
                           "amount": int(wamt)})
        if not stocks:
            continue
        rows_w.append({
            "group":   grp,
            "avg_chg": (sum(chgs) / len(chgs)) if chgs else 0.0,
            "amount":  amount,
            "n":       len(stocks),
            "n_total": len(members),
        })
        detail_w[grp] = sorted(stocks, key=lambda x: x["amount"], reverse=True)

    MARKET_STATE.industry_rows_week = rows_w
    MARKET_STATE.industry_detail_week = detail_w
    MARKET_STATE.last_industry_update = datetime.now()
