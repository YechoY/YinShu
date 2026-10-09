"""FastAPI 版 hub 启动入口（音枢 Yinshu）。

用法（在项目根目录）：
    uv run yinshu                                        # 默认 127.0.0.1:8000（推荐，跨平台）
    uv run yinshu --host 0.0.0.0                         # 手机栖弦局域网联调
    uv run yinshu --port 8000 --config config.json
    uv run python -m hub.run_fastapi                     # 等价写法

配置与认证语义与旧版 run.py 一致（config.json / HUB_USER / 默认 admin:admin123）。
启动后浏览器打开 http://127.0.0.1:8000/ 即是可视化界面。
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

from .api import app, configure
from .api import _hash_pw   # 迁移/哈希校验
from .store import Hub


def _user_entry(name: str, info: dict, is_first: bool) -> dict:
    """账号条目归一化：明文密码 → pbkdf2 哈希；缺 role 补 admin(首个)/user。"""
    stored = str(info.get("password") or "")
    if stored and not stored.startswith("pbkdf2$"):
        stored = _hash_pw(stored)          # 明文 → 哈希
    role = str(info.get("role") or "").strip().lower()
    if role not in ("admin", "user"):
        role = "admin" if is_first else "user"
    return {
        "password": stored or _hash_pw(""),
        "space": str(info.get("space") or name).strip() or name,
        "role": role,
        "enabled": bool(info.get("enabled", True)),
        "created_at": str(info.get("created_at") or ""),
        "updated_at": str(info.get("updated_at") or ""),
    }


def load_config(path: str):
    """读取 config.json（v2/v3 兼容）→ (users, spaces, members, invites, policy)。

    v3 的 spaces/members/invites/policy 缺失时返回空段，由 api.configure() 内的
    _ensure_v3() 幂等迁移补齐并落盘为 v3（不动任何空间 pkl，同步行为逐字节不变）。
    明文密码在此哈希化（内存）；不再回写 v2，统一由 configure 落 v3。
    """
    users: Dict[str, dict] = {}
    spaces: Dict[str, dict] = {}
    members: Dict[str, dict] = {}
    invites: Dict[str, dict] = {}
    policy: Dict[str, dict] = {}

    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        first = True
        for name, info in (cfg.get("accounts") or {}).items():
            users[name] = _user_entry(name, info or {}, first)
            first = False
        spaces = cfg.get("spaces") or {}
        members = cfg.get("members") or {}
        invites = cfg.get("invites") or {}
        policy = cfg.get("policy") or {}
    elif os.environ.get("HUB_USER"):
        u = os.environ["HUB_USER"]
        users[u] = {
            "password": _hash_pw(os.environ.get("HUB_PASS", "")),
            "space": "main", "role": "admin", "enabled": True,
            "created_at": "", "updated_at": "",
        }
    else:
        users["admin"] = {
            "password": _hash_pw("admin123"),
            "space": "main", "role": "admin", "enabled": True,
            "created_at": "", "updated_at": "",
        }
    return users, spaces, members, invites, policy


def main():
    ap = argparse.ArgumentParser(description="音枢 Yinshu：FastAPI 同步服务 + 可视化界面")
    ap.add_argument("--host", default=os.environ.get("HUB_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("HUB_PORT", "8000")))
    # 默认锚定项目根（与 cwd 无关）：`uv run yinshu` 在任何目录启动都读同一份配置
    ap.add_argument("--config", default=os.environ.get("HUB_CONFIG", str(_ROOT / "config.json")))
    ap.add_argument("--store", default=os.environ.get("HUB_STORE", str(_ROOT / "data" / "spaces")))
    ap.add_argument("--no-open", action="store_true",
                    help="启动后不自动打开浏览器（服务器/无界面环境用）")
    args = ap.parse_args()

    users, spaces, members, invites, policy = load_config(args.config)
    if not users:
        print("[hub] 没有可用的账号配置（config.json / HUB_USER 均未提供）。")
        raise SystemExit(1)

    hub = Hub(args.store)
    # v2→v3 迁移在 configure() 内幂等完成（补齐 spaces/members 并落盘，不动 pkl）
    configure(hub, users, config_path=args.config,
              spaces=spaces, members=members, invites=invites, policy=policy)
    from .api import _policy as _pol
    from .adapters import REGISTRY
    user = next(iter(users))
    print("[hub] FastAPI 同步服务 音枢 已启动")
    print(f"      监听   : http://{args.host}:{args.port}")
    print(f"      账号   : {user}  /  空间 : {users[user]['space']}  /  角色 : {users[user]['role']}")
    print(f"      界面   : http://{args.host}:{args.port}/")
    print("      同步路由:")
    for _name, _d in REGISTRY.items():
        root = _d.roots[0] if _d.roots else _name
        _file = _d.files[0] if _d.files else ""
        print(f"        {_d.name:14s} → http://{args.host}:{args.port}/{root}/{_file}")
    print(f"      认证   : Basic（用户名/密码）")
    # 启动后自动打开管理界面（无界面环境用 --no-open 关闭）
    if not args.no_open:
        import socket
        import threading
        import time
        import webbrowser
        _show = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
        _url = f"http://{_show}:{args.port}/"

        def _open_when_ready():
            for _ in range(50):          # 轮询端口就绪，最多约 10 秒
                try:
                    with socket.create_connection((_show, args.port), timeout=0.5):
                        webbrowser.open(_url)
                        return
                except OSError:
                    time.sleep(0.2)

        threading.Thread(target=_open_when_ready, daemon=True).start()
        print(f"      浏览器 : 就绪后自动打开 {_url}（--no-open 可关闭）")
    # 路径统一显示为相对项目根的形式（部署位置无关，日志可移植）
    _root_s = str(_ROOT)
    _rel = lambda p: os.path.relpath(os.path.abspath(p), _root_s)
    print(f"      项目根 : {_root_s}")
    print(f"      配置   : {_rel(args.config)}")
    print(f"      数据   : {_rel(args.store)}")
    print(f"      政策   : 允许自助建空间={'是' if _pol.get('user_create_space') else '否'}"
          f"（配额 {_pol.get('max_owned_spaces')}），成员可邀请={'是' if _pol.get('member_invite') else '否'}"
          f"，邀请码注册={'是' if _pol.get('allow_self_register') else '否'}")
    print("[hub] 注意：默认账号仅本机测试；手机联调需把 host 换成局域网 IP 或隧道。")

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
