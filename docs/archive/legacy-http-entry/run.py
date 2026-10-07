"""hub 同步服务启动入口（旧版 http.server，保留兼容；推荐用 FastAPI 版 run_fastapi）。

用法（在项目根目录）：
    uv run python -m hub.run                 # 默认 127.0.0.1:8000
    uv run python -m hub.run --host 0.0.0.0 --port 8000 --config config.json

配置（config.json）：
    { "accounts": { "<用户名>": { "password": "<密码>", "space": "<空间id>" } } }
  未提供 config.json 时回退环境变量 HUB_USER/HUB_PASS；再回退默认账号 admin/admin123（仅本机测试）。

运行后，客户端填入：
    栖弦：http://<host>:<port>/CyShineMusic/sync-v1.json
    澜音插件：http://<host>:<port>/ceru/sync-v1.json
  认证：Basic（上面的用户名/密码）。
"""
from __future__ import annotations

# 本机系统 TEMP 不可写：先把 Python 临时目录固定到项目 tmp/，
# 避免 tempfile 探测（"blat" 探针文件）散落到当前工作目录。
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_TMP = _ROOT / "tmp"
_TMP.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(_TMP)

import argparse
import json
import os
from typing import Dict, Tuple

from .server import make_server
from .store import Hub


def load_config(path: str) -> Tuple[Dict[str, str], Dict[str, str]]:
    """→ (user->password, user->space_id)"""
    accounts: Dict[str, str] = {}
    space_of: Dict[str, str] = {}
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        for user, info in (cfg.get("accounts") or {}).items():
            accounts[user] = info.get("password", "")
            space_of[user] = info.get("space", user)
    elif os.environ.get("HUB_USER"):
        u = os.environ["HUB_USER"]
        accounts[u] = os.environ.get("HUB_PASS", "")
        space_of[u] = "main"
    else:
        # 默认账号：仅本机测试，外部部署必须改
        accounts["admin"] = "admin123"
        space_of["admin"] = "main"
    return accounts, space_of


def main():
    ap = argparse.ArgumentParser(description="playlist-sync-hub 最小同步服务")
    ap.add_argument("--host", default=os.environ.get("HUB_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("HUB_PORT", "8000")))
    ap.add_argument("--config", default=os.environ.get("HUB_CONFIG", "config.json"))
    ap.add_argument("--store", default=str(_ROOT / "data" / "spaces"))
    args = ap.parse_args()

    accounts, space_of = load_config(args.config)
    if not accounts:
        print("[hub] 没有可用的账号配置（config.json / HUB_USER 均未提供）。")
        raise SystemExit(1)

    hub = Hub(args.store)
    srv = make_server(args.host, args.port, hub, accounts, space_of)
    user = next(iter(accounts))
    print("[hub] 同步服务已启动")
    print(f"      监听   : http://{args.host}:{args.port}")
    print(f"      账号   : {user}  /  空间 : {space_of.get(user)}")
    print(f"      栖弦   : http://{args.host}:{args.port}/CyShineMusic/sync-v1.json")
    print(f"      澜音   : http://{args.host}:{args.port}/ceru/sync-v1.json")
    print(f"      认证   : Basic（用户名/密码）")
    print(f"      数据   : {os.path.abspath(args.store)}")
    print("[hub] 注意：默认账号仅本机测试；手机联调需把 host 换成局域网 IP 或隧道。")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[hub] 已停止")
        srv.shutdown()


if __name__ == "__main__":
    main()
