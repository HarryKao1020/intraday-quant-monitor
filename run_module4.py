"""
模組四：漲跌停統計 — 每 30 秒更新的獨立監看器
─────────────────────────────────────────────
登入 → 建立高價 universe → 每 30 秒跑一次 scanner，印出三種口徑的漲跌停家數：
  1. 全市場（漲幅榜 / 跌幅榜，依 50 元分高低價）
  2. 成交值前 200 檔
  3. 高價排行前 100 檔（前一交易日收盤價最高）

執行：python run_module4.py   （Ctrl-C 結束）

說明：這支是模組四的單獨驗證／監看程式，邏輯（build_high_price_universe +
每 30 秒 update_scanners）日後可直接搬進正式的 main.py 排程。
"""
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from data.session import init_api, logout_api
from data.scanner import build_high_price_universe, update_scanners
from data.usage import update_usage
from compute.limit_stats import get_limit_stats
from compute.usage_stats import get_usage
from config import SCANNER_INTERVAL_SEC, LIMIT_PRICE_THRESHOLD, HIGH_PRICE_COUNT

_USAGE_ICON = {"ok": "🟢", "warn": "🟡", "alert": "🔴"}


def print_stats() -> None:
    s = get_limit_stats()
    thr = int(s["price_threshold"])
    print(f"\n┌─ {s['last_update']:%H:%M:%S} ─ 漲跌停統計 "
          f"────────────────────────────")
    print(f"│ 全市場    🔴 漲停 {s['limit_up_total']:>3}"
          f"（<{thr}: {s['limit_up_low']}  ≥{thr}: {s['limit_up_high']}）"
          f"   🟢 跌停 {s['limit_down_total']:>3}"
          f"（<{thr}: {s['limit_down_low']}  ≥{thr}: {s['limit_down_high']}）")
    print(f"│ 成交值前200 🔴 漲停 {s['top200_limit_up']:>3}"
          f"                  🟢 跌停 {s['top200_limit_down']:>3}")
    print(f"│ 高價前{HIGH_PRICE_COUNT:<3}  🔴 漲停 {s['highprice_limit_up']:>3}"
          f"                  🟢 跌停 {s['highprice_limit_down']:>3}")

    u = get_usage()
    icon = _USAGE_ICON.get(u["status"], "🟢")
    print(f"│ 流量 {icon} {u['used_human']} / {u['limit_human']}"
          f"（{u['used_pct']:.1f}%，剩 {u['remaining_human']}）"
          f"  連線 {u['connections']}/5")
    print(f"└──────────────────────────────────────────────────────")


def tick() -> None:
    update_scanners()
    update_usage()
    print_stats()


def main() -> None:
    print("=" * 56)
    print("模組四：漲跌停統計監看器（每 30 秒更新）")
    print("=" * 56)

    init_api()
    build_high_price_universe()

    # 先立即跑一次，再交給排程
    tick()

    sched = BackgroundScheduler(timezone="Asia/Taipei")
    sched.add_job(
        tick,
        "interval",
        seconds=SCANNER_INTERVAL_SEC,
        id="scanner",
        max_instances=1,
        coalesce=True,
    )
    sched.start()
    print(f"\n[排程] 已啟動，每 {SCANNER_INTERVAL_SEC} 秒更新一次。按 Ctrl-C 結束。")

    try:
        import time
        while True:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        print("\n[排程] 收到結束訊號，關閉中...")
    finally:
        sched.shutdown(wait=False)
        logout_api()
        print("[排程] 已結束 ✓")


if __name__ == "__main__":
    main()
