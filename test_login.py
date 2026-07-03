"""
Shioaji 登入測試
─────────────────
驗證 .env 金鑰與 data/session.py 是否能成功登入並下載商品檔。
執行：python test_login.py

檢查項目：
1. init_api() 能否登入成功（simulation 由 .env 的 SJ_SIMULATION 控制）
2. 商品檔是否下載完成（能取到 config.WATCHLIST 與指數合約）
3. logout_api() 能否正常登出
"""
from data.session import init_api, logout_api
from config import WATCHLIST, TAIEX_CODE, OTC_CODE


def main() -> None:
    print("=" * 50)
    print("Shioaji 登入測試")
    print("=" * 50)

    # 1. 登入
    api = init_api()

    # 2. 驗證持股合約
    print("\n[test] 驗證持股合約：")
    for code, name in WATCHLIST.items():
        contract = api.Contracts.Stocks[code]
        if contract is None:
            print(f"  ✗ {code} {name} → 找不到合約")
        else:
            print(f"  ✓ {code} {name} → {contract.name}")

    # 3. 驗證指數合約（加權 / 櫃買）
    print("\n[test] 驗證指數合約：")
    taiex = api.Contracts.Indexs.TSE[TAIEX_CODE]
    otc   = api.Contracts.Indexs.OTC[OTC_CODE]
    print(f"  ✓ 加權 TSE[{TAIEX_CODE}] → {taiex.name if taiex else '找不到'}")
    print(f"  ✓ 櫃買 OTC[{OTC_CODE}] → {otc.name if otc else '找不到'}")

    # 4. 登出
    print()
    logout_api()
    print("\n[test] 登入測試完成 ✓")


if __name__ == "__main__":
    main()
