"""Hub：每认证账号一个同步空间（SyncSpace），负责：
  - 持有一个引擎实例 + 元数据池（引擎只存身份键，展示字段在此补齐）+ opaque（客户端私有节）
  - 方言识别与适配器路由（docs/04 §4.3/4.5）
  - 客户端身份（client_id）分配与注册
  - 状态持久化/加载（store.py；P0-4：原子写 + 载入失败显式报错，禁止静默建空空间）
"""
from __future__ import annotations

import os
import sys
import threading
import logging
from typing import Dict, List, Optional, Tuple

from engine.engine import SyncSpace

from . import adapters

_log = logging.getLogger("hub.store")

# pkl 包装版本（P0-4：便于以后迁移）
SCHEMA_VERSION = 1


def _mtime_iso(path: str) -> str:
    """文件最后修改时间的本地时区 ISO 串（前端直接展示）。"""
    from datetime import datetime
    return datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec="seconds")


def _force_remove(path: str) -> bool:
    """可靠删除单个文件（多级兜底，2026-10-06 实测加固）。

    该用户环境存在"os.remove 返回成功但文件保留 / os.path.exists 假返回 False"的
    拦截现象，单纯 os.remove + exists 检查会**假成功**（文件留在磁盘 → 下次 lazy
    加载时旧空间复活，check_sync S1/S4/S6 即此根因）。兜底链：
      1) 存在性双验证（os + PowerShell Test-Path）确认是否真的没有；
      2) os.remove → 双验证；
      3) PowerShell Remove-Item -ErrorAction Stop → 双验证；
      4) rename 到 .trash-<ts>（rename 通常不被拦截）→ 原路径消失即达成删除目标，
         残留改名文件不在 `space__*.pkl` 命名内，不会被加载，由用户自行清理。
    """
    import subprocess

    def _gone(p: str) -> bool:
        if not os.path.exists(p):
            return True
        if sys.platform == "win32":
            try:
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "Test-Path -LiteralPath '%s'" % p],
                    capture_output=True, timeout=10)
                return r.stdout.strip() != b"True"
            except Exception:
                return False
        return False

    if _gone(path):
        return True
    try:
        os.remove(path)
        if _gone(path):
            return True
    except OSError:
        pass
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Remove-Item -LiteralPath '%s' -Force -ErrorAction Stop" % path],
                capture_output=True, timeout=15)
            if _gone(path):
                return True
        except Exception:
            pass
        import time as _t
        trash = "%s.trash-%d" % (path, int(_t.time()))
        try:
            os.rename(path, trash)
            try:
                os.remove(trash)
            except OSError:
                pass
            if _gone(path):
                return True
        except OSError:
            pass
    return False


