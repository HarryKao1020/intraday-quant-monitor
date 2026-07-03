import os
from dotenv import load_dotenv
import numpy as np
import pandas as pd
import requests
import shioaji as sj
import datetime
from datetime import date, timedelta
from collections import defaultdict, deque
from shioaji import TickFOPv1, Exchange
from shioaji import BidAskFOPv1, Exchange
from shioaji import BidAskSTKv1, Exchange
import matplotlib.pyplot as plt
# import seaborn as sns



# 載入 .env 檔案
load_dotenv()

# 讀取環境變數
api_key = os.getenv('API_KEY')
secret_key = os.getenv('SECRET_KEY')

print(f"API Key: {api_key}")
print (f"Secret Key: {secret_key}")


# 建立API物件，simulation=True是代表測試帳號
api = sj.Shioaji(simulation=False)
# api.logout()
accounts = api.login(api_key, secret_key)

api.account_balance()
print(f"銀行餘額: {api.account_balance()['acc_balance']:,}")

margin_balance = api.margin(api.futopt_account)
print(f"保證金: {margin_balance}")
print(f"權益數: {margin_balance['equity_amount']:,}")
print(f"未沖銷浮動損益: {margin_balance['future_open_position']:,}")
print(f"本日保證金餘額: {margin_balance['today_balance']:,}")
print(f"原始保證金: {margin_balance['initial_margin']:,}")
print(f"維持保證金: {margin_balance['maintenance_margin']:,}")
print(f"可動用保證金: {margin_balance['available_margin']:,}")
print(f"風險指標: {margin_balance['risk_indicator']:,}%")

# ** 計算期貨總曝險跟槓桿倍數 **

# 取得未實現損益
future_positions = api.list_positions(api.futopt_account)
future_pos_df = pd.DataFrame(p.__dict__ for p in future_positions)

# 建立合約代碼對應的資訊
def get_contract_info(code):
    """
    透過 Shioaji API 查詢合約資訊
    返回: (合約名稱, 乘數)
    
    判斷邏輯:
    1. 先查 Futures
       - 小型股票期貨: 100股
       - 一般股票期貨: 2000股
       - 小型臺指期貨: 50元/點
       - 微型台指期貨: 10元/點
    2. 找不到再查 Options
       - 選擇權: 50元/點
    """
    try:
        # 先嘗試期貨
        contract = api.Contracts.Futures[code]
        
        # 判斷是否為台指期貨系列
        if '臺股期貨' in contract.name or '大台指' in contract.name:
            return contract.name, 200
        elif '小型臺指' in contract.name or '小台指' in contract.name:
            return contract.name, 50
        elif '微型台指' in contract.name or '微型臺指' in contract.name:
            return contract.name, 10
        # 判斷股票期貨
        elif '小型' in contract.name:
            return contract.name, 100
        else:
            return contract.name, 2000
            
    except:
        try:
            # 找不到期貨,嘗試選擇權
            contract = api.Contracts.Options[code]
            return contract.name, 50  # 選擇權 1點 = 50元
        except:
            print(f"警告: 無法查詢 {code} 的合約資訊")
            return "查詢失敗", 2000

# 新增合約名稱和乘數欄位
future_pos_df[['contract_name', 'multiplier']] = future_pos_df['code'].apply(
    lambda x: pd.Series(get_contract_info(x))
)

# 計算曝險金額
future_pos_df['exposure'] = future_pos_df['price'] * future_pos_df['quantity'] * future_pos_df['multiplier']

# 計算總曝險
total_exposure = future_pos_df['exposure'].sum()
total_pnl = future_pos_df['pnl'].sum()

# 顯示詳細資訊
print("=" * 80)
print("期貨部位曝險分析")
print("=" * 80)
print(f"\n{future_pos_df[['code', 'contract_name', 'quantity', 'price', 'multiplier', 'exposure', 'pnl']]}\n")

print(f"總曝險金額: {total_exposure:,.2f} 元")
print(f"未實現損益: {total_pnl:,.2f} 元")

# 取得保證金資訊來計算槓桿倍數
margin_info = api.margin(api.futopt_account)

if margin_info and hasattr(margin_info, 'equity'):
    equity = margin_info.equity  # 權益數
    leverage = total_exposure / equity if equity > 0 else 0
    
    print(f"\n權益數: {equity:,.0f} 元")
    print(f"槓桿倍數: {leverage:.2f}x")
    
    if hasattr(margin_info, 'initial_margin'):
        print(f"原始保證金: {margin_info.initial_margin:,.0f} 元")
        print(f"保證金使用率: {(margin_info.initial_margin / equity * 100):.2f}%")

    
    # 風險提示
    print("\n風險評估:")
    if leverage > 3:
        print(f"  ⚠️  警告: 槓桿倍數 {leverage:.2f}x 較高,請注意風險控管")
    elif leverage > 2:
        print(f"  ⚡ 提醒: 槓桿倍數 {leverage:.2f}x 中等,建議留意部位")
    else:
        print(f"  ✓ 槓桿倍數 {leverage:.2f}x 相對安全")
else:
    print("\n無法取得保證金資訊,無法計算槓桿倍數")

print("=" * 80)