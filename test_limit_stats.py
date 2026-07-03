"""
模組四（漲跌停統計）獨立測試
─────────────────────────────
登入 → 跑一次 scanner → 印出今日漲停 / 跌停家數。
執行：python test_limit_stats.py

注意：scanner 的漲跌停是從「漲幅榜 / 跌幅榜各前 200 檔」統計而來，
若全市場漲停或跌停超過 200 檔，會被前 200 名截斷（極端行情才會發生）。
"""
from data.session import init_api, logout_api
from data.scanner import update_scanners
from compute.limit_stats import get_limit_stats


def main() -> None:
    print("=" * 50)
    print("模組四：漲跌停統計測試")
    print("=" * 50)

    init_api()

    print("\n[test] 執行 scanner 更新...")
    update_scanners()

    s = get_limit_stats()
    print("\n" + "─" * 50)
    print(f"  資料時間：{s['last_update']:%Y-%m-%d %H:%M:%S}")
    print(f"  價格分界：{s['price_threshold']:.0f} 元")
    print("─" * 50)
    print(f"  🔴 漲停總家數：{s['limit_up_total']:>3}  "
          f"(低價<50: {s['limit_up_low']}  中高價≥50: {s['limit_up_high']})")
    print(f"  🟢 跌停總家數：{s['limit_down_total']:>3}  "
          f"(低價<50: {s['limit_down_low']}  中高價≥50: {s['limit_down_high']})")
    print("─" * 50)

    logout_api()
    print("\n[test] 模組四測試完成 ✓")


if __name__ == "__main__":
    main()
