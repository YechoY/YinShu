"""多设备改造 P0 的 HTTP 验收（多设备同步改造方案.md §7 的 M6/M7）。

M6：设备段路由 `/d/{device}/...` GET/PUT 全通（MKCOL/OPTIONS 走 WebDAV 兜底 201/204）。
M6b：原精确路由（无设备段 → default 身份）不受影响；各设备身份相互独立（无基线 404）。
M7：`X-Hub-Device` 头优先于路径段（同时给 d-a 路径与头 b ⇒ 归到 b）。
M7b：非法设备名（含空格等）→ 400。
复用 tests/hub/test_api_http.py 的 uvicorn 线程模式（不用 TestClient/httpx）。
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

import uvicorn

from hub import api
from hub.store import Hub


def _cyshine_payload(playlists):
    data = []
    for pl in playlists:
        tracks = []
        for src, sid, title in pl["tracks"]:
            tracks.append({
                "musicId": f"{src}_{sid}", "name": title, "singer": "歌手",
                "albumName": "专辑", "source": src, "quality": "hires",
                "picUrl": "", "musicInfo": {
                    "id": f"{src}_{sid}", "name": title, "singer": "歌手",
                    "source": src, "interval": "03:27",
                    "meta": {"songId": sid},
                },
            })
        data.append({"version": 1, "id": pl["id"], "name": pl["name"],
                     "tracks": tracks, "createdAt": "2026-10-05T00:00:00Z",
                     "updatedAt": "2026-10-05T00:00:00Z"})
    return {"schemaVersion": 1, "generatedAt": "2026-10-05T00:00:00Z",
            "sections": {"playlists": {"data": data},
                         "appearance": {"data": {"themeSeedArgb": 4289230000}},
                         "musicSources": {"data": []}}}


class _Client:
    def __init__(self, base: str, user: str, pw: str):
        self.base = base.rstrip("/")
        tok = base64.b64encode(f"{user}:{pw}".encode()).decode()
        self.headers = {"Authorization": f"Basic {tok}",
                        "Content-Type": "application/json; charset=utf-8"}

    def _do(self, req):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
                parsed = json.loads(body.decode("utf-8")) if body else None
                return r.status, r.headers, parsed
        except urllib.error.HTTPError as e:
            body = e.read()
            try:
                return e.code, e.headers, json.loads(body.decode("utf-8")) if body else None
            except Exception:
                return e.code, e.headers, body

    def get(self, path, extra_headers=None):
        h = dict(self.headers)
        if extra_headers:
            h.update(extra_headers)
        return self._do(urllib.request.Request(self.base + path, headers=h))

    def put(self, path, payload, extra_headers=None):
        h = dict(self.headers)
        if extra_headers:
            h.update(extra_headers)
        data = json.dumps(payload).encode("utf-8")
        return self._do(urllib.request.Request(self.base + path, data=data,
                                               headers=h, method="PUT"))

    def mkcol(self, path):
        return self._do(urllib.request.Request(self.base + path,
                                               headers=self.headers, method="MKCOL"))

    def options(self, path):
        return self._do(urllib.request.Request(self.base + path,
                                               headers=self.headers, method="OPTIONS"))


def _gone_dir(path):
    """PowerShell Test-Path 双视角验证目录是否真的没了。
    本机 os.path.exists 假阴性/假阳性均不可靠（2026-10-06 实测：os.remove/.NET 删除
    返回成功但 Test-Path 双视角仍 True），所以不依赖 exists 短路。"""
    try:
        import subprocess as _sp
        r = _sp.run(["powershell", "-NoProfile", "-Command",
                     f"Test-Path -LiteralPath '{path}'"],
                    capture_output=True, timeout=30)
        return r.stdout.strip() != b"True"
    except Exception:
        return False


def _rmtree_force(path):
    """本机 Windows 的删除不可靠：os.remove / .NET File.Delete / PowerShell Remove-Item
    都实测"返回成功但文件仍在"（Test-Path 双视角确认），cmd rd 也失败；
    改名/移动可靠（os.rename / Move-Item 实测可用）。
    策略：先 shutil.rmtree（正常系统快路径）→ Test-Path 双验证 → 仍残留则
    rename 到 `.trash-<ts>`（原路径立即消失，残留集中带统一前缀，不散落根目录）。"""
    shutil.rmtree(path, ignore_errors=True)
    if _gone_dir(path):
        return
    trash = f"{path}.trash-{time.strftime('%Y%m%d%H%M%S')}-{os.getpid()}"
    try:
        os.rename(path, trash)
    except Exception:
        try:
            import subprocess
            subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"Move-Item -LiteralPath '{path}' -Destination '{trash}' -Force"],
                capture_output=True, timeout=30)
        except Exception:
            pass


class DeviceHttpTest(unittest.TestCase):
    """每个测试独立空间；服务线程与 test_api_http 同一模式。"""

    @classmethod
    def setUpClass(cls):
        cls._root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        cls.port = _free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.server = uvicorn.Server(uvicorn.Config(api.app, host="127.0.0.1",
                                                   port=cls.port, log_level="warning"))
        cls.t = threading.Thread(target=cls.server.run, daemon=True)
        cls.t.start()
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"{cls.base}/api/state", timeout=1):
                    pass
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
        import tempfile as _tf
        self.store = _tf.mkdtemp(prefix="devices_http_", dir=os.path.join(self._root, "tmp"))
        self.hub = Hub(self.store)
        api.configure(self.hub, {
            "alice": {"password": api._hash_pw("pw1"), "space": "main",
                      "role": "admin", "enabled": True},
        }, config_path=None)

    def tearDown(self):
        _rmtree_force(self.store)          # 2026-10-06 加固：本机 Python 删除假成功

    # ---- M6：设备段路由 GET/PUT 全通；MKCOL/OPTIONS 无设备段路由 → 405 ----
    def test_m6_device_route_get_put(self):
        c = _Client(self.base, "alice", "pw1")
        st, _, _ = c.put("/d/phone-a/CyShineMusic/sync-v1.json",
                         _cyshine_payload([{"id": "p1", "name": "P", "tracks": [("tx", "1", "歌A")]}]))
        self.assertIn(st, (200, 201))
        st2, _, body = c.get("/d/phone-a/CyShineMusic/sync-v1.json")
        self.assertEqual(st2, 200)
        self.assertEqual(body["sections"]["playlists"]["data"][0]["id"], "p1")
        # MKCOL / OPTIONS：WebDAV 兜底路由（/{path:path}）对目录返回 201 / 204
        st3, _, _ = c.mkcol("/d/phone-a/CyShineMusic/")
        self.assertEqual(st3, 201)
        st4, _, _ = c.options("/d/phone-a/CyShineMusic/sync-v1.json")
        self.assertEqual(st4, 204)

    # ---- M6b：原精确路由（default 身份）不受影响；各设备身份相互独立 ----
    def test_m6b_original_routes_unaffected(self):
        c = _Client(self.base, "alice", "pw1")
        # default 身份走原路由：首次无基线 404 → PUT 建基线 → 200
        st, _, _ = c.get("/CyShineMusic/sync-v1.json")
        self.assertEqual(st, 404)
        st2, _, _ = c.put("/CyShineMusic/sync-v1.json",
                          _cyshine_payload([{"id": "p1", "name": "P", "tracks": []}]))
        self.assertIn(st2, (200, 201))
        st3, _, _ = c.get("/CyShineMusic/sync-v1.json")
        self.assertEqual(st3, 200)
        # 设备段身份独立：phone-b 从未接入 → 无基线 404（即使 default 已有基线）
        st4, _, _ = c.get("/d/phone-b/CyShineMusic/sync-v1.json")
        self.assertEqual(st4, 404)

    # ---- M7：X-Hub-Device 头优先于路径段 ----
    def test_m7_header_beats_path(self):
        c = _Client(self.base, "alice", "pw1")
        # 路径段 d-a + 头 phone-b → 归 phone-b（首提建基线）
        st, _, _ = c.put("/d/d-a/CyShineMusic/sync-v1.json",
                         _cyshine_payload([{"id": "p1", "name": "P", "tracks": []}]),
                         {"X-Hub-Device": "phone-b"})
        self.assertIn(st, (200, 201))
        # 带头 → phone-b 有基线 → 200
        st2, _, _ = c.get("/d/d-a/CyShineMusic/sync-v1.json", {"X-Hub-Device": "phone-b"})
        self.assertEqual(st2, 200)
        # 不带头 → 路径段 d-a（从未接入）→ 404
        st3, _, _ = c.get("/d/d-a/CyShineMusic/sync-v1.json")
        self.assertEqual(st3, 404)

    # ---- M7b：非法设备名 → 400（路径段与头都要校验） ----
    def test_m7b_invalid_device_name_400(self):
        c = _Client(self.base, "alice", "pw1")
        st, _, body = c.get("/d/bad%20name/CyShineMusic/sync-v1.json")
        self.assertEqual(st, 400)
        self.assertIn("error", body or {})
        st2, _, _ = c.get("/CyShineMusic/sync-v1.json", {"X-Hub-Device": "bad name"})
        self.assertEqual(st2, 400)

    # ---- M8（第六轮 §3.6.1）：同账号不同设备 → 独立 client；A 删歌不影响 B 首同步 ----
    def test_m8_devices_do_not_steal_deletions(self):
        c = _Client(self.base, "alice", "pw1")
        # A 设备建立基线：x + y，并 GET 一次（交付基线 = 引擎"缺席即删"的 base_served）
        st, _, _ = c.put("/d/phone-a/CyShineMusic/sync-v1.json",
                         _cyshine_payload([{"id": "p1", "name": "P",
                                            "tracks": [("tx", "x", "X"), ("tx", "y", "Y")]}]))
        self.assertIn(st, (200, 201))
        st0, _, _ = c.get("/d/phone-a/CyShineMusic/sync-v1.json")
        self.assertEqual(st0, 200)
        # A 删掉 y（提交只剩 x）
        st2, _, _ = c.put("/d/phone-a/CyShineMusic/sync-v1.json",
                          _cyshine_payload([{"id": "p1", "name": "P", "tracks": [("tx", "x", "X")]}]))
        self.assertIn(st2, (200, 201))
        # B 设备首次接入：从未见过 x/y，提交空视图 → 不得误删 A 的歌
        st3, _, _ = c.put("/d/phone-b/CyShineMusic/sync-v1.json",
                          _cyshine_payload([{"id": "p1", "name": "P", "tracks": []}]))
        self.assertIn(st3, (200, 201))
        _, _, body = c.get("/d/phone-a/CyShineMusic/sync-v1.json")
        ids = [t["musicId"] for t in body["sections"]["playlists"]["data"][0]["tracks"]]
        self.assertIn("tx_x", ids, "B 首同步不误删 A 的歌 x")
        self.assertNotIn("tx_y", ids, "A 自己删的 y 保持删除")

    # ---- M8b（第六轮 §3.6.2）：非法设备名含中文/超长 → 400 ----
    def test_m8b_invalid_device_name_cn_and_long_400(self):
        from urllib.parse import quote
        c = _Client(self.base, "alice", "pw1")
        st, _, body = c.get(f"/d/{quote('设备名')}/CyShineMusic/sync-v1.json")
        self.assertEqual(st, 400, "路径段中文非法")
        self.assertIn("error", body or {})
        st2, _, _ = c.get(f"/d/{'a' * 33}/CyShineMusic/sync-v1.json")
        self.assertEqual(st2, 400, "路径段超长非法")
        st4, _, _ = c.get("/CyShineMusic/sync-v1.json", {"X-Hub-Device": "b" * 33})
        self.assertEqual(st4, 400, "头里超长非法")

    # ---- M8c（第六轮 §3.6.3）：/d/<设备>/… 与默认设备（无段）互不串扰 ----
    def test_m8c_default_and_device_isolated(self):
        c = _Client(self.base, "alice", "pw1")
        # default 建立基线 x + y
        c.put("/CyShineMusic/sync-v1.json",
              _cyshine_payload([{"id": "p1", "name": "P",
                                 "tracks": [("tx", "x", "X"), ("tx", "y", "Y")]}]))
        # 设备 A 首同步（从未见过 y），提交只剩 x → 不误删 default 的 y
        st, _, _ = c.put("/d/phone-a/CyShineMusic/sync-v1.json",
                         _cyshine_payload([{"id": "p1", "name": "P", "tracks": [("tx", "x", "X")]}]))
        self.assertIn(st, (200, 201))
        _, _, body = c.get("/CyShineMusic/sync-v1.json")
        ids = [t["musicId"] for t in body["sections"]["playlists"]["data"][0]["tracks"]]
        self.assertIn("tx_y", ids, "设备首同步不误删 default 的 y")
        # 反过来：default 删 y → 设备 A 提交（仍含 x）→ default 视图 y 保持删除
        c.put("/CyShineMusic/sync-v1.json",
              _cyshine_payload([{"id": "p1", "name": "P", "tracks": [("tx", "x", "X")]}]))
        _, _, body2 = c.get("/CyShineMusic/sync-v1.json")
        ids2 = [t["musicId"] for t in body2["sections"]["playlists"]["data"][0]["tracks"]]
        self.assertNotIn("tx_y", ids2, "default 自己删的 y 保持删除")
        # 设备 A 的基线独立存在（各自 GET 都 200）
        _, _, bodyA = c.get("/d/phone-a/CyShineMusic/sync-v1.json")
        self.assertEqual(bodyA["sections"]["playlists"]["data"][0]["id"], "p1")


if __name__ == "__main__":
    unittest.main()


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