class SpaceRuntime:
    """单个账号同步空间的运行时状态。"""

    def __init__(self, space_id: str):
        self.space_id = space_id
        self.engine = SyncSpace(f"space:{space_id}")
        # 元数据池：identity_key -> 展示字段（引擎不存展示层，P5 只存一份）
        self.meta_pool: Dict[str, dict] = {}
        # 客户端私有 opaque（栖弦 appearance/musicSources 原样透传，docs/04 §4.4.1；
        # P0-3：本地/文件曲目按 I6 也放这里，渲染时回填）
        # 多账户共用歌单 §1：按客户端分桶（键 = cid），主题/音源/本地曲目不再互相覆盖
        self.opaque_by_client: Dict[str, dict] = {}
        # 老格式（仅空间级 "opaque" 键）迁移槽：老设备读取时回退用它，新设备从空开始
        self.opaque_legacy: dict = {}
        self._clients: Dict[str, str] = {}   # client_id -> dialect
        # P0-4/P1-7：每空间一把锁，merge/save/UI 写操作同一把（单进程单 worker 前提）
        self.lock = threading.RLock()

    # ---- 方言与客户端身份 ----
    def client_for(self, account: str, dialect: str, device: Optional[str] = None) -> str:
        """同一账号+同一方言+同一设备 = 一个客户端（多设备改造 §3.1）。

        设备标识来自 URL 设备段 `/d/{device}/...` 或 `X-Hub-Device` 头（头优先，
        见 api.client_identity）。无设备标识时退化为 `default`。
        兼容：老空间 client key 是 `账号:方言`，无标识请求继续沿用旧 key
        （§5 别名继承，行为与升级前完全一致——删除判据用的 base_served/base_submitted 原样保留）；
        只有带设备标识的请求才新建独立 client（首次接入走只增不删建基线，语义正确）。
        """
        did = device or "default"
        cid = f"{account}:{dialect}:{did}"
        if did == "default":
            legacy = f"{account}:{dialect}"
            if legacy in self.engine.clients:
                return legacy   # 老空间：沿用升级前的 client，零行为变化
        if cid not in self.engine.clients:
            caps = adapters.CAPABILITIES.get(dialect, {})
            _srcs = caps.get("round_trip_sources")
            self.engine.register_client(
                cid, dialect=dialect, identity_verified=True,
                can_delete=bool(caps.get("delete_track", False)),
                full_view_submit=bool(caps.get("full_view_submit", False)),
                round_trip_sources=frozenset(_srcs) if _srcs else None)
            self._clients[cid] = dialect
        return cid

    # ---- 账号空间切换：重置客户端基线 ----
    def reset_account_clients(self, account: str) -> int:
        """账号刚切到这个空间 → 清空它所有 client 的同步基线。

        目的：让下一次 PUT 走 _adopt_initial（只增不删），避免客户端带着
        另一个空间的歌单回来时，引擎把"缺席"误判为删除。
        返回被重置的 client 数量。
        """
        reset = 0
        prefix = f"{account}:"
        for cid, client in list(self.engine.clients.items()):
            if cid.startswith(prefix):
                client.base_served = None
                client.base_submitted = None
                client.opaque = {}
                client.last_delivered_stamp = None
                client.last_submitted_stamp = None
                reset += 1
        return reset

    # ---- 合并 ----
    def merge(self, client_id: str, dialect: str, submission: dict,
              meta_delta: Optional[Dict[str, dict]], now: Optional[str],
              client_modified_at: Optional[str] = None) -> dict:
        # 元数据合并（缺失字段不覆盖已存在的，P4 保留）
        if meta_delta:
            for key, meta in meta_delta.items():
                cur = self.meta_pool.setdefault(key, {})
                for k, v in meta.items():
                    if v is not None and k not in cur:
                        cur[k] = v
        # P0-A：客户端提交声明的 modifiedAt 透传给引擎（交付时间戳须压过它）
        return self.engine.merge(client_id, submission, now,
                                 client_modified_at=client_modified_at)

    # ---- 渲染辅助 ----
    def native_ids(self, dialect: str) -> Dict[str, str]:
        """canonical pl_id -> 该端 native id（engine 已存 platforms.<dialect>.native_id）。"""
        out = {}
        for pl_id, pl in self.engine.playlists.items():
            if pl.deleted_at is None:
                pf = pl.platforms.get(dialect)
                if pf and pf.get("native_id"):
                    out[pl_id] = pf["native_id"]
        return out


