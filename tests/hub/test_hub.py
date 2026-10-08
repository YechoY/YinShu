"""hub 同步服务冒烟验证（**FastAPI 入口**，真实 HTTP）。

用法（项目根）：uv run python -m pytest tests/hub -q
起一个线程内 uvicorn（FastAPI 就是 README/文档指定的运行入口），模拟栖弦 / 澜音插件走真实
GET/PUT 流程：
  认证(401) · 未知路径(404) · 坏负载(400) · PUT→GET 回环 · 两端合并不丢数据 ·
  删除传播 · 幂等 · 空间隔离

背景（修改意见.md P1-6 / P3-1）：旧版 stdlib 入口 `hub/server.py` + `hub/run.py` 已移出 `src/`
（归档在 `docs/archive/legacy-http-entry/`）。本文件原先 `from hub.server import make_server`，
测的其实是**没人用的旧入口**；现在直接驱动 FastAPI，测的才是线上路径。
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

import uvicorn

from hub import api
from hub.store import Hub
from tests.hub.helpers import (HttpClient, ceru_payload, cyshine_payload,
                               cyshine_tracks, do_request, rmtree_force)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class SmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 端口与 test_api_http 的 18731+ 段错开，避免同进程内两个套件撞端口
        cls.port = _free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.server = uvicorn.Server(uvicorn.Config(api.app, host="127.0.0.1",
                                                  port=cls.port, log_level="warning"))
        cls.t = threading.Thread(target=cls.server.run, daemon=True)
        cls.t.start()
        for _ in range(100):
            try:
                urllib.request.urlopen(cls.base + "/api/state", timeout=1)
                break
            except urllib.error.HTTPError:
                break
            except Exception:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.t.join(timeout=10)

    def setUp(self):
        # 共享空间会让前序测试的歌单被后序测试的"整段提交"改写/判删（测试间耦合）
        # → 每个测试独立 store + 配置
        self.store = tempfile.mkdtemp(prefix="hub_smoke_", dir=os.path.join(ROOT, "tmp"))
        api.configure(Hub(self.store), {
            "alice": {"password": api._hash_pw("pw1"), "space": "main",
                      "role": "admin", "enabled": True},
            "bob": {"password": api._hash_pw("pw2"), "space": "other",
                    "role": "user", "enabled": True},
        }, config_path=None)

    def tearDown(self):
        rmtree_force(self.store)

    # ---- 认证 ----
    def test_01_auth_required(self):
        st, _, _ = HttpClient(self.base, "alice", "WRONG").get("/CyShineMusic/sync-v1.json")
        self.assertEqual(st, 401)
        req = urllib.request.Request(self.base + "/CyShineMusic/sync-v1.json")
        from tests.hub.helpers import do_request
        st2, _, _ = do_request(req)
        self.assertEqual(st2, 401)

    # ---- C6：挑战头分离（第三轮 §1）----
    def test_unauthorized_webdav_has_challenge_header_browser_has_not(self):
        """WebDAV 同步路由的 401 带 WWW-Authenticate（栖弦/澜音预置凭据、需要被挑战）；
        浏览器/API 路由的 401 不带该头（否则 Chrome/Edge 弹原生 Basic 框）。"""
        req = urllib.request.Request(self.base + "/CyShineMusic/sync-v1.json")
        from tests.hub.helpers import do_request
        st, headers, _ = do_request(req)
        self.assertEqual(st, 401)
        self.assertIn("WWW-Authenticate", headers)
        self.assertTrue(str(headers.get("WWW-Authenticate", "")).startswith("Basic"))
        st2, headers2, _ = HttpClient(self.base, "alice", "WRONG").get("/api/state")
        self.assertEqual(st2, 401)
        self.assertNotIn("WWW-Authenticate", headers2)

    # ---- 未知路径 ----
    def test_02_unknown_path_404(self):
        c = HttpClient(self.base, "alice", "pw1")
        self.assertEqual(c.get("/nope.json")[0], 404)

    # ---- 坏负载 ----
    def test_03_bad_payload_400(self):
        c = HttpClient(self.base, "alice", "pw1")
        st, _, _ = c.put("/CyShineMusic/sync-v1.json", {"not": "a sync doc"})
        self.assertEqual(st, 400)

    # ---- PUT→GET 回环 ----
    def test_04_cyshine_put_get_roundtrip(self):
        c = HttpClient(self.base, "alice", "pw1")
        st, _, _ = c.put("/CyShineMusic/sync-v1.json",
                         cyshine_payload([{"id": "0", "name": "我的喜欢",
                                           "tracks": [("tx", "1", "歌A")]}]))
        self.assertEqual(st, 200)
        st, headers, body = c.get("/CyShineMusic/sync-v1.json")
        self.assertEqual(st, 200)
        self.assertTrue(headers.get("ETag"))
        self.assertEqual(cyshine_tracks(body), {"0": {"tx_1"}})
        # 三节齐全、musicId 形如 source_songId、interval mm:ss
        pl = body["sections"]["playlists"]["data"][0]
        self.assertEqual(pl["tracks"][0]["musicId"], "tx_1")
        self.assertEqual(pl["tracks"][0]["musicInfo"]["interval"], "03:27")
        self.assertEqual(pl["tracks"][0]["musicInfo"]["meta"]["songId"], "1")
        self.assertIsInstance(body["sections"]["appearance"]["data"]["themeSeedArgb"], int)

    # ---- 两端合并不丢数据 ----
    def test_05_two_clients_merge_no_loss(self):
        c = HttpClient(self.base, "alice", "pw1")
        c.put("/CyShineMusic/sync-v1.json",
              cyshine_payload([{"id": "1", "name": "Rock",
                                "tracks": [("tx", "A", "歌A")]}]))
        c.get("/CyShineMusic/sync-v1.json")
        lanyin = HttpClient(self.base, "alice", "pw1")
        lanyin.put("/ceru/sync-v1.json",
                   ceru_payload([{"id": "1", "name": "Rock",
                                  "tracks": [("tx", "A", "歌A"), ("kw", "B", "歌B")]}]))
        lanyin.get("/ceru/sync-v1.json")
        st, _, body = c.get("/CyShineMusic/sync-v1.json")
        self.assertEqual(st, 200)
        self.assertEqual(cyshine_tracks(body)["1"], {"tx_A", "kw_B"})

    # ---- 删除传播 ----
    def test_06_delete_propagates(self):
        c = HttpClient(self.base, "alice", "pw1")
        lanyin = HttpClient(self.base, "alice", "pw1")
        c.put("/CyShineMusic/sync-v1.json",
              cyshine_payload([{"id": "2", "name": "Mixed",
                                "tracks": [("tx", "A", "歌A"), ("wy", "B", "歌B")]}]))
        c.get("/CyShineMusic/sync-v1.json")
        lanyin.put("/ceru/sync-v1.json",
                   ceru_payload([{"id": "2", "name": "Mixed",
                                  "tracks": [("tx", "A", "歌A"), ("wy", "B", "歌B")]}]))
        lanyin.get("/ceru/sync-v1.json")
        # 栖弦删 B → 澜音 GET 后也不再见 B（墓碑压制，不复活）
        c.put("/CyShineMusic/sync-v1.json",
              cyshine_payload([{"id": "2", "name": "Mixed",
                                "tracks": [("tx", "A", "歌A")]}]))
        c.get("/CyShineMusic/sync-v1.json")
        _, _, body = lanyin.get("/ceru/sync-v1.json")
        tracks = {t["songId"] for pl in body["playlists"] for t in pl["tracks"]}
        self.assertEqual(tracks, {"A"})

    # ---- 幂等 ----
    def test_07_idempotent_put(self):
        c = HttpClient(self.base, "alice", "pw1")
        pl = cyshine_payload([{"id": "3", "name": "Stable",
                               "tracks": [("tx", "X", "歌X")]}])
        st1, _, _ = c.put("/CyShineMusic/sync-v1.json", pl)
        c.get("/CyShineMusic/sync-v1.json")
        st2, _, _ = c.put("/CyShineMusic/sync-v1.json", pl)   # 同内容重复提交
        c.get("/CyShineMusic/sync-v1.json")
        _, _, body = c.get("/CyShineMusic/sync-v1.json")
        self.assertEqual(cyshine_tracks(body)["3"], {"tx_X"})
        self.assertEqual(st1, 200)
        self.assertEqual(st2, 200)

    # ---- 空间隔离 ----
    def test_08_spaces_isolated(self):
        a = HttpClient(self.base, "alice", "pw1")
        b = HttpClient(self.base, "bob", "pw2")   # 不同账号 → 不同空间
        a.put("/CyShineMusic/sync-v1.json",
              cyshine_payload([{"id": "0", "name": "Alice 的",
                                "tracks": [("tx", "Z", "Z")]}]))
        a.get("/CyShineMusic/sync-v1.json")
        # bob 从未提交/交付过 → 无基线 GET 404（P0-1：新设备不会被清库）
        st, _, body = b.get("/CyShineMusic/sync-v1.json")
        self.assertEqual(st, 404)
        self.assertEqual((body or {}).get("error"), "no baseline")


if __name__ == "__main__":
    unittest.main(verbosity=2)


def _free_port() -> int:
    """向系统要一个空闲端口。

    第十二轮（测试基建）：原实现是 `18xxx + (os.getpid() % 100)` 的固定基址，
    而本机 18787 上常驻着 DSH 的亿级上下文代理（billion-context）。某个 shell 的
    PID%100 恰好撞上基址偏移时，uvicorn 线程起不来（winerror 10048），整卷跑就
    表现为"偶发失败/单跑却通过"的假失败。改成向系统要端口，彻底避免撞车。
    """
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
