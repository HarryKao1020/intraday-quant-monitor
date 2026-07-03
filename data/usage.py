# data/usage.py
from datetime import datetime

from store.memory_store import MARKET_STATE
from data.session import get_api


def update_usage() -> None:
    """
    查詢當日 API 流量用量（api.usage()），寫入 MARKET_STATE。
    UsageOut 欄位：connections, bytes, limit_bytes, remaining_bytes
    流量上限依當日交易量浮動（500MB ~ 10GB）。
    """
    api = get_api()
    try:
        u = api.usage()
        MARKET_STATE.usage_bytes           = int(u.bytes)
        MARKET_STATE.usage_limit_bytes     = int(u.limit_bytes)
        MARKET_STATE.usage_remaining_bytes = int(u.remaining_bytes)
        MARKET_STATE.usage_connections     = int(u.connections)
        MARKET_STATE.last_usage_update     = datetime.now()
    except Exception as e:
        print(f"[usage] 查詢失敗: {e}")
