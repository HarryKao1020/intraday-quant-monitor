import os
import shioaji as sj
from dotenv import load_dotenv

load_dotenv()

_api: sj.Shioaji | None = None


def get_api() -> sj.Shioaji:
    if _api is None:
        raise RuntimeError("API 尚未初始化，請先呼叫 init_api()")
    return _api


def init_api() -> sj.Shioaji:
    global _api
    simulation = os.environ.get("SJ_SIMULATION", "true").lower() == "true"
    _api = sj.Shioaji(simulation=simulation)
    _api.login(
        api_key=os.environ["SJ_API_KEY"],
        secret_key=os.environ["SJ_SECRET_KEY"],
        contracts_timeout=10_000,
    )
    print(f"[session] 登入成功 | simulation={simulation}")
    return _api


def logout_api() -> None:
    global _api
    if _api is not None:
        _api.logout()
        _api = None
        print("[session] 已登出")
