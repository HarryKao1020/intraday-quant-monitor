# data/holdings.py
"""
依「股票期貨部位」推導要監控的個股清單，並輪詢這些個股的盤中量。

- get_stock_futures_watchlist()：正式環境唯讀查期貨部位，用 stock_fut_map.json
  把股期商品代碼對到標的個股，回傳 {stock_code: name}。查完即登出，
  不影響行情用的 simulation session（也不涉及下單）。
- update_holdings_volume()：用 snapshots 取這些個股今日累積量，寫入 STOCK_STORE
  （模組一爆量預估的即時量來源，30 秒輪詢，免 streaming）。
"""
import json
import os
from pathlib import Path

import shioaji as sj
from dotenv import load_dotenv

from store.memory_store import STOCK_STORE, MARKET_STATE
from data.session import get_api

_ROOT = Path(__file__).resolve().parent.parent
_MAP_CACHE: dict | None = None
_PROD_API: sj.Shioaji | None = None   # 常駐正式環境連線（唯讀查部位）


def _get_prod_api() -> sj.Shioaji:
    """取得常駐正式環境 session（登入一次重複用；60 秒輪詢不重複登入）。"""
    global _PROD_API
    if _PROD_API is None:
        load_dotenv(_ROOT / ".env")
        api = sj.Shioaji(simulation=False)
        api.login(
            os.environ["SJ_API_KEY"],
            os.environ["SJ_SECRET_KEY"],
            contracts_timeout=10_000,
        )
        _PROD_API = api
        print("[holdings] 正式環境部位連線已建立（常駐）")
    return _PROD_API


def logout_prod() -> None:
    """關閉常駐正式環境連線（程式結束時呼叫）。"""
    global _PROD_API
    if _PROD_API is not None:
        try:
            _PROD_API.logout()
        except Exception:
            pass
        _PROD_API = None
        print("[holdings] 正式環境部位連線已登出")


def load_stock_fut_map() -> dict:
    """載入股期商品代碼 → 標的個股對應表（{prefix: {code, name}}）。"""
    global _MAP_CACHE
    if _MAP_CACHE is None:
        _MAP_CACHE = json.loads((_ROOT / "stock_fut_map.json").read_text(encoding="utf-8"))
    return _MAP_CACHE


def _match_prefix(contract_code: str, fut_map: dict) -> str | None:
    """期貨合約代碼（如 QFFG6）→ 取前三碼商品代號（QFF）對 stock_fut_map.json。"""
    prefix = contract_code[:3]
    return prefix if prefix in fut_map else None


def _contract_info(api, code: str) -> tuple[str, int]:
    """
    查合約名稱與乘數（邏輯同 shioaji_account.py::get_contract_info）：
    大台 200、小台 50、微台 10、小型股票期貨 100 股、一般股票期貨 2000 股、
    選擇權 50 元/點。
    """
    try:
        contract = api.Contracts.Futures[code]
        if contract is not None:
            name = contract.name
            if "臺股期貨" in name or "大台指" in name:
                return name, 200
            if "小型臺指" in name or "小台指" in name:
                return name, 50
            if "微型台指" in name or "微型臺指" in name:
                return name, 10
            if "小型" in name:
                return name, 100
            return name, 2000
    except Exception:
        pass
    try:
        contract = api.Contracts.Options[code]
        if contract is not None:
            return contract.name, 50   # 選擇權 1 點 = 50 元
    except Exception:
        pass
    return code, 2000


def _collect_positions(api, positions) -> None:
    """整理期貨部位明細（名稱/口數/均價/現價/曝險/損益）寫入 MARKET_STATE。"""
    from datetime import datetime
    rows = []
    for pos in positions:
        name, mult = _contract_info(api, pos.code)
        sign = -1 if "Sell" in str(getattr(pos, "direction", "")) else 1
        qty = sign * int(pos.quantity)
        avg = float(pos.price)
        last = float(getattr(pos, "last_price", 0) or 0) or avg
        rows.append({
            "code":     pos.code,
            "name":     name,
            "qty":      qty,                          # 賣方為負
            "avg":      avg,                          # 庫存均價
            "last":     last,                         # 現價
            "exposure": last * qty * mult,            # 曝險金額（現價計）
            "cost":     avg * abs(qty) * mult,        # 成本曝險（獲利率分母）
            "pnl":      float(getattr(pos, "pnl", 0) or 0),
        })
    MARKET_STATE.fut_positions = rows
    MARKET_STATE.last_positions_update = datetime.now()