class Hub:
    """账号→空间 的容器，负责持久化。"""

    def __init__(self, store_dir: str):
        self.store_dir = store_dir
        os.makedirs(store_dir, exist_ok=True)
        self._runtimes: Dict[str, SpaceRuntime] = {}
        # 由 api 层注入：每次加载/探测到空间后立刻套用全局 policy
        # （第八轮修正：_apply_engine_policy 原先只在启动时对**已加载**的空间生效，
        #  按需加载的空间永远拿不到 policy —— 开关缺省的旧 pkl 因此一直产生恢复卡）
        self.policy_hook = None
        self._load_index()

    def _apply_policy(self, rt: Optional[SpaceRuntime]) -> None:
        hook = getattr(self, "policy_hook", None)
        if hook is not None and rt is not None and rt.engine is not None:
            hook(rt.engine)

    def _space_path(self, space_id: str) -> str:
        return os.path.join(self.store_dir, f"space__{space_id}.pkl")

    def _index_path(self) -> str:
        return os.path.join(self.store_dir, "_index.json")

    def _load_index(self):
        import json
        try:
            with open(self._index_path(), "r", encoding="utf-8") as f:
                idx = json.load(f)
            self._known = set(idx.get("spaces", []))
        except Exception:
            self._known = set()

    def _save_index(self):
        import json
        tmp = self._index_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"spaces": sorted(self._known)}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._index_path())

    def get(self, space_id: str) -> SpaceRuntime:
        rt = self._runtimes.get(space_id)
        if rt is None:
            rt = self._load(space_id)
            if rt is None:
                rt = SpaceRuntime(space_id)
                self._known.add(space_id)
            self._runtimes[space_id] = rt
        self._apply_policy(rt)
        return rt

    def probe(self, space_id: str) -> Optional[SpaceRuntime]:
        """只读探测（多账户共用歌单 §3）：已加载直接返回；未加载从磁盘读；
        磁盘也没有 → None（**不创建**空空间——只读接口不得有建空间副作用）。"""
        rt = self._runtimes.get(space_id)
        if rt is None:
            rt = self._load(space_id)
        self._apply_policy(rt)
        return rt

    def list_spaces(self) -> list[str]:
        """枚举磁盘/内存中实际存在的空间 id（含无账号指向的孤儿空间），供空间管理展示。"""
        out: set[str] = set(self._runtimes.keys())
        out |= set(self._known)
        if os.path.isdir(self.store_dir):
            try:
                for fn in os.listdir(self.store_dir):
                    if fn.startswith("space__") and fn.endswith(".pkl"):
                        out.add(fn[len("space__"):-len(".pkl")])
            except OSError:
                pass
        return sorted(out)

    # ---- 备份与恢复（前端「备份与恢复」块，2026-10-09） ----
    def list_backups(self, space_id: str) -> list:
        """列出空间的三个槽位：current / bak1 / bak2，各带摘要（歌单数/曲目数/歌单名）。

        bak1 = 上一次 save 前的状态，bak2 = 再往前一次；滚动保留，不会越堆越多。"""
        base = self._space_path(space_id)
        slots = []
        for slot in ("current", "bak1", "bak2"):
            path = base if slot == "current" else f"{base}.{slot}"
            if not os.path.exists(path):
                slots.append({"slot": slot, "exists": False})
                continue
            info = {"slot": slot, "exists": True,
                    "size": os.path.getsize(path),
                    "mtime": _mtime_iso(path)}
            # 读 pkl 摘要：只取 engine.playlists，给前端展示"恢复后会变成什么样"
            try:
                import pickle
                with open(path, "rb") as f:
                    blob = pickle.load(f)
                eng = (blob.get("data") or {}).get("engine")
                if eng is not None and hasattr(eng, "playlists"):
                    live = [p for p in eng.playlists.values() if p.deleted_at is None]
                    live.sort(key=lambda p: p.name or "")
                    info["playlists"] = len(live)
                    info["tracks"] = sum(len(p.track_ids) for p in live)
                    info["names"] = [p.name for p in live][:8]
                    info["revision"] = getattr(eng, "revision", 0)
            except Exception as exc:
                info["error"] = f"备份文件损坏或不可读: {exc}"
            slots.append(info)
        return slots

    def restore_backup(self, space_id: str, slot: str) -> dict:
        """从 bak1/bak2 恢复空间数据。

        - 恢复 = 文件互换（current ↔ bak1；bak2 走三向滚动），**可逆**：恢复 bak1 后
          再恢复一次 bak1 即撤销（原 current 已换到 bak1 槽位）。
        - 恢复后清空所有 client 的同步基线 → 各端下一次 PUT 走 _adopt_initial
          （只增不删），避免客户端本地还是恢复前状态时把刚恢复的数据判删。
        - 返回 {"reset_clients": n}。"""
        if slot not in ("bak1", "bak2"):
            raise ValueError("slot 只能是 bak1 或 bak2")
        base = self._space_path(space_id)
        src = f"{base}.{slot}"
        if not os.path.exists(src):
            raise FileNotFoundError(f"备份 {slot} 不存在")
        # 先把内存运行时踢掉，防止它随后 save() 用旧状态覆盖恢复结果
        self._runtimes.pop(space_id, None)
        if slot == "bak1":
            # current ↔ bak1 直接互换（可逆）
            tmp = base + ".swap"
            os.replace(base, tmp)
            os.replace(src, base)
            os.replace(tmp, src)
        else:
            # bak2 → current；current → bak1；旧 bak1 → bak2（三向滚动，不丢任何一份）
            old_bak1 = f"{base}.bak1"
            tmp = base + ".swap"
            if os.path.exists(old_bak1):
                os.replace(old_bak1, tmp)
            os.replace(base, old_bak1)
            os.replace(src, base)
            if os.path.exists(tmp):
                os.replace(tmp, src)
        # 重新加载并重置基线
        rt = self._load(space_id)
        reset = 0
        if rt is not None:
            for client in rt.engine.clients.values():
                if client.base_served is not None or client.base_submitted is not None:
                    reset += 1
                client.base_served = None
                client.base_submitted = None
            self._runtimes[space_id] = rt
        return {"reset_clients": reset}

    def save(self, space_id: str):
        """P0-4：原子落盘（tmp + fsync + os.replace）+ 滚动 .bak。"""
        rt = self._runtimes.get(space_id)
        if rt is None:
            # 2026-10-08：这里原先静默 return —— 维护脚本用 probe()（只读、不登记
            # _runtimes）拿到空间再调 save()，会"看起来落盘成功但一个字节都没写"。
            # 改为显式报错，杜绝静默丢写。（api 层调用点全部先 Hub.get()。）
            raise KeyError("空间 %s 未加载到内存，save() 不会写盘；请先用 Hub.get(space_id) 取运行时"
                           % space_id)
        import pickle
        blob = {
            "schema_version": SCHEMA_VERSION,
            "data": {
                "engine": rt.engine,
                "meta_pool": rt.meta_pool,
                "opaque_by_client": rt.opaque_by_client,
                # 兼容快照：老格式空间级 opaque（迁移槽）也写一份旧键，方便回滚/旧工具读取
                "opaque": rt.opaque_legacy,
                "clients": rt._clients,
            },
        }
        target = self._space_path(space_id)
        tmp = target + ".tmp"
        with open(tmp, "wb") as f:
            pickle.dump(blob, f, protocol=pickle.HIGHEST_PROTOCOL)
            f.flush()
            os.fsync(f.fileno())
        # 滚动备份旧文件：旧 target → .bak1，更旧 .bak1 → .bak2（崩溃可回滚）
        bak1, bak2 = target + ".bak1", target + ".bak2"
        if os.path.exists(target):
            if os.path.exists(bak1):
                if os.path.exists(bak2):
                    _force_remove(bak2)
                try:
                    os.replace(bak1, bak2)
                except OSError:
                    pass
            try:
                os.replace(target, bak1)
            except OSError:
                pass
        os.replace(tmp, target)     # 原子替换，崩溃不会留下半写文件
        self._known.add(space_id)
        self._save_index()

    def delete_space(self, space_id: str) -> bool:
        """删除一个空间的全部持久化数据（pkl + 索引记录 + 内存运行时）。

        仅当没有任何账号指向该空间时由账号删除流程调用；返回是否实际删除。
        _force_remove 内部自带双验证（os + PowerShell Test-Path），不能依赖
        裸 os.path.exists 判断"文件在不在"（该环境会假阴性，2026-10-06 实测）。
        """
        path = self._space_path(space_id)
        removed = _force_remove(path)
        self._runtimes.pop(space_id, None)
        if space_id in self._known:
            self._known.discard(space_id)
            self._save_index()
        if not removed:
            _log.warning("[store] delete_space(%s) 磁盘文件未能删除: %s（可能被占用/拦截）",
                         space_id, path)
        return removed

    def _load(self, space_id: str) -> Optional[SpaceRuntime]:
        import pickle
        import time as _time
        path = self._space_path(space_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "rb") as f:
                blob = pickle.load(f)
        except Exception as exc:
            # P0-4：载入失败**显式报错**（禁止静默建空空间覆盖唯一数据副本）。
            # 损坏文件改名保留取证，随后抛错让上层进入只读故障态。
            corrupt = path + ".corrupt-" + _time.strftime("%Y%m%d%H%M%S")
            try:
                os.replace(path, corrupt)
            except OSError:
                pass
            raise RuntimeError(
                f"空间 {space_id} 数据损坏无法载入（已保留为 {os.path.basename(corrupt)}）：{exc!r}") from exc
        if not isinstance(blob, dict):
            raise RuntimeError(f"空间 {space_id} 数据损坏无法载入：非对象")
        # v0 旧格式（无 schema_version，直接是 {engine,meta_pool,...}）→ 自动兼容迁移，
        # 首次 save 后按新格式重写；真正不兼容的版本才报错。
        if blob.get("schema_version") == SCHEMA_VERSION:
            data = blob.get("data") or {}
        elif "engine" in blob:
            data = blob
        else:
            raise RuntimeError(
                f"空间 {space_id} 数据版本不兼容：{blob.get('schema_version')!r}")
        rt = SpaceRuntime(space_id)
        rt.engine = data["engine"]
        rt.meta_pool = data.get("meta_pool", {})
        # 多账户共用歌单 §1：新格式按客户端分桶；老格式（仅 "opaque" 键）迁到 legacy 槽
        if "opaque_by_client" in data:
            rt.opaque_by_client = data.get("opaque_by_client") or {}
        else:
            rt.opaque_by_client = {}
        rt.opaque_legacy = data.get("opaque") or {}
        rt._clients = data.get("clients", {})
        # 自愈：旧版本 pkl 反序列化不跑 __init__，补齐本轮新增字段（P0-2/P1-9）
        _repair_engine_fields(rt.engine)
        # 自愈：opaque 新字段/老键迁移集中处理（多账户共用歌单 §1）
        _repair_opaque_fields(rt)
        # 自愈：playlist_order 可能因历史 bug（复活不 append / 序列化遗漏）缺失存活歌单，
        # 补全显示顺序，防止"数据在但歌单列表渲染不出来"。
        _repair_playlist_order(rt.engine)
        self._known.add(space_id)
        return rt


