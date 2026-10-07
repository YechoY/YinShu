"""HTTP 同步端点（M2 最小原型）：Basic 认证 + 方言路由 + GET/PUT sync 文件。

端点语义（docs/06 §6.1）：
  PUT  /CyShineMusic/sync-v1.json  → 栖弦(cyshine-v1) 合并
  GET  /CyShineMusic/sync-v1.json  → 栖弦 渲染交付
  PUT  /ceru/sync-v1.json          → 澜音插件(ceru-plugin) 合并
  GET  /ceru/sync-v1.json          → 澜音 渲染交付（含待清理清单）

客户端只接受 2xx（docs/04 §4.3 / PF4）：GET 内容未变也返回 200 + 相同 ETag。
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Optional, Tuple

from . import adapters
from .api import _verify_pw
from .store import Hub

# 路径 → 方言
_ROUTES = {
    "/CyShineMusic/sync-v1.json": "cyshine-v1",
    "/ceru/sync-v1.json": "ceru-plugin",
}
_PARSE = {
    "cyshine-v1": adapters.parse_cyshine,
    "ceru-plugin": adapters.parse_ceru,
}
_RENDER = {
    "cyshine-v1": adapters.render_cyshine,
    "ceru-plugin": adapters.render_ceru,
}


def _now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _canonical_etag(obj) -> str:
    """PF5：ETag 用规范化哈希（对象键排序 + 稳定 JSON）。"""
    from engine.canonical import canonical_hash
    return f'"{canonical_hash(obj)}"'


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    hub: Hub = None          # 由 run.py 注入
    accounts: Dict[str, str] = None   # user -> password
    space_of: Dict[str, str] = None   # user -> space_id

    # ---- 认证 ----
    def _auth_user(self) -> Optional[str]:
        hdr = self.headers.get("Authorization", "")
        if not hdr.startswith("Basic "):
            return None
        try:
            raw = base64.b64decode(hdr[6:]).decode("utf-8")
        except Exception:
            return None
        user, _, pw = raw.partition(":")
        stored = self.accounts.get(user)
        if stored is not None and _verify_pw(pw, stored):   # 兼容哈希与旧明文
            return user
        return None

    def _require_auth(self) -> Optional[str]:
        user = self._auth_user()
        if user is None:
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="playlist-sync-hub"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        return user

    def _route(self):
        path = self.path.split("?", 1)[0]
        return _ROUTES.get(path)

    def _dialect_hint(self, dialect: str):
        return "cyshine-v1" if dialect == "cyshine-v1" else "ceru-plugin"

    # ---- GET ----
    def do_GET(self):
        self._t0 = time.time()
        user = self._require_auth()
        if user is None:
            return
        dialect = self._route()
        if dialect is None:
            self._send(404, {}, etag=None)
            return
        space_id = self.space_of[user]
        rt = self.hub.get(space_id)
        cid = rt.client_for(user, dialect)
        try:
            result = rt.engine.deliver(cid)
        except Exception:
            self.hub.save(space_id)
            self._send(500, {"error": "deliver failed"}, etag=None)
            return
        self.hub.save(space_id)

        view = result.view
        native_ids = rt.native_ids(dialect)
        generated_at = rt.engine._now()
        if dialect == "ceru-plugin":
            payload = adapters.render_ceru(view, rt.meta_pool, native_ids,
                                           result.pending_cleanup, generated_at)
        else:
            payload = _RENDER[dialect](view, rt.meta_pool, native_ids,
                                       rt.opaque, rt.engine.revision, generated_at)
        etag = _canonical_etag(payload)
        self._send(200, payload, etag=etag)

    # ---- PUT ----
    def do_PUT(self):
        self._t0 = time.time()
        user = self._require_auth()
        if user is None:
            return
        dialect = self._route()
        if dialect is None:
            self._send(404, {}, etag=None)
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            self._send(400, {"error": "body 必须是合法 UTF-8 JSON"})
            return

        space_id = self.space_of[user]
        rt = self.hub.get(space_id)
        cid = rt.client_for(user, dialect)
        try:
            submission, meta_delta, opaque, _w = _PARSE[dialect](payload)
        except adapters.ParseError as exc:
            self._send(400, {"error": f"parse failed: {exc}"})
            return
        # 透传 opaque（栖弦 appearance/musicSources，整段 LWW）
        if opaque and any(v is not None for v in opaque.values()):
            rt.opaque = opaque

        try:
            result = rt.merge(cid, dialect, submission, meta_delta, now=None)
        except Exception:
            self.hub.save(space_id)
            self._send(500, {"error": "merge failed"})
            return
        self.hub.save(space_id)

        if result.status in (400, 412):
            self._send(result.status, {"error": result.meta.get("error", "rejected")})
            return
        # 2xx：栖弦只接受 2xx（docs/04 §4.3 / PF4），挂起的删除也按 200 正常交付
        self._send(200, {"status": result.status,
                         "revision": result.meta.get("revision", rt.engine.revision),
                         "deferred": bool(result.meta.get("deferred"))})
        self._log_merge(dialect, self.path, result.meta)

    # ---- 其它方法 ----
    def do_MKCOL(self):
        """栖弦启动先 MKCOL {baseUrl}/CyShineMusic（webdav_client.dart:50），
        只接受 200/201/204/405。我们对其父目录路径返回 201（幂等）。"""
        user = self._require_auth()
        if user is None:
            return
        path = self.path.split("?", 1)[0]
        # 只允许目录级路径（不允许在 sync-v1.json 上 MKCOL）
        if path.endswith(".json"):
            self._send(405, {"error": "method not allowed"})
            return
        self.send_response(201)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Allow", "GET, PUT, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---- 工具 ----
    def _send(self, status: int, obj: dict, etag: Optional[str] = None):
        if isinstance(obj, (dict, list)):
            data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        else:
            data = json.dumps({"error": str(obj)}, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if etag is not None:
            self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(data)

    def _log_merge(self, dialect: str, path: str, meta: dict) -> None:
        """PUT 合并后打印变更摘要（歌单/曲目增减、挂起删除）。"""
        try:
            parts = []
            if meta.get("added_playlists"):
                parts.append("+歌单:" + ",".join(meta["added_playlists"]))
            if meta.get("removed_playlists"):
                parts.append("-歌单:" + ",".join(meta["removed_playlists"]))
            for pl, ks in (meta.get("added_tracks") or {}).items():
                parts.append(f"+曲目[{pl}]:{len(ks)}")
            for pl, ks in (meta.get("removed_tracks") or {}).items():
                parts.append(f"-曲目[{pl}]:{len(ks)}")
            if meta.get("suspects"):
                parts.append("候选删除(他端写入):" + str(len(meta["suspects"])))
            if meta.get("deferred"):
                parts.append("挂起删除(安全阀)")
            if meta.get("suppressed_playlists") or meta.get("suppressed_tracks"):
                parts.append("墓碑压制")
            rev = meta.get("revision")
            changed = "变更" if meta.get("mutated") else "无变更"
            detail = ("；".join(parts)) if parts else "无增删"
            print(
                f"[hub {_now_iso()}] PUT {path} ({dialect}) -> rev={rev} {changed}：{detail}",
                flush=True,
            )
        except Exception:
            pass

    def log_message(self, fmt, *args):
        # 每请求一行：方法/路径/状态码 + 耗时（默认静默，这里开放给命令行观察）
        try:
            elapsed = ""
            t0 = getattr(self, "_t0", None)
            if t0 is not None:
                elapsed = f" ({int((time.time() - t0) * 1000)}ms)"
            print(
                f"[hub {_now_iso()}] {self.address_string()} \"{self.requestline}\" {args[0]}{elapsed}",
                flush=True,
            )
        except Exception:
            pass


def make_server(host: str, port: int, hub: Hub,
                accounts: Dict[str, str], space_of: Dict[str, str]) -> ThreadingHTTPServer:
    _Handler.hub = hub
    _Handler.accounts = accounts
    _Handler.space_of = space_of
    return ThreadingHTTPServer((host, port), _Handler)