def get_stock_futures_watchlist(verbose: bool = True) -> dict[str, str]:
    """
    正式環境唯讀查詢期貨部位（常駐連線），過濾出股票期貨並對到標的個股。
    回傳 {stock_code: name}。非股期（大台/小台/選擇權）自動略過。
    連線失效會自動重連一次。
    """
    fut_map = load_stock_fut_map()

    try:
        api = _get_prod_api()
        positions = api.list_positions(api.futopt_account)
    except Exception as e:
        print(f"[holdings] 部位查詢失敗（{e}），重連中…")
        logout_prod()
        api = _get_prod_api()
        positions = api.list_positions(api.futopt_account)

    _collect_positions(api, positions)   # 模組七：庫存帳務明細
    watch: dict[str, str] = {}
    for pos in positions:
        prefix = _match_prefix(pos.code, fut_map)
        if prefix:
            info = fut_map[prefix]
            watch[info["code"]] = info["name"]
    if verbose:
        print(f"[holdings] 股期部位對應到 {len(watch)} 檔個股: "
              f"{', '.join(f'{k}{v}' for k, v in watch.items())}")
    return watch


def _apply_holdings(watch: dict[str, str]) -> tuple[set, set]:
    """
    依最新 watch 對 STOCK_STORE 做增量同步：移除已平倉、預取並加入新標的。
    回傳 (removed, added) 代碼集合。
    """
    current = set(STOCK_STORE.keys())
    wanted = set(watch.keys())

    removed = current - wanted
    for code in removed:
        STOCK_STORE.pop(code, None)
    if removed:
        print(f"[holdings] 同步移除已平倉: {sorted(removed)}")

    new = {c: watch[c] for c in (wanted - current)}
    if new:
        from data.fetcher import prefetch_all
        print(f"[holdings] 同步新增股期標的: {list(new.values())}，預取中…")
        prefetch_all(new, fetch_index=False)   # 只預取新標的，指數不重抓
        update_holdings_volume()
        print(f"[holdings] 同步新增完成: {list(new.keys())}")

    return removed, set(new)


def sync_holdings() -> None:
    """
    盤中定期重讀期貨部位，對 STOCK_STORE 做增量同步（僅交易時段執行）：
    - 新股期標的：預取（均量／曲線／MACD 日收）後加入，並立即抓一次量
    - 已平倉標的：從 STOCK_STORE 移除
    只在有變動時輸出日誌。
    """
    import datetime as dt

    now = dt.datetime.now().time()
    if now < dt.time(8, 45) or now > dt.time(13, 35):
        return   # 非交易時段不同步

    try:
        watch = get_stock_futures_watchlist(verbose=False)
    except Exception as e:
        print(f"[holdings] 同步讀取部位失敗: {e}")
        return

    _apply_holdings(watch)


def update_holdings_volume() -> None:
    """
    用 snapshots 取 STOCK_STORE 內個股的今日累積量與現價，寫入 STOCK_STORE。
    模組一爆量預估的即時量來源；30 秒輪詢一次。
    """
    codes = list(STOCK_STORE.keys())
    if not codes:
        return
    api = get_api()
    try:
        contracts = [api.Contracts.Stocks[c] for c in codes]
        contracts = [c for c in contracts if c is not None]
        snaps = api.snapshots(contracts)
        for s in snaps:
            st = STOCK_STORE.get(s.code)
            if st is not None:
                st.today_total_vol = int(s.total_volume)
                st.latest_close = float(s.close)
                st.change_rate = float(s.change_rate)   # 今日漲跌幅 %
    except Exception as e:
        print(f"[holdings] 量能更新失敗: {e}")