def _repair_engine_fields(engine) -> None:
    """旧格式 pkl 加载后补齐新字段（pickle 反序列化不执行 __init__）。"""
    if not hasattr(engine, "content_changed_at"):
        engine.content_changed_at = None
    if not hasattr(engine, "order_changed_at"):
        engine.order_changed_at = None
    if not hasattr(engine, "sort_tag"):
        engine.sort_tag = 0
    # D34：墓碑/冷静期/确认卡全拆除——旧 pkl 遗留字段一律清掉
    for _legacy in ("pending_deletions", "pending_restores",
                    "confirm_restore", "restore_cards_enabled", "delete_cards_enabled",
                    "tombstones", "trusted_direct_delete", "restore_grace_seconds"):
        engine.__dict__.pop(_legacy, None)
    for c in engine.clients.values():
        if not hasattr(c, "last_delivered_stamp"):
            c.last_delivered_stamp = None
        if not hasattr(c, "last_submitted_stamp"):
            c.last_submitted_stamp = None
        if "full_view_submit" not in c.__dict__ or "round_trip_sources" not in c.__dict__:
            caps = adapters.CAPABILITIES.get(c.dialect, {})
            c.full_view_submit = bool(caps.get("full_view_submit", False))
            _srcs = caps.get("round_trip_sources")
            c.round_trip_sources = frozenset(_srcs) if _srcs else None
        if c.base_served is not None:
            if not hasattr(c.base_served, "stamp"):
                c.base_served.stamp = ""
            if not hasattr(c.base_served, "opaque_hash"):
                c.base_served.opaque_hash = None


def _repair_opaque_fields(rt) -> None:
    """多账户共用歌单 §1：opaque 新字段补齐（极端情况下对象缺属性时兜底）。
    "旧键 → legacy 槽"已在 _load 载入路径集中处理；这里只保证字段存在。"""
    if not hasattr(rt, "opaque_by_client"):
        rt.opaque_by_client = {}
    if not hasattr(rt, "opaque_legacy"):
        rt.opaque_legacy = {}


def _repair_playlist_order(engine) -> None:
    """把存活但不在 playlist_order 里的歌单补到末尾（顺序权威源 playlist_order）。"""
    order = list(engine.playlist_order)
    for pid, pl in engine.playlists.items():
        if pl.deleted_at is None and pid not in order:
            order.append(pid)
    if order != list(engine.playlist_order):
        engine.playlist_order[:] = order
