# compute/usage_stats.py
from store.memory_store import MARKET_STATE

# 用量百分比警戒門檻
USAGE_WARN_PCT = 80.0   # 黃色警示
USAGE_ALERT_PCT = 95.0  # 紅色警示


def _fmt_bytes(n: int) -> str:
    """bytes → 人類可讀（MB / GB）。"""
    mb = n / (1024 * 1024)
    if mb >= 1024:
        return f"{mb / 1024:.2f} GB"
    return f"{mb:.1f} MB"


def get_usage() -> dict:
    """讀取 MARKET_STATE 流量用量，換算百分比與警示狀態。"""
    used = MARKET_STATE.usage_bytes
    limit = MARKET_STATE.usage_limit_bytes
    remaining = MARKET_STATE.usage_remaining_bytes
    pct = (used / limit * 100.0) if limit > 0 else 0.0

    if pct >= USAGE_ALERT_PCT:
        status = "alert"    # 🔴 即將爆量
    elif pct >= USAGE_WARN_PCT:
        status = "warn"     # 🟡 注意
    else:
        status = "ok"       # 🟢 安全

    return {
        "used_bytes":      used,
        "limit_bytes":     limit,
        "remaining_bytes": remaining,
        "used_pct":        pct,
        "connections":     MARKET_STATE.usage_connections,
        "status":          status,
        "used_human":      _fmt_bytes(used),
        "limit_human":     _fmt_bytes(limit),
        "remaining_human": _fmt_bytes(remaining),
        "last_update":     MARKET_STATE.last_usage_update,
    }
