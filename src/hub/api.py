"""FastAPI 版同步服务 + AList 式可视化界面（音枢 Yinshu 合并枢纽）。

保留原 server.py 的同步端点与语义（客户端零改动）：
  GET/PUT /<方言根>/<文件名>  → 按 REGISTRY 循环注册（栖弦 / 澜音插件 / 洛雪）
  MKCOL /<方言根>（目录路径） → 201 幂等（客户端启动探测）
新增可视化：
  GET /                  → AList 式 Web UI（登录页）
  GET /api/state         → 歌单/歌曲/客户端/同步日志（只读渲染，不推进交付基线）

认证：同步端点沿用 Basic（客户端兼容）；UI 前端同样带 Basic 头（localStorage 保存）。
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import os
import re
import time
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import adapters
from .adapters import REGISTRY, RenderContext
from .store import Hub

# 项目根（src/hub/api.py → 上溯三级）与前端目录
_ROOT = Path(__file__).resolve().parents[2]
_WEBUI = _ROOT / "webui"

# 路径 → 方言（第六轮 §1：单一来源 = 适配器包 REGISTRY，按 Dialect.roots/files 循环注册）
# _ROUTES/_PARSE/_RENDER 三表已删除；路由注册见文件尾部 _register_dialect_routes()。

app = FastAPI(title="yinshu", docs_url=None, redoc_url=None, openapi_url=None)
# Vue 工程化构建产物（webui/dist/assets）
# 注意：dist/ 是构建产物、被 .gitignore 忽略，全新克隆或未构建的部署里可能不存在。
# StaticFiles(check_dir=True) 在 import 期就会抛 RuntimeError，那样连 "/" 上的友好提示
# （webui/dist/index.html 缺失，请先 npm run build）都到不了，所以先探测再挂载。
_ASSETS_DIR = _WEBUI / "dist" / "assets"
if _ASSETS_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_ASSETS_DIR)), name="assets")

_hub: Optional[Hub] = None
# 账号模型（v3）：name -> {password: 哈希, space(当前生效空间), role: "admin"|"user", enabled, created_at, updated_at}
_users: Dict[str, dict] = {}
_config_path: Optional[str] = None

# ---- v3：空间与成员体系（docs/12） --------------------------------------
# _spaces[space_id]   = {name(=space_id 本期), kind: personal|shared, created_at, created_by}
# _members[space_id]  = {account: {role: owner|editor|viewer, joined_at, invited_by}}
# _invites[code]      = {space, role: editor|viewer, created_by, created_at, expires_at,
#                        max_uses, uses, note, disabled}
# 账号可属于多个空间（成员表多对多），但 accounts[*].space 指针决定"当前生效空间"
# （同步端点地址里没有空间，这是硬约束）。
_spaces: Dict[str, dict] = {}
_members: Dict[str, Dict[str, dict]] = {}
_invites: Dict[str, dict] = {}
_policy: dict = {}

DEFAULT_POLICY = {
    "user_create_space": True,    # 普通账号可自助新建空间（纯增量，不碰别人数据）
    "max_owned_spaces": 3,        # 普通账号建空间配额（全局 admin 不限）
    "allow_self_register": True,  # 允许凭邀请码自助注册（公网部署前应关闭）
    "member_invite": False,       # 仅 owner/管理员可发邀请码；True 时 editor 也可
    # D34：墓碑/冷静期/安全阀全拆除——删除就是删除，加回来就是加回来
}
_SPACE_ROLES = ("owner", "editor", "viewer")
_ROLE_RANK = {"viewer": 1, "editor": 2, "owner": 3}
_ROLE_LABEL = {"owner": "空间管理员", "editor": "可编辑成员", "viewer": "只读成员"}


# ---- 密码哈希（config.json 禁止明文） -----------------------------------
# 存储格式：pbkdf2$<iterations>$<salt_b64>$<hash_b64>（sha256 + 随机盐）
_PBKDF2_PREFIX = "pbkdf2$"
_PBKDF2_ITERS = 120_000


def _hash_pw(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERS)
    return f"{_PBKDF2_PREFIX}{_PBKDF2_ITERS}${base64.b64encode(salt).decode('ascii')}${base64.b64encode(dk).decode('ascii')}"


def _verify_pw(password: str, stored: str) -> bool:
    """校验密码。新格式为 pbkdf2 哈希；旧格式（明文）做兼容比较。"""
    if not stored:
        return False
    if stored.startswith(_PBKDF2_PREFIX):
        try:
            _, iters_s, salt_b64, hash_b64 = stored.split("$")
            dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                     base64.b64decode(salt_b64), int(iters_s))
            return hmac.compare_digest(base64.b64encode(dk).decode("ascii"), hash_b64)
        except Exception:
            return False
    return hmac.compare_digest(password, stored)   # 旧明文兼容（读到时会被迁移）


def configure(hub: Hub, users: Dict[str, dict],
              config_path: Optional[str] = None,
              spaces: Optional[Dict[str, dict]] = None,
              members: Optional[Dict[str, Dict[str, dict]]] = None,
              invites: Optional[Dict[str, dict]] = None,
              policy: Optional[dict] = None) -> None:
    """users: name -> {password(哈希), space, role, enabled, created_at, updated_at}

    v3 额外段（spaces/members/invites/policy）缺省时从 users 幂等迁移（v2→v3），
    保证老调用方（只传 hub/users）行为不变。
    """
    global _hub, _users, _config_path, _spaces, _members, _invites, _policy
    _hub, _users = hub, users
    _config_path = config_path
    _spaces = dict(spaces or {})
    _members = {sp: dict(ms) for sp, ms in (members or {}).items()}
    _invites = dict(invites or {})
    _policy = dict(DEFAULT_POLICY)
    _policy.update(policy or {})
    # 第八轮：让 Hub 在每次按需加载/探测空间后立刻套用 policy（否则后加载的空间
    # 拿不到全局 policy，引擎策略字段会一直是缺省值）
    if _hub is not None:
        _hub.policy_hook = _apply_engine_policy_to
    _ensure_v3()
    _apply_engine_policy()


def _apply_engine_policy_to(eng) -> None:
    """把全局 policy 里的引擎策略套到**单个**引擎上（第八轮）。"""
    if eng is None:
        return
    # D34：墓碑/冷静期全拆除
    pass


def _apply_engine_policy() -> None:
    """把 policy 里的引擎策略应用到已加载的所有空间引擎（D34 后无引擎策略，保留入口）。"""
    if _hub is None:
        return
    for rt in _hub._runtimes.values():
        _apply_engine_policy_to(rt.engine)


def _ensure_v3() -> bool:
    """从 accounts 幂等补齐 spaces/members（v2→v3，§2）。返回是否发生了补齐。

    角色规则：**空间的首个创建/归属账号即 owner**（同名 personal 当然是 owner），
    之后加入的为 editor；这样每个在用空间天然有 owner，又不会把全局 admin 硬塞进
    与他无关的空间（否则账号删尽后空间永远挂着 admin、无法成为孤儿）。
    不变量：① accounts[*].space 指向存在的 spaces 键且账号在 members[该空间]；
    ② 每个有成员的空间至少一个 owner（有成员无 owner 时提升首个成员）。
    """
    if not _users:
        return False
    changed = False
    for acc, meta in _users.items():
        sp = meta.get("space") or acc
        if sp not in _spaces:
            _spaces[sp] = {
                "name": sp,
                "kind": "personal" if sp == acc else "shared",
                "created_at": meta.get("created_at") or _now_iso(),
                "created_by": acc,
            }
            changed = True
        ms = _members.setdefault(sp, {})
        if acc not in ms:
            # 该空间此前无成员 → 当前账号是创建者，即 owner；否则按"同名 owner / 其余 editor"
            is_first = not ms
            ms[acc] = {
                "role": "owner" if (is_first or sp == acc) else "editor",
                "joined_at": meta.get("created_at") or _now_iso(),
                "invited_by": "",
            }
            # 第三轮 §3.2：不变量①异常（账号 space 不在其成员表）被幂等补齐时记中性标签
            # member.autofill（此前叫 invariant.repair，被 _persist_accounts→_ensure_v3 反复触发，
            # 正常建号也写"不变量修复"会误导——自动填充是常态维护而非错误修复）
            _audit("member.autofill", acc, sp, "账号 space 不在成员表，自动补齐（不变量①维护）")
            changed = True
    # 兜底：有成员但缺 owner（异常数据）→ 提升首个成员；无成员则留空（磁盘空间成孤儿）
    for sp, ms in _members.items():
        if ms and not any(m.get("role") == "owner" for m in ms.values()):
            first = sorted(ms.keys())[0]
            ms[first]["role"] = "owner"
            changed = True
    if changed and _config_path:
        _persist_config()
    return changed


def _atomic_write_json(path: str, obj) -> None:
    """原子写 JSON（tmp + fsync + os.replace），并滚动 .bak1/.bak2。

    备份统一收进配置文件同级的 backups/ 目录（不散落在根目录）；
    backups/ 已在 .gitignore 中（config 快照含密码哈希，不入库）。"""
    import shutil as _shutil
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    _bak_dir = os.path.join(os.path.dirname(os.path.abspath(path)), "backups")
    os.makedirs(_bak_dir, exist_ok=True)
    _base = os.path.basename(path)
    bak1 = os.path.join(_bak_dir, f"{_base}.bak1")
    bak2 = os.path.join(_bak_dir, f"{_base}.bak2")
    if os.path.exists(bak1):
        _shutil.copyfile(bak1, bak2)
    if os.path.exists(path):
        _shutil.copyfile(path, bak1)
    os.replace(tmp, path)


def _admin_users() -> list:
    """全部管理员账号名（按配置顺序）。"""
    return [u for u, meta in _users.items() if meta.get("role") == "admin"]


def _admin_user() -> str:
    """首个管理员（管理接口默认管理员）。"""
    admins = _admin_users()
    return admins[0] if admins else ""


def _is_admin(user: str) -> bool:
    return bool(user) and _users.get(user, {}).get("role") == "admin"


def _space_of(user: str) -> str:
    """账号当前生效空间（保持 v2 语义：缺省与账号同名）。"""
    return (_users.get(user, {}).get("space") or user) if user else ""


def _role_in(user: str, space: str) -> Optional[str]:
    """账号在某空间的角色：全局 admin 视同 owner；否则查成员表；非成员 → None。"""
    if not user:
        return None
    if _is_admin(user):
        return "owner"
    return (_members.get(space) or {}).get(user, {}).get("role")


def _require_role(user: str, space: str, need: str):
    """空间角色闸门。need ∈ read|write|owner；通过返回 None，否则返回 403 响应。

    read：成员即可（viewer 可拉取）；write：editor/owner（同步 PUT、网页改歌单、删）；
    owner：空间管理员（成员管理、邀请码、删空间）。
    """
    role = _role_in(user, space)
    if role is None:
        return JSONResponse({"error": "你不是该空间成员，无权访问（加入空间需有效邀请码）"},
                            status_code=403)
    ok = (need == "read"
          or (need == "write" and _ROLE_RANK[role] >= _ROLE_RANK["editor"])
          or (need == "owner" and _ROLE_RANK[role] >= _ROLE_RANK["owner"]))
    if ok:
        return None
    hint = {"write": "写入（同步/改歌单/删除）", "owner": "空间管理（成员/邀请码/删空间）"}.get(need, need)
    return JSONResponse(
        {"error": f"只读/权限不足：{_ROLE_LABEL.get(role, role)}无权{hint}"},
        status_code=403)


def _audit(action: str, actor: str, space: str = "", detail: str = "") -> dict:
    """审计日志：追加一行 JSON 到 <store_dir>/_audit.jsonl（成员/邀请/空间变更）。"""
    rec = {"ts": _now_iso(), "action": action, "actor": actor,
           "space": space, "detail": detail}
    if _hub is not None:
        try:
            with open(os.path.join(_hub.store_dir, "_audit.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass
    return rec


def _audit_tail(limit: int = 30) -> list:
    if _hub is None:
        return []
    path = os.path.join(_hub.store_dir, "_audit.jsonl")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
        out = []
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
        return out
    except Exception:
        return []


def _user_view(name: str) -> dict:
    meta = _users[name]
    return {
        "name": name,
        "space": meta.get("space", name),
        "role": meta.get("role", "user"),
        "enabled": bool(meta.get("enabled", True)),
        "created_at": meta.get("created_at", ""),
        "updated_at": meta.get("updated_at", ""),
        "space_quota": meta.get("space_quota"),   # null = 跟随全局 max_owned_spaces
        "admin": meta.get("role") == "admin",   # 兼容旧前端字段
    }


# ---- v3 空间 / 成员 / 邀请码辅助 ---------------------------------------
_SPACE_NAME_RE = re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fa5]{1,32}$")


def _valid_space_name(name: str) -> bool:
    """空间名校验：1-32 位中英文/数字/_/-；禁止路径分隔与保留前缀。"""
    if not name or not _SPACE_NAME_RE.fullmatch(name):
        return False
    if name in (".", "..") or name.startswith("space__"):
        return False
    return True


def _space_exists(name: str) -> bool:
    """存在性 = spaces 表 / 账号当前指向 / 磁盘 pkl（与列表口径一致）。"""
    if name in _spaces:
        return True
    if any((m.get("space") or n) == name for n, m in _users.items()):
        return True
    return _hub is not None and name in _hub.list_spaces()


def _name_available(name: str) -> bool:
    """新建/改名时的名字可用性（C2+C4 合并）：name 不得是任何空间 id
    （含磁盘孤儿 id 与账号 space 指针），也不得是任何空间的展示名。
    否则 A 把空间改名为 family 后，他人新建/改名撞名会劫持 _find_space。"""
    if name in _spaces:
        return False
    if any(m.get("name") == name for m in _spaces.values()):
        return False
    if any((m.get("space") or n) == name for n, m in _users.items()):
        return False
    if _hub is not None and name in _hub.list_spaces():
        return False
    return True


def _find_space(token: str) -> Optional[str]:
    """把空间路由的路径/body 参数解析成稳定 id（第三轮 §2：id 不变、展示名可改）。

    token 命中 id（spaces 表 / 账号指向 / 磁盘孤儿）→ 直接返回该 id；
    否则在 _spaces 的展示名里**唯一命中** → 返回其 id；命中多个/无命中 → None（调用方回 404/409）。
    传 id 永远有效（老前端/老脚本不受影响）。
    """
    if not token:
        return None
    if _space_exists(token):
        return token
    hits = [sp for sp, meta in _spaces.items() if meta.get("name") == token]
    return hits[0] if len(hits) == 1 else None


def _add_membership(space: str, account: str, role: str,
                    invited_by: str = "", joined_at: Optional[str] = None) -> None:
    """加入空间（已存在则不降级、不覆盖更高角色）。"""
    ms = _members.setdefault(space, {})
    cur = ms.get(account)
    if cur is not None:
        if _ROLE_RANK.get(role, 2) > _ROLE_RANK.get(cur.get("role"), 2):
            cur["role"] = role      # 仅升级，不降级
        return
    ms[account] = {"role": role if role in _SPACE_ROLES else "editor",
                   "joined_at": joined_at or _now_iso(),
                   "invited_by": invited_by}


def _ensure_personal_space(account: str) -> str:
    """确保账号有同名个人空间且为 owner（leave/被移除后回退用）。返回空间名。"""
    sp = account
    if sp not in _spaces:
        _spaces[sp] = {"name": sp, "kind": "personal",
                       "created_at": _now_iso(), "created_by": account}
    _add_membership(sp, account, "owner")
    return sp


def _reset_to_personal(account: str) -> str:
    """把账号当前空间指针切回其个人空间（不存在则自动建并设 owner）。"""
    sp = _ensure_personal_space(account)
    if account in _users and _users[account].get("space") != sp:
        _users[account]["space"] = sp
        _users[account]["updated_at"] = _now_iso()
    return sp


def _account_name_ok(name: str) -> bool:
    """账号名校验：1-32 位，只允许中英文与数字（2026-10-07 收紧：去掉 _/-）。"""
    return bool(name) and bool(re.fullmatch(r"[A-Za-z0-9\u4e00-\u9fa5]{1,32}", name))


def _count_owned(account: str) -> int:
    """该账号作为 owner 的空间数（配额口径）。"""
    return sum(1 for ms in _members.values()
               if ms.get(account, {}).get("role") == "owner")


def _quota_of(account: str) -> Optional[int]:
    """账号建空间配额：账号级 space_quota（0-1000）覆盖全局 policy；admin 不限（None）。"""
    if _is_admin(account):
        return None
    q = _users.get(account, {}).get("space_quota")
    if q is not None:
        try:
            qv = int(q)
        except (TypeError, ValueError):
            qv = -1
        if qv >= 0:
            return qv
    return int(_policy.get("max_owned_spaces", 3))


def _space_owners(space: str) -> list:
    return [a for a, m in (_members.get(space) or {}).items()
            if m.get("role") == "owner"]


def _gen_invite_code() -> str:
    import secrets as _secrets
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # 去除易混 0/O/1/I
    while True:
        code = "".join(_secrets.choice(alphabet) for _ in range(8))
        if code not in _invites:
            return code


def _invite_status(inv: dict) -> Optional[str]:
    """返回失效原因；有效返回 None。"""
    if not inv or inv.get("disabled"):
        return "邀请码不存在或已撤销"
    try:
        exp = datetime.datetime.fromisoformat(inv["expires_at"])
        if datetime.datetime.now(exp.tzinfo) > exp:
            return "邀请码已过期"
    except Exception:
        return "邀请码已过期"
    if int(inv.get("uses", 0)) >= int(inv.get("max_uses", 1)):
        return "邀请码已用完"
    return None


def _prune_invites() -> None:
    """删除所有过期、用完、已撤销的邀请码。每次持久化前调用。"""
    dead = [code for code, inv in _invites.items() if _invite_status(inv) is not None]
    for code in dead:
        _invites.pop(code, None)
    if dead:
        _log(f"清理过期邀请码 {len(dead)} 个: {dead}")


def _space_summary(space: str, viewer: str) -> dict:
    """GET /api/spaces 与 /api/me 共用的空间条目（不含歌单内容）。"""
    rt = _hub.probe(space) if _hub is not None else None
    eng = rt.engine if rt is not None else None
    referenced = {n for n, m in _users.items() if (m.get("space") or n) == space}
    members = [
        {"name": a, "role": m.get("role", "editor"),
         "joined_at": m.get("joined_at", ""), "invited_by": m.get("invited_by", "")}
        for a, m in (_members.get(space) or {}).items()
    ]
    meta = _spaces.get(space) or {}
    return {
        "space": space,
        "name": meta.get("name", space),
        "kind": meta.get("kind", "shared"),
        "my_role": _role_in(viewer, space),
        "members": members,
        "playlists": len([p for p in eng.playlists.values() if p.deleted_at is None]) if eng else 0,
        "revision": eng.revision if eng else 0,
        "exists": _space_exists(space),
        "orphan": space not in _spaces and not referenced and (rt is not None),
        "is_current": _space_of(viewer) == space,
    }


def _persist_accounts() -> None:
    """薄包装：账号变更后保证 v3 成员表一致并落盘（保留旧调用点不遗漏）。"""
    _ensure_v3()
    _persist_config()


def _persist_config() -> None:
    """把内存中的 v3 配置（accounts/spaces/members/invites/policy）原子写回 config.json。

    v3 schema 见 docs/12；密码一律 pbkdf2 哈希落盘，绝不明文；原子写 + .bak1/.bak2 滚动。
    未配置路径时仅内存生效。
    """
    if not _config_path:
        return
    cfg = {}
    if os.path.exists(_config_path):
        try:
            with open(_config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}
    cfg["version"] = 3
    cfg["accounts"] = {
        u: {
            # 内存中的 password 一律已是哈希（load_config 迁移 / 新增 / 改密都哈希化），
            # 直接原样落盘，禁止再次哈希（否则双哈希导致认证失效）。
            "password": meta["password"],
            "space": meta.get("space", u),
            "role": meta.get("role", "user"),
            "enabled": bool(meta.get("enabled", True)),
            "created_at": meta.get("created_at", ""),
            "updated_at": meta.get("updated_at", ""),
            "space_quota": meta.get("space_quota"),   # null = 跟随全局 max_owned_spaces
        }
        for u, meta in _users.items()
    }
    cfg["spaces"] = json.loads(json.dumps(_spaces, ensure_ascii=False))
    cfg["members"] = json.loads(json.dumps(_members, ensure_ascii=False))
    # D34：自动清理过期/用完/撤销的邀请码，避免 config.json 越堆越多
    _prune_invites()
    cfg["invites"] = json.loads(json.dumps(_invites, ensure_ascii=False))
    cfg["policy"] = dict(_policy)
    try:
        _atomic_write_json(_config_path, cfg)
    except Exception as exc:
        _log(f"写回 config.json 失败（变更仅内存生效）: {exc}")


def _now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _canonical_etag(obj) -> str:
    from engine.canonical import canonical_hash  # engine 路径由 hub.py 注入
    return f'"{canonical_hash(obj)}"'


def _auth_user(authorization: str) -> Optional[str]:
    if not authorization or not authorization.startswith("Basic "):
        return None
    try:
        raw = base64.b64decode(authorization[6:]).decode("utf-8")
        u, _, pw = raw.partition(":")
        meta = _users.get(u)
        if meta is None or not _verify_pw(pw, meta.get("password", "")):
            return None
        if not meta.get("enabled", True):
            return None       # 已禁用账号拒绝登录（与未认证同响应）
        return u
    except Exception:
        return None
    return None


def _unauthorized(webdav: bool = False) -> JSONResponse:
    """认证失败响应。webdav=True 仅限 WebDAV 同步路由（栖弦/澜音插件）。

    第三轮 §1：浏览器路由（/、/api/*、/assets/*）的 401 **不带** WWW-Authenticate 挑战头，
    否则 Chrome/Edge 看到该头会弹原生 Basic 登录框（改完密码后旧凭据 401 → 原生框）。
    客户端都是预置 Authorization 头、不需要被 401 挑战，去掉挑战头对它们无影响。
    """
    headers = None
    if webdav:
        headers = {"WWW-Authenticate": 'Basic realm="yinshu"'}
    return JSONResponse(
        {"error": "未认证或凭据错误"},
        status_code=401,
        headers=headers,
    )


def _log(msg: str) -> None:
    print(f"[hub {_now_iso()}] {msg}", flush=True)


# ---- 可视化界面 ----
@app.get("/")
def index() -> HTMLResponse:
    try:
        html = (_WEBUI / "dist" / "index.html").read_text(encoding="utf-8")
    except FileNotFoundError:
        html = "<h1 style='font-family:sans-serif'>webui/dist/index.html 缺失（先在 webui 执行 npm run build）</h1>"
    resp = HTMLResponse(html)
    # 前端每次构建 index.html 文件名不变（内部引用的 js/css 带 hash），
    # 必须禁用缓存，否则浏览器一直加载旧的构建产物。
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/api/users")
def api_users(authorization: str = Header(default="")) -> JSONResponse:
    """账号列表（只读，不含密码）：管理员返回全部账号；普通账号只能看到自己。"""
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized()
    if _is_admin(user):
        return JSONResponse({"users": [_user_view(u) for u in _users]})
    return JSONResponse({"users": [_user_view(user)]})


@app.post("/api/users")
async def api_user_create(request: Request, authorization: str = Header(default="")) -> JSONResponse:
    """新增账号（管理员）：{name, password, space?, role?}。role 仅 admin 可指定。"""
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized()
    if not _is_admin(user):
        return JSONResponse({"error": "仅管理员可管理账号"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "body 必须是 JSON"}, status_code=400)
    name = str(body.get("name") or "").strip()
    password = str(body.get("password") or "")
    space = str(body.get("space") or name).strip() or name
    # P1-3：管理员打错共享空间名不得静默新建（同名个人空间隐含允许）
    if space != name and not _space_exists(space):
        return JSONResponse(
            {"error": f"空间 {space} 不存在；请先在空间管理新建，或让成员用邀请码加入"},
            status_code=404)
    role = str(body.get("role") or "user").strip().lower()
    if role not in ("admin", "user"):
        return JSONResponse({"error": "role 只能是 admin 或 user"}, status_code=400)
    if not name or not password:
        return JSONResponse({"error": "账号名与密码不能为空"}, status_code=400)
    if not _account_name_ok(name):
        return JSONResponse({"error": "账号名需为 1-32 位中英文/数字"}, status_code=400)
    if len(password) < 6:
        return JSONResponse({"error": "密码至少 6 位"}, status_code=400)
    if name in _users:
        return JSONResponse({"error": f"账号 {name} 已存在"}, status_code=409)
    now = _now_iso()
    _users[name] = {
        "password": _hash_pw(password),
        "space": space,
        "role": role,
        "enabled": True,
        "created_at": now,
        "updated_at": now,
    }
    _persist_accounts()
    _audit("account.create", user, space, f"新增账号 {name}（role={role}）")
    _log(f"账号管理: {user} 新增账号 {name} (space={space}, role={role})")
    return JSONResponse({"ok": True, "user": _user_view(name)})


@app.delete("/api/users/{name}")
def api_user_delete(name: str, authorization: str = Header(default=""),
                    purge: bool = False) -> JSONResponse:
    """删除账号（管理员）。不能删除当前登录账号，且至少保留一个管理员。

    多账户共用歌单 §3：删除后若空间变成孤儿（没有任何账号指向），**默认保留数据**
    （不删 pkl，日志说明），仅显式 `?purge=true` 时才清理空间数据。
    """
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized()
    if not _is_admin(user):
        return JSONResponse({"error": "仅管理员可管理账号"}, status_code=403)
    if name == user:
        return JSONResponse({"error": "不能删除当前登录的账号"}, status_code=400)
    if name not in _users:
        return JSONResponse({"error": f"账号 {name} 不存在"}, status_code=404)
    if len(_users) <= 1:
        return JSONResponse({"error": "至少保留一个账号"}, status_code=400)
    if _users[name].get("role") == "admin" and len(_admin_users()) <= 1:
        return JSONResponse({"error": "至少保留一个管理员账号"}, status_code=400)
    space = _users[name].get("space", name)
    del _users[name]
    # v3：从所有空间成员表移除；失去唯一 owner 且仍有成员 → 提升一名成员为 owner；
    # 成员表清空的空间从 spaces 表摘除（磁盘 pkl 保留为孤儿，admin 可在空间管理删除）。
    for sp, ms in list(_members.items()):
        ms.pop(name, None)
        if ms and not _space_owners(sp):
            new_owner = sorted(ms.keys())[0]
            ms[new_owner]["role"] = "owner"
            _audit("space.owner_transfer", user, sp, f"删号 {name} 后兜底转移给 {new_owner}")
        if not ms:
            _members.pop(sp, None)
            if sp in _spaces and not any(_space_of(n) == sp for n in _users):
                _spaces.pop(sp, None)
    _persist_config()
    # 若没有任何账号还指向该空间 → 孤儿空间：默认保留数据，仅 ?purge=true 才删
    orphan = not any(m.get("space", n) == space for n, m in _users.items())
    if orphan and _hub is not None:
        if purge:
            _hub.delete_space(space)
            _audit("account.delete_purge", user, space, f"删除账号 {name} 并清理空间")
            _log(f"账号管理: {user} 删除账号 {name}，并清理孤儿空间 {space}（?purge=true）")
        else:
            _audit("account.delete", user, space, f"删除账号 {name}，空间数据保留")
            _store_dir = getattr(_hub, "store_dir", "data/spaces") if _hub else "data/spaces"
            _log(f"账号管理: {user} 删除账号 {name}；空间 {space} 已无成员，数据保留在 {_store_dir}/")
    else:
        _audit("account.delete", user, space, f"删除账号 {name}")
        _log(f"账号管理: {user} 删除账号 {name}")
    return JSONResponse({"ok": True, "space_deleted": bool(orphan and purge),
                         "space_kept": bool(orphan and not purge)})


@app.get("/api/spaces")
def api_spaces(authorization: str = Header(default="")) -> JSONResponse:
    """空间列表（只读，**不含歌单内容**，每项见 _space_summary）：
    普通账号 → 自己是成员的全部空间（多空间成员）；admin → 表 ∪ 磁盘（含孤儿空间）。"""
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized()
    disk = _hub.list_spaces() if _hub is not None else []
    if _is_admin(user):
        names = sorted(set(_spaces.keys()) | set(disk))
    else:
        mine = {sp for sp, ms in _members.items() if user in ms}
        names = sorted(mine | {_space_of(user)})
    return JSONResponse({"spaces": [_space_summary(sp, user) for sp in names]})


@app.delete("/api/spaces/{name}")
def api_space_delete(name: str, authorization: str = Header(default="")) -> JSONResponse:
    """删除空间：该空间 owner 或全局 admin 可删；要求**除操作者外无其他成员/账号指向**。

    孤儿空间（无成员、磁盘有数据）仅全局 admin 可删（保留原空间管理语义）。
    {name} 接受 id 或展示名（_find_space 解析）；不存在 → 404。
    """
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized()
    if not _valid_space_name(name):
        return JSONResponse({"error": "非法的空间名"}, status_code=400)
    sp = _find_space(name)
    if sp is None:
        return JSONResponse({"error": f"空间 {name} 不存在"}, status_code=404)
    role = _role_in(user, sp)
    members = _members.get(sp, {})
    disk = _hub.list_spaces() if _hub is not None else []
    is_orphan = (sp not in _spaces and not members and sp in disk)
    if not _is_admin(user) and role != "owner":
        return JSONResponse({"error": "仅空间 owner 或全局管理员可删除空间"}, status_code=403)
    others = [a for a in members if a != user]
    pointing = [n for n, m in _users.items() if _space_of(n) == sp and n != user]
    blockers = sorted(set(others) | set(pointing))
    if blockers:
        return JSONResponse(
            {"error": f"空间 {sp} 仍有其他成员（{'、'.join(blockers)}），请先移除成员后再删除"},
            status_code=400)
    # 操作者自己的当前指针若在此，先切回其个人空间
    if _space_of(user) == sp:
        _users[user]["space"] = _ensure_personal_space(user)
    if _hub is not None and sp in disk:
        _hub.delete_space(sp)
    _spaces.pop(sp, None)
    _members.pop(sp, None)
    _persist_config()
    _audit("space.delete", user, sp, "孤儿" if is_orphan else "owner 删除")
    _log(f"空间管理: {user} 删除空间 {sp}")
    return JSONResponse({"ok": True, "deleted": sp})


@app.get("/api/spaces/{name}/backups")
def api_space_backups(name: str, authorization: str = Header(default="")) -> JSONResponse:
    """列出空间备份槽位（current/bak1/bak2）：空间 owner 或全局 admin。

    前端「备份与恢复」块用；每个槽位带摘要（歌单数/曲目数/歌单名/时间/大小），
    让用户在恢复前看清"会恢复成什么样"。"""
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized()
    sp = _find_space(name)
    if sp is None:
        return JSONResponse({"error": f"空间 {name} 不存在"}, status_code=404)
    deny = _require_role(user, sp, "owner")
    if deny is not None:
        return JSONResponse({"error": "仅空间管理员可查看备份"}, status_code=403)
    if _hub is None:
        return JSONResponse({"error": "存储未初始化"}, status_code=500)
    return JSONResponse({"space": sp, "slots": _hub.list_backups(sp)})


@app.post("/api/spaces/{name}/backups/{slot}/restore")
def api_space_backup_restore(name: str, slot: str,
                             authorization: str = Header(default="")) -> JSONResponse:
    """从备份槽位恢复空间数据：空间 owner 或全局 admin。

    恢复是文件互换（可逆：恢复 bak1 后原数据在 bak1 槽位，再恢复一次即撤销）；
    恢复后自动清空所有 client 同步基线，各端下一次同步走只增不删。"""
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized()
    sp = _find_space(name)
    if sp is None:
        return JSONResponse({"error": f"空间 {name} 不存在"}, status_code=404)
    deny = _require_role(user, sp, "owner")
    if deny is not None:
        return JSONResponse({"error": "仅空间管理员可恢复备份"}, status_code=403)
    if _hub is None:
        return JSONResponse({"error": "存储未初始化"}, status_code=500)
    if slot not in ("bak1", "bak2"):
        return JSONResponse({"error": "只能从 bak1 或 bak2 恢复"}, status_code=400)
    try:
        result = _hub.restore_backup(sp, slot)
    except FileNotFoundError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": f"恢复失败: {exc}"}, status_code=500)
    _audit("space.restore_backup", user, sp, f"从 {slot} 恢复（重置 {result['reset_clients']} 个 client 基线）")
    _log(f"空间管理: {user} 从 {slot} 恢复空间 {sp}")
    return JSONResponse({"ok": True, "space": sp, **result})


@app.put("/api/spaces/{name}/rename")
async def api_space_rename(name: str, request: Request,
                           authorization: str = Header(default="")) -> JSONResponse:
    """重命名空间（**展示名**）：该空间 owner 或全局 admin。id 稳定不变。

    第三轮 §2：不搬 pkl、不动 members/invites/account.space 的键，客户端地址零影响。
    {name} 接受 id 或当前展示名；new_name 需合法且不得与任何空间的 id/展示名冲突。
    """
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized()
    sp = _find_space(name)
    if sp is None:
        return JSONResponse({"error": f"空间 {name} 不存在"}, status_code=404)
    deny = _require_role(user, sp, "owner")
    if deny is not None:
        return JSONResponse({"error": "仅空间管理员可重命名"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "body 必须是 JSON"}, status_code=400)
    new_name = str(body.get("new_name") or "").strip()
    if not new_name:
        return JSONResponse({"error": "新空间名不能为空"}, status_code=400)
    if not _valid_space_name(new_name):
        return JSONResponse({"error": "非法的空间名"}, status_code=400)
    old_name = _spaces.get(sp, {}).get("name", sp)
    if new_name == old_name:
        return JSONResponse({"ok": True, "space": sp, "name": old_name})   # 幂等，不写审计
    # 新名不得等于任何空间 id（含磁盘孤儿、账号指针）、也不得是其他空间展示名（C2/C4）
    if not _name_available(new_name):
        return JSONResponse({"error": "名字已被占用"}, status_code=409)
    meta = _spaces.setdefault(sp, {})
    meta["name"] = new_name
    if "kind" not in meta:
        meta["kind"] = "shared"
    _persist_config()
    _audit("space.rename", user, sp, f"{old_name} → {new_name}")
    _log(f"空间管理: {user} 重命名空间 {sp}：{old_name} → {new_name}")
    return JSONResponse({"ok": True, "space": sp, "name": new_name})


@app.post("/api/spaces")
async def api_space_create(request: Request, authorization: str = Header(default="")) -> JSONResponse:
    """新建空间（登录即可，受 policy 控制）：普通账号受 max_owned_spaces 配额，admin 不限。

    创建者即 owner；body {"space": 名字, "current": true(默认)}，current=true 顺手切过去。
    纯增量操作（不碰别人数据），故对普通账号放开；加入他人空间仍必须走邀请码。
    """
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized()
    if not _is_admin(user) and not _policy.get("user_create_space", True):
        return JSONResponse({"error": "当前策略禁止普通账号新建空间"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "body 必须是 JSON"}, status_code=400)
    name = str(body.get("space") or "").strip()
    if not _valid_space_name(name):
        return JSONResponse({"error": "空间名需为 1-32 位中英文/数字/_/-，且不能以 space__ 开头"},
                            status_code=400)
    if not _name_available(name):
        return JSONResponse({"error": f"空间 {name} 已存在（含展示名占用）"}, status_code=409)
    if not _is_admin(user):
        cap = _quota_of(user)
        if cap is not None and _count_owned(user) >= cap:
            return JSONResponse(
                {"error": f"你拥有的空间已达上限（{cap} 个）；可请管理员调整配额"},
                status_code=409)
    if _hub is None:
        return JSONResponse({"error": "hub 未就绪"}, status_code=500)
    now = _now_iso()
    _spaces[name] = {"name": name, "kind": "shared", "created_at": now, "created_by": user}
    _members[name] = {user: {"role": "owner", "joined_at": now, "invited_by": ""}}
    _hub.get(name)
    _hub.save(name)
    switch_current = bool(body.get("current", True))
    if switch_current:
        _users[user]["space"] = name
        _users[user]["updated_at"] = now
    _persist_config()
    _audit("space.create", user, name, f"current={switch_current}")
    _log(f"空间管理: {user} 新建空间 {name}（owner；current={switch_current}）")
    return JSONResponse({"ok": True, "space": name, "current": switch_current})


@app.put("/api/users/{name}/space")
async def api_user_space(name: str, request: Request,
                         authorization: str = Header(default="")) -> JSONResponse:
    """修改账号空间（多账户共用歌单 §3）：**仅管理员**可改（含改自己）。

    普通账号不能自行加入/切换空间——否则它的同步会合并进目标空间、影响其他成员
    （栖弦 can_delete=true 甚至能删歌）；空间归属由管理员统一分配。
    目标空间**必须已存在**（probe 磁盘/内存都找不到 → 404 + 明确文案），禁止静默新建。
    """
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized()
    if not _is_admin(user):
        return JSONResponse({"error": "仅管理员可分配空间（加入/切换空间请联系管理员）"},
                            status_code=403)
    if name not in _users:
        return JSONResponse({"error": f"账号 {name} 不存在"}, status_code=404)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "body 必须是 JSON"}, status_code=400)
    new_space = str(body.get("space") or "").strip()
    if not new_space:
        return JSONResponse({"error": "空间不能为空"}, status_code=400)
    if not _valid_space_name(new_space):
        return JSONResponse({"error": "非法的空间名"}, status_code=400)
    # 目标空间必须已存在（禁止静默新建）；接受 id 或展示名，统一解析为 id
    space_token = new_space
    new_space = _find_space(new_space)
    if new_space is None:
        return JSONResponse(
            {"error": f"空间 {space_token} 不存在；请先在空间管理新建，或让成员用邀请码加入"},
            status_code=404)
    old_space = _users[name].get("space", name)
    _users[name]["space"] = new_space
    _users[name]["updated_at"] = _now_iso()
    # 多账户共用歌单 §3：切空间后，清空目标空间里该账号所有 client 的同步基线。
    # 让下一次 PUT 走 _adopt_initial（只增不删），避免客户端带着另一个空间的歌单回来时
    # 引擎把"缺席"误判为删除。
    if _hub is not None:
        try:
            rt = _hub._runtimes.get(new_space)
            if rt is not None:
                n = rt.reset_account_clients(name)
                if n:
                    _log(f"空间切换: 重置账号 {name} 在空间 {new_space} 的 {n} 个 client 基线")
        except Exception as _e:
            _log(f"空间切换: 重置 client 基线失败（不阻断）: {_e}")
    # 成员表：把账号加入目标空间（已是成员则不降级；空间无 owner 时由其补位）
    if name not in _members.setdefault(new_space, {}):
        role = "owner" if not _space_owners(new_space) else "editor"
        _members[new_space][name] = {"role": role, "joined_at": _now_iso(),
                                     "invited_by": user}
    _persist_config()
    _audit("space.assign", user, new_space, f"账号 {name}：{old_space} → {new_space}")
    _log(f"账号管理: {user} 分配账号 {name} 到空间 {old_space} -> {new_space}")
    return JSONResponse({"ok": True, "user": _user_view(name)})


@app.put("/api/users/{name}/quota")
async def api_user_quota(name: str, request: Request,
                         authorization: str = Header(default="")) -> JSONResponse:
    """设置账号建空间配额（**仅管理员**）。

    body {"space_quota": n|null}：n 为 0-1000 整数 = 该账号最多拥有（owner）几个空间；
    null / 省略 = 跟随全局 policy.max_owned_spaces。全局 admin 账号本身不限配额。
    """
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized()
    if not _is_admin(user):
        return JSONResponse({"error": "仅管理员可设置空间配额"}, status_code=403)
    if name not in _users:
        return JSONResponse({"error": f"账号 {name} 不存在"}, status_code=404)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "body 必须是 JSON"}, status_code=400)
    q = body.get("space_quota")
    if q is None or str(q).strip() == "":
        _users[name].pop("space_quota", None)   # 跟随全局
    else:
        try:
            qv = int(q)
        except (TypeError, ValueError):
            return JSONResponse(
                {"error": "space_quota 必须是 0-1000 的整数，或 null 表示跟随全局"},
                status_code=400)
        if qv < 0 or qv > 1000:
            return JSONResponse({"error": "space_quota 需为 0-1000 的整数"}, status_code=400)
        _users[name]["space_quota"] = qv
    _users[name]["updated_at"] = _now_iso()
    _persist_config()
    _audit("space.quota", user, "",
           f"账号 {name} 建空间配额 -> {_users[name].get('space_quota', '跟随全局')}")
    _log(f"账号管理: {user} 设置账号 {name} 建空间配额 = "
         f"{_users[name].get('space_quota', '跟随全局')}")
    return JSONResponse({"ok": True, "user": _user_view(name)})


# ---- v3：我（/api/me） -------------------------------------------------
@app.put("/api/policy")
async def api_policy_update(request: Request,
                            authorization: str = Header(default="")) -> JSONResponse:
    """修改全局策略（仅管理员）：body {member_invite?, user_create_space?, allow_self_register?,
    max_owned_spaces?}。第三轮 §3.4：让 owner/管理员在界面上切换「允许成员生成邀请码」。"""
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized()
    if not _is_admin(user):
        return JSONResponse({"error": "仅管理员可修改策略"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "body 必须是 JSON"}, status_code=400)
    allowed = {"member_invite": bool, "user_create_space": bool,
               "allow_self_register": bool}
    changed = []
    for k, conv in allowed.items():
        if k in body:
            _policy[k] = conv(body[k])
            changed.append(k)
    if "max_owned_spaces" in body:
        try:
            _policy["max_owned_spaces"] = max(0, min(1000, int(body["max_owned_spaces"])))
            changed.append("max_owned_spaces")
        except (TypeError, ValueError):
            return JSONResponse({"error": "max_owned_spaces 必须是整数"}, status_code=400)
    if not changed:
        return JSONResponse({"error": "没有可修改的策略字段"}, status_code=400)
    _apply_engine_policy()
    _persist_config()
    _audit("policy.update", user, "", "、".join(changed))
    _log(f"策略: {user} 修改 {', '.join(changed)}")
    return JSONResponse({"ok": True, "policy": dict(_policy)})


@app.get("/api/me")
def api_me(authorization: str = Header(default="")) -> JSONResponse:
    """当前登录账号的身份与空间清单（前端不再自己推导身份）。"""
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized()
    my_spaces = sorted({sp for sp, ms in _members.items() if user in ms}
                       | {_space_of(user)})
    cur = _space_of(user)
    return JSONResponse({
        "name": user,
        "global_role": _users[user].get("role", "user"),
        "is_admin": _is_admin(user),
        "space": cur,
        "space_name": _spaces.get(cur, {}).get("name", cur),
        "my_role": _role_in(user, cur),
        "spaces": [_space_summary(sp, user) for sp in my_spaces],
        "policy": {
            "can_create_space": bool(_policy.get("user_create_space", True)) or _is_admin(user),
            "max_owned_spaces": int(_policy.get("max_owned_spaces", 3)),
            "owned_count": _count_owned(user),
            "my_quota": _quota_of(user),          # None = 不限（admin）；否则数字
            "member_invite": bool(_policy.get("member_invite", False)),
            "allow_self_register": bool(_policy.get("allow_self_register", True)),
        },
    })


@app.post("/api/me/space")
async def api_me_switch_space(request: Request,
                              authorization: str = Header(default="")) -> JSONResponse:
    """切换我的当前空间：必须是自己已加入的空间（成员表）；加入新空间走 /api/join。"""
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized()
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "body 必须是 JSON"}, status_code=400)
    target = str(body.get("space") or "").strip()
    if not target:
        return JSONResponse({"error": "空间不能为空"}, status_code=400)
    target = _find_space(target) or target      # C3：接受展示名（解析成稳定 id）
    if _role_in(user, target) is None:
        return JSONResponse(
            {"error": "你还不是该空间成员，无法切换；请向空间管理员索取邀请码后加入"},
            status_code=403)
    old = _space_of(user)
    if old == target:
        return JSONResponse({"ok": True, "space": target})
    _users[user]["space"] = target
    _users[user]["updated_at"] = _now_iso()
    _persist_config()
    _audit("space.switch", user, target, f"{old} → {target}")
    _log(f"空间切换: {user} {old} → {target}")
    return JSONResponse({"ok": True, "space": target, "my_role": _role_in(user, target)})


@app.post("/api/me/leave")
async def api_me_leave(request: Request,
                       authorization: str = Header(default="")) -> JSONResponse:
    """退出某空间：唯一 owner 不能退（先转移 owner）；退的是当前空间则切回个人空间。"""
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized()
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "body 必须是 JSON"}, status_code=400)
    sp = str(body.get("space") or "").strip()
    sp = _find_space(sp) or sp                  # C3：接受展示名（解析成稳定 id）
    ms = _members.get(sp) or {}
    if user not in ms:
        return JSONResponse({"error": "你不在该空间中"}, status_code=404)
    if ms[user].get("role") == "owner" and len(_space_owners(sp)) <= 1:
        return JSONResponse(
            {"error": "你是该空间唯一管理员，不能退出；请先把其他成员设为管理员或删除空间"},
            status_code=400)
    ms.pop(user)
    if not ms:
        _members.pop(sp, None)
        if sp in _spaces and not any(_space_of(n) == sp for n in _users):
            _spaces.pop(sp, None)
    switched = None
    if _space_of(user) == sp:
        switched = _reset_to_personal(user)
    _persist_config()
    _audit("space.leave", user, sp, f"退出空间" + (f"，切回 {switched}" if switched else ""))
    _log(f"空间成员: {user} 退出空间 {sp}" + (f"，切回 {switched}" if switched else ""))
    return JSONResponse({"ok": True, "space": sp, "current": switched or _space_of(user)})


# ---- v3：成员管理 -------------------------------------------------------
@app.get("/api/spaces/{name}/members")
def api_space_members(name: str, authorization: str = Header(default="")) -> JSONResponse:
    """成员列表（该空间成员可读；含角色/加入时间/邀请人）。{name} 接受 id 或展示名。"""
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized()
    sp = _find_space(name)
    if sp is None:
        return JSONResponse({"error": f"空间 {name} 不存在"}, status_code=404)
    if _role_in(user, sp) is None:
        return JSONResponse({"error": "你不是该空间成员"}, status_code=403)
    members = [
        {"name": a, "role": m.get("role", "editor"), "joined_at": m.get("joined_at", ""),
         "invited_by": m.get("invited_by", ""),
         "is_current": _space_of(a) == sp}
        for a, m in (_members.get(sp) or {}).items()
    ]
    return JSONResponse({"space": sp, "space_name": _spaces.get(sp, {}).get("name", sp),
                         "members": members})


@app.put("/api/spaces/{name}/members/{account}")
async def api_space_member_role(name: str, account: str, request: Request,
                                authorization: str = Header(default="")) -> JSONResponse:
    """修改成员角色（owner/管理员）：body {role: editor|viewer}；不能降级最后一个 owner。"""
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized()
    sp = _find_space(name)
    if sp is None:
        return JSONResponse({"error": f"空间 {name} 不存在"}, status_code=404)
    deny = _require_role(user, sp, "owner")
    if deny is not None:
        return deny
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "body 必须是 JSON"}, status_code=400)
    role = str(body.get("role") or "").strip().lower()
    if role not in ("editor", "viewer"):
        return JSONResponse({"error": "角色只能是 editor 或 viewer（owner 由空间归属决定）"},
                            status_code=400)
    ms = _members.get(sp) or {}
    if account not in ms:
        return JSONResponse({"error": f"{account} 不是该空间成员"}, status_code=404)
    if ms[account].get("role") == "owner" and len(_space_owners(sp)) <= 1:
        return JSONResponse({"error": "不能降级该空间唯一的管理员"}, status_code=400)
    old = ms[account].get("role")
    ms[account]["role"] = role
    _persist_config()
    _audit("member.role", user, sp, f"{account}：{old} → {role}")
    _log(f"空间成员: {user} 将 {sp}/{account} 角色 {old} → {role}")
    return JSONResponse({"ok": True})


@app.delete("/api/spaces/{name}/members/{account}")
def api_space_member_remove(name: str, account: str,
                            authorization: str = Header(default="")) -> JSONResponse:
    """移除成员（owner/管理员）；移除自己请走 /api/me/leave。被移除者当前空间在此 → 切回个人空间。"""
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized()
    sp = _find_space(name)
    if sp is None:
        return JSONResponse({"error": f"空间 {name} 不存在"}, status_code=404)
    deny = _require_role(user, sp, "owner")
    if deny is not None:
        return deny
    if account == user:
        return JSONResponse({"error": "移除自己请用「退出空间」"}, status_code=400)
    ms = _members.get(sp) or {}
    if account not in ms:
        return JSONResponse({"error": f"{account} 不是该空间成员"}, status_code=404)
    if ms[account].get("role") == "owner" and len(_space_owners(sp)) <= 1:
        return JSONResponse({"error": "不能移除该空间唯一的管理员"}, status_code=400)
    ms.pop(account)
    switched = None
    if account in _users and _space_of(account) == sp:
        switched = _reset_to_personal(account)
    _persist_config()
    _audit("member.remove", user, sp, f"移除 {account}" + (f"，其切回 {switched}" if switched else ""))
    _log(f"空间成员: {user} 从 {sp} 移除 {account}")
    return JSONResponse({"ok": True, "current": switched})


# ---- v3：邀请码 ---------------------------------------------------------
def _invite_manager(inv: dict, user: str, sp: str) -> bool:
    """是否可管理该空间的邀请码：owner/admin；或 policy.member_invite 开启时的 editor（仅自己创建的）。"""
    role = _role_in(user, sp)
    if role == "owner":
        return True
    if role == "editor" and _policy.get("member_invite"):
        return inv is None or inv.get("created_by") == user
    return False


@app.post("/api/invites")
async def api_invite_create(request: Request,
                            authorization: str = Header(default="")) -> JSONResponse:
    """生成邀请码：owner/admin；policy.member_invite=true 时 editor 也可。
    body {space?, role="editor", expires_in_hours=24, max_uses=1, note?}。"""
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized()
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "body 必须是 JSON"}, status_code=400)
    sp = str(body.get("space") or _space_of(user)).strip()
    # 接受 id 或展示名，统一解析为 id（body.space 缺省 = 当前空间）
    sp = _find_space(sp) or sp
    if _role_in(user, sp) is None:
        return JSONResponse({"error": "你不是该空间成员"}, status_code=403)
    if not _invite_manager(None, user, sp):
        return JSONResponse({"error": "仅空间管理员可生成邀请码"}, status_code=403)
    role = str(body.get("role") or "editor").strip().lower()
    if role not in ("editor", "viewer"):
        return JSONResponse({"error": "邀请码角色只能是 editor 或 viewer"}, status_code=400)
    try:
        hours = max(1, min(720, int(body.get("expires_in_hours", 24))))
        max_uses = max(1, min(100, int(body.get("max_uses", 1))))
    except (TypeError, ValueError):
        return JSONResponse({"error": "有效期/次数必须是整数"}, status_code=400)
    now = datetime.datetime.now().astimezone()
    expires = now + datetime.timedelta(hours=hours)
    code = _gen_invite_code()
    rec = {
        "space": sp, "role": role, "created_by": user,
        "created_at": now.isoformat(timespec="seconds"),
        "expires_at": expires.isoformat(timespec="seconds"),
        "max_uses": max_uses, "uses": 0,
        "note": str(body.get("note") or "")[:60], "disabled": False,
    }
    _invites[code] = rec
    _persist_config()
    _audit("invite.create", user, sp, f"码 {code} role={role} 有效期{hours}h 次数{max_uses}")
    _log(f"邀请码: {user} 为空间 {sp} 生成 {code}（role={role}, {hours}h, ×{max_uses}）")
    return JSONResponse({"code": code, "space": sp, "role": role,
                         "expires_at": rec["expires_at"], "max_uses": max_uses})


@app.get("/api/invites")
def api_invite_list(space: str = "", authorization: str = Header(default="")) -> JSONResponse:
    """列出邀请码（含剩余次数/到期/创建者）。码只对 owner/管理员可见；editor 仅见自己创建的。"""
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized()
    sp = space or _space_of(user)
    sp = _find_space(sp) or sp
    if _role_in(user, sp) is None:
        return JSONResponse({"error": "你不是该空间成员"}, status_code=403)
    role = _role_in(user, sp)
    # P0-2：只读成员不得列码（码只对 owner 可见全部；editor 仅见自己创建的）
    if role not in ("owner", "editor"):
        return JSONResponse({"error": "只读成员无权查看邀请码"}, status_code=403)
    is_owner = role == "owner"
    out = []
    for code, inv in _invites.items():
        if inv.get("space") != sp:
            continue
        if not is_owner and inv.get("created_by") != user:
            continue
        out.append({
            "code": code, "space": sp,
            "space_name": _spaces.get(sp, {}).get("name", sp), "role": inv.get("role"),
            "created_by": inv.get("created_by"), "note": inv.get("note", ""),
            "created_at": inv.get("created_at"), "expires_at": inv.get("expires_at"),
            "max_uses": inv.get("max_uses", 1), "uses": inv.get("uses", 0),
            "remaining": max(0, int(inv.get("max_uses", 1)) - int(inv.get("uses", 0))),
            "disabled": bool(inv.get("disabled")),
            "invalid_reason": _invite_status(inv),
        })
    return JSONResponse({"space": sp, "invites": out})


@app.delete("/api/invites/{code}")
def api_invite_revoke(code: str, authorization: str = Header(default="")) -> JSONResponse:
    """撤销邀请码（创建者 / 空间 owner / 全局 admin）。"""
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized()
    inv = _invites.get(code)
    if inv is None:
        return JSONResponse({"error": "邀请码不存在"}, status_code=404)
    sp = inv.get("space")
    if not (inv.get("created_by") == user or _role_in(user, sp) == "owner"):
        return JSONResponse({"error": "无权撤销该邀请码"}, status_code=403)
    inv["disabled"] = True
    _persist_config()
    _audit("invite.revoke", user, sp, f"撤销码 {code}")
    _log(f"邀请码: {user} 撤销 {code}")
    return JSONResponse({"ok": True})


def _consume_invite(code: str):
    """校验并消费邀请码 → (inv, None) 或 (None, 中文原因)。"""
    inv = _invites.get(str(code or "").strip())
    if inv is None:
        return None, "邀请码不存在"
    reason = _invite_status(inv)
    if reason:
        return None, reason
    if not _space_exists(inv["space"]):
        return None, "邀请的空间已不存在"
    return inv, None


@app.post("/api/join")
async def api_join(request: Request, authorization: str = Header(default="")) -> JSONResponse:
    """登录账号凭邀请码加入空间：写 membership + 当前空间指针切到该空间 + 用码 +1。"""
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized()
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "body 必须是 JSON"}, status_code=400)
    inv, reason = _consume_invite(body.get("code"))
    if inv is None:
        return JSONResponse({"error": reason}, status_code=400)
    sp, role = inv["space"], inv["role"]
    # P0-2：已在该空间的成员重复 join 只切当前空间指针，不消耗邀请码（一次性码不被自己人误点耗尽）
    if user in (_members.get(sp) or {}):
        _users[user]["space"] = sp
        _users[user]["updated_at"] = _now_iso()
        _persist_config()
        return JSONResponse({"ok": True, "space": sp, "role": _role_in(user, sp),
                             "already_member": True})
    _add_membership(sp, user, role, invited_by=inv.get("created_by", ""))
    _users[user]["space"] = sp
    _users[user]["updated_at"] = _now_iso()
    inv["uses"] = int(inv.get("uses", 0)) + 1
    _persist_config()
    _audit("invite.join", user, sp, f"凭码加入，角色 {role}")
    _log(f"邀请码: {user} 凭码加入空间 {sp}（角色 {role}）")
    return JSONResponse({"ok": True, "space": sp, "role": _role_in(user, sp)})


@app.post("/api/register")
async def api_register(request: Request) -> JSONResponse:
    """自助注册（无需登录，policy.allow_self_register 开关）：邀请码即注册许可。

    body {name, password, code}：建号（全局 user、当前空间=码空间、角色取码角色、不建个人空间）。
    """
    if not _policy.get("allow_self_register", True):
        return JSONResponse({"error": "当前未开放自助注册，请联系管理员建号"}, status_code=403)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "body 必须是 JSON"}, status_code=400)
    name = str(body.get("name") or "").strip()
    password = str(body.get("password") or "")
    if not _account_name_ok(name):
        return JSONResponse({"error": "账号名需为 1-32 位中英文/数字"}, status_code=400)
    if len(password) < 6:
        return JSONResponse({"error": "密码至少 6 位"}, status_code=400)
    if name in _users:
        return JSONResponse({"error": "该账号已注册；请直接登录后用邀请码加入空间"}, status_code=409)
    inv, reason = _consume_invite(body.get("code"))
    if inv is None:
        return JSONResponse({"error": reason}, status_code=400)
    sp, role = inv["space"], inv["role"]
    now = _now_iso()
    _users[name] = {"password": _hash_pw(password), "space": sp, "role": "user",
                    "enabled": True, "created_at": now, "updated_at": now}
    _members.setdefault(sp, {})[name] = {"role": role, "joined_at": now,
                                         "invited_by": inv.get("created_by", "")}
    inv["uses"] = int(inv.get("uses", 0)) + 1
    _persist_config()
    _audit("account.register", name, sp, f"凭码自助注册，角色 {role}")
    _log(f"自助注册: {name} 凭码加入空间 {sp}（角色 {role}）")
    return JSONResponse({"ok": True, "name": name, "space": sp, "role": role})


@app.put("/api/users/{name}/password")
async def api_user_password(name: str, request: Request, authorization: str = Header(default="")) -> JSONResponse:
    """修改账号密码。管理员可改任意账号；普通账号只能改自己的（需提供旧密码）。"""
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized()
    if name not in _users:
        return JSONResponse({"error": f"账号 {name} 不存在"}, status_code=404)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "body 必须是 JSON"}, status_code=400)
    password = str(body.get("password") or "")
    if len(password) < 6:
        return JSONResponse({"error": "新密码至少 6 位"}, status_code=400)
    admin = _is_admin(user)
    if name != user and not admin:
        return JSONResponse({"error": "仅管理员可修改其他账号的密码"}, status_code=403)
    if not admin:
        old = str(body.get("old_password") or "")
        if not _verify_pw(old, _users.get(name, {}).get("password", "")):
            return JSONResponse({"error": "旧密码不正确"}, status_code=400)
    _users[name]["password"] = _hash_pw(password)
    _users[name]["updated_at"] = _now_iso()
    _persist_accounts()
    _log(f"账号管理: {user} 修改账号 {name} 的密码")
    return JSONResponse({"ok": True})


@app.put("/api/users/{name}/rename")
async def api_user_rename(name: str, request: Request, authorization: str = Header(default="")) -> JSONResponse:
    """重命名账号：管理员可改任意账号；普通账号只能重命名自己。空间与数据保留，密码不变。"""
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized()
    if not _is_admin(user) and name != user:
        return JSONResponse({"error": "普通账号只能重命名自己"}, status_code=403)
    if name not in _users:
        return JSONResponse({"error": f"账号 {name} 不存在"}, status_code=404)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "body 必须是 JSON"}, status_code=400)
    new_name = str(body.get("new_name") or "").strip()
    if not new_name:
        return JSONResponse({"error": "新账号名不能为空"}, status_code=400)
    if not _account_name_ok(new_name):
        return JSONResponse({"error": "账号名需为 1-32 位中英文/数字"}, status_code=400)
    if new_name == name:
        return JSONResponse({"ok": True})    # 无变化，幂等
    if new_name in _users:
        return JSONResponse({"error": f"账号 {new_name} 已存在"}, status_code=409)
    meta = _users.pop(name)
    meta["updated_at"] = _now_iso()
    _users[new_name] = meta
    # v3：成员表 / 空间创建人 / 邀请记录中的账号名同步迁移（空间名不随账号改名，见 docs/12）
    for ms in _members.values():
        if name in ms:
            ms[new_name] = ms.pop(name)
        # P1-5：成员条目内的 invited_by 字段值也要同步（邀请人改名后仍可追溯）
        for m in ms.values():
            if m.get("invited_by") == name:
                m["invited_by"] = new_name
    for sp_info in _spaces.values():
        if sp_info.get("created_by") == name:
            sp_info["created_by"] = new_name
    for inv in _invites.values():
        if inv.get("created_by") == name:
            inv["created_by"] = new_name
        if inv.get("invited_by") == name:
            inv["invited_by"] = new_name
    _persist_config()
    _audit("account.rename", user, meta.get("space", name), f"{name} → {new_name}")
    _log(f"账号管理: {user} 重命名账号 {name} → {new_name}（空间 {meta.get('space')} 保留）")
    return JSONResponse({"ok": True, "user": _user_view(new_name)})


@app.put("/api/users/{name}/enabled")
async def api_user_enabled(name: str, request: Request, authorization: str = Header(default="")) -> JSONResponse:
    """启用/禁用账号（管理员）：{enabled: bool}。不能禁用自己，不能禁用最后一个管理员。"""
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized()
    if not _is_admin(user):
        return JSONResponse({"error": "仅管理员可管理账号"}, status_code=403)
    if name not in _users:
        return JSONResponse({"error": f"账号 {name} 不存在"}, status_code=404)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "body 必须是 JSON"}, status_code=400)
    enabled = bool(body.get("enabled"))
    if name == user and not enabled:
        return JSONResponse({"error": "不能禁用当前登录的账号"}, status_code=400)
    if not enabled and _users[name].get("role") == "admin" and len(_admin_users()) <= 1:
        return JSONResponse({"error": "至少保留一个启用的管理员账号"}, status_code=400)
    _users[name]["enabled"] = enabled
    _users[name]["updated_at"] = _now_iso()
    _persist_accounts()
    _log(f"账号管理: {user} {'禁用' if not enabled else '启用'}账号 {name}")
    return JSONResponse({"ok": True})


@app.put("/api/users/{name}/role")
async def api_user_role(name: str, request: Request, authorization: str = Header(default="")) -> JSONResponse:
    """调整账号角色（管理员）：{role: "admin"|"user"}。不能降级最后一个管理员。"""
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized()
    if not _is_admin(user):
        return JSONResponse({"error": "仅管理员可管理账号"}, status_code=403)
    if name not in _users:
        return JSONResponse({"error": f"账号 {name} 不存在"}, status_code=404)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "body 必须是 JSON"}, status_code=400)
    role = str(body.get("role") or "").strip().lower()
    if role not in ("admin", "user"):
        return JSONResponse({"error": "role 只能是 admin 或 user"}, status_code=400)
    if role == "user" and _users[name].get("role") == "admin" and len(_admin_users()) <= 1:
        return JSONResponse({"error": "至少保留一个管理员账号"}, status_code=400)
    _users[name]["role"] = role
    _users[name]["updated_at"] = _now_iso()
    _persist_accounts()
    _log(f"账号管理: {user} 调整账号 {name} 角色为 {role}")
    return JSONResponse({"ok": True})


# ---- 歌单管理（网页端直接操作当前空间） ----
@app.post("/api/playlists/reorder")
async def api_playlists_reorder(request: Request, authorization: str = Header(default="")) -> JSONResponse:
    """重排歌单显示顺序：{pl_ids: [canonical pl_id ...]}（须恰好包含当前全部存活歌单）。"""
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized()
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "body 必须是 JSON"}, status_code=400)
    order = body.get("pl_ids")
    if not isinstance(order, list) or not all(isinstance(x, str) for x in order):
        return JSONResponse({"error": "pl_ids 必须是字符串数组"}, status_code=400)
    space = _users[user]["space"]
    deny = _require_role(user, space, "write")
    if deny is not None:
        return deny
    rt = _hub.get(space)
    with rt.lock:
        try:
            rt.engine.reorder_playlists(order, origin=f"ui:{user}")
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        _hub.save(space)
    _log(f"歌单管理: {user} 重排歌单顺序 ({len(order)} 个)")
    return JSONResponse({"ok": True})


@app.delete("/api/playlists/{pl_id}")
def api_playlist_delete(pl_id: str, authorization: str = Header(default="")) -> JSONResponse:
    """删除歌单（当前账号空间）：从引擎移除，所有客户端下次拉取时同步删除。"""
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized()
    space = _users[user]["space"]
    deny = _require_role(user, space, "write")
    if deny is not None:
        return deny
    rt = _hub.get(space)
    with rt.lock:
        try:
            rt.engine.delete_playlist(pl_id, origin=f"ui:{user}")
        except KeyError:
            return JSONResponse({"error": f"歌单 {pl_id} 不存在"}, status_code=404)
        _hub.save(space)
    _log(f"歌单管理: {user} 删除歌单 {pl_id}")
    return JSONResponse({"ok": True})


@app.post("/api/playlists/{pl_id}/tracks/reorder")
async def api_tracks_reorder(pl_id: str, request: Request, authorization: str = Header(default="")) -> JSONResponse:
    """重排歌单内歌曲顺序：{keys: [身份键 ...]}（须恰好包含该歌单当前全部歌曲）。"""
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized()
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "body 必须是 JSON"}, status_code=400)
    keys = body.get("keys")
    if not isinstance(keys, list) or not all(isinstance(x, str) for x in keys):
        return JSONResponse({"error": "keys 必须是字符串数组"}, status_code=400)
    space = _users[user]["space"]
    deny = _require_role(user, space, "write")
    if deny is not None:
        return deny
    rt = _hub.get(space)
    with rt.lock:
        try:
            rt.engine.reorder_tracks(pl_id, keys, origin=f"ui:{user}")
        except (KeyError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        _hub.save(space)
    _log(f"歌单管理: {user} 重排歌单 {pl_id} 内歌曲顺序 ({len(keys)} 首)")
    return JSONResponse({"ok": True})


@app.delete("/api/playlists/{pl_id}/tracks/{key:path}")
def api_track_delete(pl_id: str, key: str, authorization: str = Header(default="")) -> JSONResponse:
    """删除歌单内一首歌（全局删除）：从所有歌单摘除并同步传播。"""
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized()
    space = _users[user]["space"]
    deny = _require_role(user, space, "write")
    if deny is not None:
        return deny
    rt = _hub.get(space)
    with rt.lock:
        try:
            rt.engine.remove_track_from_playlist(pl_id, key, origin=f"ui:{user}")
        except KeyError:
            return JSONResponse({"error": "歌单或歌曲不存在"}, status_code=404)
        _hub.save(space)
    _log(f"歌单管理: {user} 删除歌曲 {key}（歌单 {pl_id}）")
    return JSONResponse({"ok": True})


@app.get("/api/state")
def api_state(authorization: str = Header(default="")) -> JSONResponse:
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized()
    space = _users[user]["space"]
    # P0-1：读鉴权——账号当前空间被改坏/指向非成员空间时不得越权读（不变量①之外的兜底门）
    deny = _require_role(user, space, "read")
    if deny is not None:
        return deny
    rt = _hub.get(space)
    eng = rt.engine

    # 只读渲染：用任一已注册方言身份渲染视图（canonical 视图与方言无关，
    # 只读不推进 base_served——查看不改变删除判定）。不硬编码方言名（第六轮 §1 验收）。
    cid = rt.client_for(user, next(iter(REGISTRY)))
    client = eng.clients.get(cid)
    view = {}
    if client is not None:
        view, _cleanup = eng.render_for(client)

    playlists = []
    for pl_id, e in view.items():
        pl_obj = eng.playlists.get(pl_id)
        tracks = []
        for key in e["tracks"]:
            meta = rt.meta_pool.get(key, {})
            tracks.append({
                "key": key,
                "title": meta.get("title") or key,
                "singer": meta.get("singer") or "",
                "album": meta.get("album") or "",
                # 新数据 meta 里有 source；旧数据兜底从身份键 "src:sid" 提取
                "source": meta.get("source") or key.partition(":")[0],
                "quality": meta.get("quality") or "",
                "picUrl": meta.get("pic_url") or "",
                "durationMs": meta.get("duration_ms"),
            })
        playlists.append({
            "pl_id": pl_id,
            "name": e["name"],
            "track_count": len(tracks),
            "cover": next((t["picUrl"] for t in tracks if t["picUrl"]), ""),
            "sort_tag": getattr(pl_obj, "sort_tag", 0) if pl_obj else 0,
            "updated_at": (getattr(pl_obj, "content_changed_at", None) or
                           getattr(pl_obj, "order_changed_at", None)) if pl_obj else None,
            "tracks": tracks,
        })
    # 保持引擎 playlist_order 顺序（网页端可排序），不再按名字重排

    clients = []
    for cid2, c in eng.clients.items():
        clients.append({
            "client_id": cid2,
            "dialect": getattr(c, "dialect", ""),
            "served_rev": c.base_served.served_revision if c.base_served else None,
            "last_seen": c.last_seen,
        })

    def _pl_name(pl_id: str) -> str:
        pl = rt.engine.playlists.get(pl_id)
        return pl.name if pl is not None else pl_id

    def _track_titles(keys) -> list:
        out = []
        for k in (keys or []):
            meta = rt.meta_pool.get(k, {})
            out.append(meta.get("title") or k)
        return out

    journal_out = []
    for j in eng.journal[-30:]:
        m = j.get("meta") or {}
        journal_out.append({
            "at": j.get("at"),
            "client": j.get("client"),
            "revision": j.get("revision"),
            "sort_tag": m.get("sort_tag"),
            "action": j.get("action"),          # deliver（拉取）/ reorder / playlist_delete / 合并（None）
            "mutated": m.get("mutated"),
            "reordered": m.get("reordered"),
            "added_playlists": [_pl_name(x) for x in (m.get("added_playlists") or [])],
            "removed_playlists": [_pl_name(x) for x in (m.get("removed_playlists") or [])],
            "added_tracks": {_pl_name(k): _track_titles(v) for k, v in (m.get("added_tracks") or {}).items()},
            "removed_tracks": {_pl_name(k): _track_titles(v) for k, v in (m.get("removed_tracks") or {}).items()},
            "never_owned": {"playlists": [_pl_name(x) for x in ((m.get("never_owned") or {}).get("playlists") or [])],
                            "tracks": {_pl_name(k): _track_titles(v) for k, v in ((m.get("never_owned") or {}).get("tracks") or {}).items()}},   # P0.5
        })

    # v3：当前空间成员（前端显示"谁在共用"）+ 我的空间角色
    members_out = [
        {"name": acc, "role": m.get("role", "editor"),
         "joined_at": m.get("joined_at", ""), "invited_by": m.get("invited_by", "")}
        for acc, m in (_members.get(space) or {}).items()
    ]
    # P1-4：先按空间过滤再取尾部 30——避免其他空间记录挤占本空间审计条数
    audit_all = _audit_tail(10 ** 6)
    audit = [r for r in audit_all if not r.get("space") or r.get("space") == space][-30:]

    # 平台占比统计（前端环形图用）
    from collections import Counter
    _src_counts: Counter = Counter()
    for pl_obj in eng.playlists.values():
        if pl_obj.deleted_at is not None:
            continue
        for tr_id in pl_obj.track_ids:
            tr = eng.tracks.get(tr_id)
            if tr is not None and tr.deleted_at is None:
                _src_counts[tr.source] += 1
    _SOURCE_NAME = {"tx": "QQ音乐", "wy": "网易云", "kw": "酷我", "kg": "酷狗",
                    "mg": "咪咕", "zz": "知乎", "local": "本地"}
    _SOURCE_CLASS = {"tx": "tx", "netease": "netease", "wy": "netease",
                     "kw": "kuwo", "kg": "kugou", "mg": "migu"}
    platform_stats = []
    for _src, _cnt in sorted(_src_counts.items(), key=lambda x: -x[1]):
        cls = _SOURCE_CLASS.get(_src, "other")
        platform_stats.append({
            "source": _src,
            "name": _SOURCE_NAME.get(_src, _src.upper()),
            "cls": cls,
            "count": _cnt,
        })

    return JSONResponse({
        "space": space,
        "name": _spaces.get(space, {}).get("name", space),
        "user": user,
        "my_role": _role_in(user, space),
        "is_global_admin": _is_admin(user),
        "members": members_out,
        "policy": {
            "can_create_space": bool(_policy.get("user_create_space", True)) or _is_admin(user),
            "max_owned_spaces": _policy.get("max_owned_spaces", 3),
            "member_invite": bool(_policy.get("member_invite", False)),
            "allow_self_register": bool(_policy.get("allow_self_register", True)),
        },
        "audit": audit,
        "revision": eng.revision,
        "sort_tag": eng.sort_tag,
        "generated_at": eng._now(),
        "playlists": playlists,
        "clients": clients,
        "journal": journal_out,
        "platform_stats": platform_stats,
        "meta_pool_size": len(rt.meta_pool),
    })


# ---- 同步端点（保持原语义；第六轮 §1.4 路由泛型化：按 REGISTRY 循环注册） ----
_DEVICE_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def client_identity(path_device: Optional[str], x_hub_device: Optional[str]) -> Optional[str]:
    """设备身份归一化（docs/04:50，多设备改造 §3.1）：头 > 路径段 > 无。

    - `X-Hub-Device` 头优先于路径段（M7：同时给 d-a 路径与头 b ⇒ 归到 b）；
    - 头/路径段都缺 ⇒ None（调用方退化为 `default`，兼容老空间）；
    - 非法设备名（非 [A-Za-z0-9_-]{1,32}）⇒ 抛 ValueError（路由层转 400，显式报错不静默忽略）。
    """
    raw = (x_hub_device or "").strip() or (path_device or "").strip()
    if not raw:
        return None
    if not _DEVICE_RE.fullmatch(raw):
        raise ValueError(f"设备标识非法（限 [A-Za-z0-9_-]{{1,32}}）：{raw!r}")
    return raw


def _resolve_device(device: str, x_hub_device: str):
    """路由层包装：归一化失败返回 (None, 400 响应)；成功返回 (device, None)。"""
    try:
        return client_identity(device, x_hub_device), None
    except ValueError as exc:
        return None, JSONResponse({"error": str(exc)}, status_code=400)


def _make_get(dialect: str, file: str, has_device: bool):
    """生成 GET 路由闭包（第六轮 §1.4）：有/无设备段两种签名；file 用于旁路分流。"""
    if has_device:
        def route(device: str, authorization: str = Header(default=""),
                  if_none_match: str = Header(default=""),
                  x_hub_device: str = Header(default="")) -> Response:
            dev, err = _resolve_device(device, x_hub_device)
            if err is not None:
                return err
            return _sync_get(dialect, authorization, if_none_match, device=dev, file=file)
    else:
        def route(authorization: str = Header(default=""),
                  if_none_match: str = Header(default=""),
                  x_hub_device: str = Header(default="")) -> Response:
            dev, err = _resolve_device(None, x_hub_device)
            if err is not None:
                return err
            return _sync_get(dialect, authorization, if_none_match, device=dev, file=file)
    return route


def _make_put(dialect: str, file: str, has_device: bool):
    """生成 PUT 路由闭包（第六轮 §1.4）：有/无设备段两种签名；file 用于旁路分流。"""
    if has_device:
        async def route(device: str, request: Request,
                        authorization: str = Header(default=""),
                        x_hub_device: str = Header(default="")) -> Response:
            dev, err = _resolve_device(device, x_hub_device)
            if err is not None:
                return err
            return _sync_put(dialect, authorization, await request.body(), device=dev, file=file)
    else:
        async def route(request: Request, authorization: str = Header(default=""),
                        x_hub_device: str = Header(default="")) -> Response:
            dev, err = _resolve_device(None, x_hub_device)
            if err is not None:
                return err
            return _sync_put(dialect, authorization, await request.body(), device=dev, file=file)
    return route


def _register_dialect_routes() -> None:
    """按 REGISTRY 循环注册全部方言路由（第六轮 §1.4）：

    每个方言 roots×files → 4 条路由：/root/file 与 /d/{device}/root/file 的 GET/PUT。
    必须在本模块加载时执行，且先于尾部 catch-all（webdav_fallback）注册。
    """
    for _d in REGISTRY.values():
        for _root in _d.roots:
            for _f in _d.files:
                app.add_api_route(f"/{_root}/{_f}", _make_get(_d.name, _f, False), methods=["GET"])
                app.add_api_route(f"/{_root}/{_f}", _make_put(_d.name, _f, False), methods=["PUT"])
                app.add_api_route(f"/d/{{device}}/{_root}/{_f}", _make_get(_d.name, _f, True), methods=["GET"])
                app.add_api_route(f"/d/{{device}}/{_root}/{_f}", _make_put(_d.name, _f, True), methods=["PUT"])


_register_dialect_routes()


def _sync_get(dialect: str, authorization: str, if_none_match: str = "",
              device: Optional[str] = None, file: Optional[str] = None) -> Response:
    t0 = time.time()
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized(webdav=True)
    space = _users[user]["space"]
    # v3 角色闸门：成员即可拉取（viewer 只读也放行 GET）
    deny = _require_role(user, space, "read")
    if deny is not None:
        return deny
    rt = _hub.get(space)
    # 第六轮 §2.4：旁路文件（如洛雪 settings.json/user_apis.json）——整文件 opaque 原样归还
    _by = REGISTRY.get(dialect)
    if _by is not None and file is not None and file in _by.bypass_files:
        cid = rt.client_for(user, dialect, device)
        blob = ((rt.opaque_by_client.get(cid) or {}).get("lx_blobs") or {}).get(file)
        if blob is None:
            _log(f"GET /{dialect}/{file} 404 旁路文件不存在（客户端按'云端未找到'处理）")
            return JSONResponse({"error": "not found"}, status_code=404)
        return Response(content=blob, media_type="application/json; charset=utf-8")
    role = _role_in(user, space)
    # 能力表单一来源（adapters.CAPABILITIES）：2026-10-07 起澜音也按可删处理
    can_delete = bool(adapters.CAPABILITIES.get(dialect, {}).get("delete_track", False))
    cid = None
    with rt.lock:
        if role == "viewer":
            # v3 只读成员：不能 PUT、永远建不了基线，deliver 会对其 404；
            # 故走权威视图只读快照（不注册客户端/不推进基线/不 ack 墓碑，见 engine.peek_view）。
            view, peek_cleanup, peek_stamp = rt.engine.peek_view(dialect, can_delete=can_delete)
            result = None
        else:
            cid = rt.client_for(user, dialect, device)
            try:
                result = rt.engine.deliver(cid)
            except Exception:
                _hub.save(space)
                return JSONResponse({"error": "deliver failed"}, status_code=500)
            _hub.save(space)
    # P0-1：无基线客户端（从未交付且从未提交）→ 404，栖弦走"远端不存在 ⇒ 以本地为准 ⇒ PUT"。
    # 澜音例外（**按方言注册表字段判断，与删除能力无关**）：它把"空远端"只当作"没有可导入内容"，
    #   404 反而让它误报"同步服务里没有该歌单端点"（澜音保存配置时会先探测），
    #   故给它 200 + 空歌单视图。2026-10-07 起澜音已按可删处理，但这条探测兼容保留。
    # （viewer 只读分支 result=None，不走 404，直接给权威视图。）
    if result is not None and result.status == 404:
        dev = f" device={device or 'default'}" if device else ""
        client = rt.engine.clients.get(cid)
        d = REGISTRY.get(dialect)
        if client is not None and d is not None and d.empty_view_on_no_baseline:
            payload = d.render(RenderContext(
                view={}, meta_pool=rt.meta_pool, native_ids=rt.native_ids(dialect),
                pending_cleanup=None, opaque={}, opaque_tracks=None,
                revision=rt.engine.revision, generated_at=rt.engine._now(), playlist_times={},
            ))
            data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            _log(f'GET /{dialect}{dev} 200 空视图（无基线；{dialect} 探测兼容）')
            return Response(content=data, media_type="application/json; charset=utf-8")
        _log(f'GET /{dialect}{dev} 404 无基线（从未交付/从未提交，需先 PUT 建基线）')
        return JSONResponse({"error": "no baseline"}, status_code=404)

    if result is not None:
        view = result.view
        pending_cleanup = result.pending_cleanup
        generated_at = result.stamp or rt.engine._now()
    else:
        # viewer 只读快照
        pending_cleanup = peek_cleanup
        generated_at = peek_stamp
    native_ids = rt.native_ids(dialect)
    # 渲染统一走注册表（第六轮 §1.3：RenderContext 抹平各方言渲染器签名差异）。
    # P0-2：确定性 generatedAt/modifiedAt —— 用引擎 stamp（内容不变则同字节同值），不用墙钟；
    # P0-2/P0-3：歌单 createdAt/updatedAt 用 canonical 时刻；本地/文件曲目按 I6 回填。
    pl_times = {
        pl_id: {"created_at": pl.created_at, "updated_at": pl.updated_at}
        for pl_id, pl in rt.engine.playlists.items()
        if pl.deleted_at is None
    }
    # 多账户共用歌单 §1：opaque 按客户端取（本客户端桶；老格式回退 legacy 槽）。
    # viewer 只读快照没有客户端桶，且绝不能看到别人的私有 opaque → 用空。
    client_opaque = ({} if cid is None
                     else (rt.opaque_by_client.get(cid) or getattr(rt, "opaque_legacy", {})))
    opaque_tracks = (client_opaque or {}).get("tracks") if isinstance(client_opaque, dict) else None
    d = REGISTRY[dialect]
    payload = d.render(RenderContext(
        view=view, meta_pool=rt.meta_pool, native_ids=native_ids,
        pending_cleanup=pending_cleanup, opaque=client_opaque or {},
        opaque_tracks=opaque_tracks, revision=rt.engine.revision,
        generated_at=generated_at, playlist_times=pl_times,
    ))
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    etag = _canonical_etag(payload)
    # 决策（docs/03:124、docs/05:98、docs/09:159）：**永不向客户端回 304**。
    # 栖弦把任何非 2xx 当同步失败；内容未变时同样返回 200 + 相同正文/ETag。
    # 故 If-None-Match 只作为请求头被容忍（签名保留），不参与分支。
    _log(f'GET /{dialect}{" device=" + device if device else ""} 200 '
         f'({int((time.time() - t0) * 1000)}ms) '
         f'stamp={generated_at} rev={rt.engine.revision}')
    # P0-2：内容不变 ⇒ 渲染字节全同 ⇒ ETag 同值（客户端条件请求可复用）
    return Response(content=data, media_type="application/json; charset=utf-8",
                    headers={"ETag": etag})


def _sync_put(dialect: str, authorization: str, body: bytes,
              device: Optional[str] = None, file: Optional[str] = None) -> Response:
    t0 = time.time()
    user = _auth_user(authorization)
    if user is None:
        return _unauthorized(webdav=True)
    # v3 角色闸门：viewer（只读成员）拒绝一切同步写入（覆盖 4 条 PUT 路由，含设备段）
    space = _users[user]["space"]
    deny = _require_role(user, space, "write")
    if deny is not None:
        _log(f"PUT /{dialect}{' device=' + device if device else ''} 403 "
             f"只读成员 {user}（角色 {_role_in(user, space)}）拒绝写入")
        return deny

    rt = _hub.get(space)
    cid = rt.client_for(user, dialect, device)
    # 取证开关（默认关，第九轮 lx 事故后加）：设 HUB_RAW_PUT_DIR=<目录> 时，把每个 PUT 的
    # **原文**按客户端落盘（解析结果只说明我们读到了什么，说明不了客户端到底发了什么——
    # lx 那次"整份少一张歌单"的根因就卡在这里）。每个客户端只保留最近 20 个文件。
    _dump_dir = os.environ.get("HUB_RAW_PUT_DIR")
    if _dump_dir:
        try:
            safe = re.sub(r"[^0-9A-Za-z._-]", "_", cid)
            d = os.path.join(_dump_dir, safe)
            os.makedirs(d, exist_ok=True)
            ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            fn = os.path.join(d, f"{ts}_{file or 'playlists'}.json")
            with open(fn, "wb") as fh:
                fh.write(body)
            _log(f"PUT /{dialect} 原文已落盘 {fn}（{len(body)} 字节）")
            for old in sorted(os.listdir(d))[:-20]:
                os.remove(os.path.join(d, old))
        except Exception as exc:      # 取证失败绝不影响同步
            _log(f"PUT 原文落盘失败：{exc!r}")
    # 第六轮 §2.4：旁路文件（洛雪 settings.json/user_apis.json）——整文件 opaque 原样入库，
    # 不解析、不合并、不跨设备（按客户端分桶）；blob 存原文 bytes，渲染原样归还。
    _by = REGISTRY.get(dialect)
    if _by is not None and file is not None and file in _by.bypass_files:
        with rt.lock:
            bucket = rt.opaque_by_client.setdefault(cid, {}).setdefault("lx_blobs", {})
            bucket[file] = body
            _hub.save(space)
        _log(f"PUT /{dialect}/{file}{' device=' + device if device else ''} 200 旁路文件已存")
        return JSONResponse({"ok": True})

    try:
        payload = json.loads(body.decode("utf-8"))
    except Exception:
        return JSONResponse({"error": "body 必须是合法 UTF-8 JSON"}, status_code=400)

    try:
        submission, meta_delta, opaque, _w = REGISTRY[dialect].parse(payload)
    except adapters.ParseError as exc:
        return JSONResponse({"error": f"parse failed: {exc}"}, status_code=400)

    with rt.lock:
        # 多账户共用歌单 §1：opaque 按客户端写入本客户端桶；保留非空判断——
        # 澜音 parse_ceru 返回 opaque=None，不能把栖弦那份覆盖成空。
        if opaque and any(v is not None for v in opaque.values()):
            rt.opaque_by_client[cid] = opaque
        # P0-A（根因 A）：客户端提交声明的时刻由**适配器**放进 submission.client_modified_at
        # （栖弦 = sections.playlists.modifiedAt；洛雪 = 顶层 lastModified epoch ms）。
        # 枢纽不认任何方言字段名（加客户端不改 api.py）。交付时间戳必须严格压过它，
        # 否则"远端时间大才采纳"的客户端会丢弃删除视图并回推。
        client_modified_at = submission.get("client_modified_at")
        if not isinstance(client_modified_at, str) or not client_modified_at:
            client_modified_at = None
        try:
            result = rt.merge(cid, dialect, submission, meta_delta, now=None,
                              client_modified_at=client_modified_at)
        except Exception:
            _hub.save(space)
            return JSONResponse({"error": "merge failed"}, status_code=500)
        _hub.save(space)

    if result.status in (400, 412):
        # P0-5：400 由引擎"两段式校验"保证无半截 canonical 落库
        return JSONResponse({"error": result.meta.get("error", "rejected")},
                            status_code=result.status)

    m = result.meta
    parts = []
    if m.get("added_playlists"):
        parts.append("+歌单:" + ",".join(m["added_playlists"]))
    if m.get("removed_playlists"):
        parts.append("-歌单:" + ",".join(m["removed_playlists"]))
    for pl, ks in (m.get("added_tracks") or {}).items():
        parts.append(f"+曲目[{pl}]:{len(ks)}")
    for pl, ks in (m.get("removed_tracks") or {}).items():
        parts.append(f"-曲目[{pl}]:{len(ks)}")
    no = m.get("never_owned") or {}
    if no.get("playlists") or no.get("tracks"):
        parts.append("缺席仅记录(未拥有，不判删)")
    if m.get("reordered"):
        parts.append("排序变化")
    changed = "变更" if m.get("mutated") else "无变更"
    _log(f"PUT /{dialect}{' device=' + device if device else ''} -> rev={m.get('revision')} "
         f"sort_tag={m.get('sort_tag')} {changed}："
         f"{('；'.join(parts)) if parts else '无增删'} "
         f"({int((time.time() - t0) * 1000)}ms)")
    return JSONResponse({
        "status": result.status,
        "revision": m.get("revision", rt.engine.revision),
        "sort_tag": m.get("sort_tag", rt.engine.sort_tag),
        "stamp": result.stamp,
    })


# ---- 兼容端点（栖弦启动探测 / 洛雪目录探测 / CORS 预检 / 兜底 404） ----
@app.api_route("/{path:path}", methods=["PROPFIND"])
def webdav_propfind(path: str, request: Request) -> Response:
    """最小合法 207 multistatus（第六轮 §1.4 第 4 条）。

    洛雪（lx-x）用的 webdav@5.8.0 在上传前要 `ensureDirectoryExists()→cli.exists()→stat()`
    与 `testConnection()→getDirectoryContents('/')`，两者都发 **PROPFIND**；该库的 `exists()`
    只把 404 当"不存在"、其它错误照抛 ⇒ 拿到 405 会让它整轮同步直接失败。
    这里只描述请求路径自身（Depth 0/1 同一响应），**不参与任何业务逻辑**、不读磁盘。
    """
    href = "/" + path.strip("/")
    is_json = path.endswith(".json")
    rtype = "" if is_json else "<D:collection/>"
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<D:multistatus xmlns:D="DAV:">'
        f"<D:response><D:href>{href}</D:href>"
        "<D:propstat><D:prop>"
        f"<D:resourcetype>{rtype}</D:resourcetype>"
        "<D:getcontentlength>0</D:getcontentlength>"
        "<D:getlastmodified>Thu, 01 Jan 1970 00:00:00 GMT</D:getlastmodified>"
        "</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>"
        "</D:response></D:multistatus>"
    )
    _log(f"PROPFIND /{path} 207（最小 multistatus）")
    return Response(content=body, media_type='application/xml; charset="utf-8"',
                    status_code=207, headers={"DAV": "1"})


@app.api_route("/{path:path}", methods=["MKCOL"])
def webdav_mkcol(path: str) -> Response:
    """栖弦启动先 MKCOL {baseUrl}/CyShineMusic（webdav_client.dart），只接受 2xx/405。
    目录级路径返回 201（幂等），.json 上 MKCOL 返回 405。"""
    if path.endswith(".json"):
        return JSONResponse({"error": "method not allowed"}, status_code=405)
    return Response(status_code=201)


@app.options("/{path:path}")
def webdav_options(path: str) -> Response:
    return Response(status_code=204,
                    headers={"Allow": "GET, PUT, OPTIONS, MKCOL, PROPFIND", "DAV": "1"})


@app.head("/{path:path}")
def webdav_head(path: str) -> Response:
    return Response(status_code=200)


def _propfind_fake(href: str) -> bytes:
    """生成假 PROPFIND 207 响应——href 用实际请求路径，webdav npm 库的 exists() 会检查 href 匹配。"""
    # 确保 href 以 / 开头且 URL 编码安全
    if not href.startswith("/"):
        href = "/" + href
    return f"""<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>{href}</d:href>
    <d:propstat>
      <d:prop>
        <d:resourcetype><d:collection/></d:resourcetype>
        <d:displayname>{href.rsplit("/", 1)[-1] or "root"}</d:displayname>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>""".encode("utf-8")


@app.api_route("/{path:path}", methods=["GET", "PUT", "POST", "DELETE", "PROPFIND", "MKCOL", "HEAD"])
async def webdav_fallback(path: str, request: Request,
                          authorization: str = Header(default=""),
                          if_none_match: str = Header(default=""),
                          x_hub_device: str = Header(default="")) -> Response:
    """兜底 404。第六轮 §1.4.2：文件名兜底——地址里带任意目录名时（洛雪这类客户端），
    basename 命中某方言 files 且该方言 file_fallback=True 且方法为 GET/PUT → 分派给该方言
    （设备段可选：path 以 /d/<设备>/ 开头时按设备段解析）。只对显式开启的方言生效；
    /api/*、/assets/*、/ 的既有行为不变（它们都有更具体的路由，到不了这里）。

    第十一轮补：PROPFIND/MKCOL/HEAD 假响应——洛雪 ensureDirectoryExists 会先 exists()（PROPFIND）
    再 createDirectory()（MKCOL），枢纽没有真实目录结构，故统一返回假成功，让目录探测跳过。
    HEAD 走 PROPFIND 同路径（洛雪 testConnection 用 getDirectoryContents('/')）。
    """
    if request.method in ("PROPFIND", "HEAD"):
        # 任何路径的目录探测 → 207 Multi-Status 假响应，href 用实际路径
        _log(f"PROPFIND 兜底：{path} → 207 fake directory")
        return Response(content=_propfind_fake("/" + path), status_code=207,
                        headers={"Content-Type": "application/xml; charset=utf-8", "DAV": "1"})
    if request.method == "MKCOL":
        # 创建目录 → 201 Created 假响应
        _log(f"MKCOL 兜底：{path} → 201 fake created")
        return Response(status_code=201, headers={"DAV": "1"})
    if request.method in ("GET", "PUT"):
        base = path.rsplit("/", 1)[-1]
        for _d in REGISTRY.values():
            if _d.file_fallback and base in _d.files:
                _log(f"WARNING 文件兜底：{path} → {_d.name}（{request.method}）")
                path_device = None
                m = re.match(r"^d/([^/]+)/", path)
                if m:
                    path_device = m.group(1)
                dev, err = _resolve_device(path_device, x_hub_device)
                if err is not None:
                    return err
                if request.method == "GET":
                    return _sync_get(_d.name, authorization, if_none_match, device=dev, file=base)
                return _sync_put(_d.name, authorization, await request.body(), device=dev, file=base)
    return JSONResponse({"error": "not found"}, status_code=404)
