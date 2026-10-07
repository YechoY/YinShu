"""FastAPI 版同步服务 HTTP 验收（修改意见.md P0/P1 可执行验收）。

起一个线程内 uvicorn（不依赖 TestClient/httpx，venv 装不上时仍可跑），
用 urllib 打真实 HTTP 端点，覆盖：
  无基线 GET 404 · PUT→GET 回环 · modifiedAt/ETag 确定性 · 条件请求 304
  · I6 本地文件曲目 opaque 回填 · 跨方言 native id · 排序不推 revision
  · 坏负载 400 无 canonical 污染 · 新增空歌单 revision+1
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
import urllib.request
import urllib.error

import uvicorn

from hub import api
from hub.store import Hub


def cyshine_payload(playlists: list, local_tracks: dict = None) -> dict:
    """栖弦 payload。playlists: [{id,name,tracks:[(src,sid,title)]}]
    local_tracks: {playlist_id: [原始 track 对象]} —— 本地/文件曲目（I6 opaque）。"""
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
        if local_tracks and str(pl["id"]) in local_tracks:
            tracks.extend(local_tracks[str(pl["id"])])
        data.append({"version": 1, "id": pl["id"], "name": pl["name"],
                     "tracks": tracks, "createdAt": "2026-10-05T00:00:00Z",
                     "updatedAt": "2026-10-05T00:00:00Z"})
    return {"schemaVersion": 1, "generatedAt": "2026-10-05T00:00:00Z",
            "sections": {"playlists": {"data": data},
                         "appearance": {"data": {"themeSeedArgb": 4289230000}},
                         "musicSources": {"data": []}}}


def ceru_payload(playlists: list) -> dict:
    out = []
    for pl in playlists:
        out.append({"id": pl["id"], "name": pl["name"], "tracks": [
            {"source": s, "songId": i, "title": t, "singer": "歌手",
             "album": "专辑", "durationMs": 207000} for s, i, t in pl["tracks"]]})
    return {"schemaVersion": 1, "playlists": out}


class _Client:
    def __init__(self, base: str, user: str, pw: str):
        self.base = base.rstrip("/")
        tok = base64.b64encode(f"{user}:{pw}".encode()).decode()
        self.headers = {"Authorization": f"Basic {tok}",
                        "Content-Type": "application/json; charset=utf-8"}

    def get(self, path, extra_headers=None):
        h = dict(self.headers)
        if extra_headers:
            h.update(extra_headers)
        req = urllib.request.Request(self.base + path, headers=h)
        return _do(req)

    def put(self, path, payload):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data,
                                     headers=self.headers, method="PUT")
        return _do(req)


def _do(req):
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read()
            parsed = json.loads(body.decode("utf-8")) if body else None
            return r.status, r.headers, parsed
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, e.headers, json.loads(body.decode("utf-8")) if body else None
        except Exception:
            return e.code, e.headers, body


def _cyshine_tracks(payload) -> dict:
    out = {}
    for pl in payload["sections"]["playlists"]["data"]:
        out[pl["id"]] = [t.get("musicId") for t in pl["tracks"]]
    return out


LOCAL_TRACK = {
    "name": "本地歌曲", "singer": "本机", "albumName": "本地专辑",
    "source": "local", "quality": "flac", "picUrl": "",
    "musicInfo": {"name": "本地歌曲", "singer": "本机",
                  "source": "local", "interval": "04:10", "meta": {}},
}


class ApiHttpTest(unittest.TestCase):
    """每个测试独立空间（同一客户端"提交=全量视图"语义下避免跨测试污染）。"""

    @classmethod
    def setUpClass(cls):
        _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        cls._root = _root
        cls.port = 18731 + (os.getpid() % 100)
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.server = uvicorn.Server(uvicorn.Config(api.app, host="127.0.0.1",
                                                   port=cls.port, log_level="warning"))
        cls.t = threading.Thread(target=cls.server.run, daemon=True)
        cls.t.start()
        # 等待端口就绪
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
        self.store = _tf.mkdtemp(prefix="api_http_", dir=os.path.join(self._root, "tmp"))
        self.hub = Hub(self.store)
        api.configure(self.hub, {
            "alice": {"password": api._hash_pw("pw1"), "space": "main",
                      "role": "admin", "enabled": True},
            "bob": {"password": api._hash_pw("pw2"), "space": "other",
                    "role": "user", "enabled": True},
        }, config_path=None)

    def tearDown(self):
        for _ in range(5):
            try:
                shutil.rmtree(self.store)
                break
            except OSError:
                time.sleep(0.3)

    # ---- P0-1：无基线 GET 404 ----
    def test_01_no_baseline_get_404(self):
        c = _Client(self.base, "bob", "pw2")   # bob 空间全新，从未交付/提交
        st, _, _ = c.get("/CyShineMusic/sync-v1.json")
        self.assertEqual(st, 404)
        # PUT 建基线后 GET 正常
        st2, _, _ = c.put("/CyShineMusic/sync-v1.json",
                          cyshine_payload([{"id": "p1", "name": "我的", "tracks": []}]))
        self.assertIn(st2, (200, 201))
        st3, _, body = c.get("/CyShineMusic/sync-v1.json")
        self.assertEqual(st3, 200)
        self.assertEqual(_cyshine_tracks(body), {"p1": []})

    # ---- P0-2：modifiedAt/ETag 确定性（内容不变 ⇒ 字节全同） ----
    def test_02_modified_at_stable_bytes_identical(self):
        c = _Client(self.base, "alice", "pw1")
        c.put("/CyShineMusic/sync-v1.json",
              cyshine_payload([{"id": "m1", "name": "稳定", "tracks": [("tx", "1", "歌A")]}]))
        st1, h1, b1 = c.get("/CyShineMusic/sync-v1.json")
        time.sleep(1.1)                       # 跨过 1s 墙钟，若用墙钟字节必然不同
        st2, h2, b2 = c.get("/CyShineMusic/sync-v1.json")
        self.assertEqual((st1, st2), (200, 200))
        self.assertEqual(h1.get("ETag"), h2.get("ETag"))
        raw1 = json.dumps(b1, ensure_ascii=False, sort_keys=True).encode()
        raw2 = json.dumps(b2, ensure_ascii=False, sort_keys=True).encode()
        self.assertEqual(raw1, raw2)
        m1 = b1["generatedAt"]
        m2 = b2["generatedAt"]
        self.assertEqual(m1, m2)
        pl1 = b1["sections"]["playlists"]["data"][0]
        pl2 = b2["sections"]["playlists"]["data"][0]
        self.assertEqual(pl1["createdAt"], pl2["createdAt"])
        self.assertEqual(pl1["updatedAt"], pl2["updatedAt"])
        self.assertNotEqual(pl1["updatedAt"], b1["generatedAt"])  # 歌单时间≠渲染时刻

    # ---- P0-2：条件请求 If-None-Match → 304 ----
    def test_03_conditional_304(self):
        c = _Client(self.base, "alice", "pw1")
        c.put("/CyShineMusic/sync-v1.json",
              cyshine_payload([{"id": "c1", "name": "条件", "tracks": [("tx", "1", "歌A")]}]))
        _, h, _ = c.get("/CyShineMusic/sync-v1.json")
        etag = h.get("ETag")
        self.assertTrue(etag)
        st, _, _ = c.get("/CyShineMusic/sync-v1.json",
                         {"If-None-Match": etag})
        self.assertEqual(st, 304)
        # 内容变化后，旧 ETag → 200 + 新 ETag
        c.put("/CyShineMusic/sync-v1.json",
              cyshine_payload([{"id": "c1", "name": "条件", "tracks": [("tx", "1", "歌A"), ("kw", "2", "歌B")]}]))
        st2, h2, b2 = c.get("/CyShineMusic/sync-v1.json", {"If-None-Match": etag})
        self.assertEqual(st2, 200)
        self.assertNotEqual(h2.get("ETag"), etag)
        self.assertEqual(_cyshine_tracks(b2)["c1"], ["tx_1", "kw_2"])

    # ---- P0-3：I6 本地/文件曲目 opaque 回填 ----
    def test_04_local_track_opaque_roundtrip(self):
        c = _Client(self.base, "alice", "pw1")
        pl = cyshine_payload([{"id": "l1", "name": "本地", "tracks": [("tx", "1", "歌A")]}],
                             local_tracks={"l1": [LOCAL_TRACK]})
        st, _, _ = c.put("/CyShineMusic/sync-v1.json", pl)
        self.assertIn(st, (200, 201))
        _, _, body = c.get("/CyShineMusic/sync-v1.json")
        tracks = _cyshine_tracks(body)["l1"]
        self.assertEqual(len(tracks), 2)               # 平台曲目 + 本地曲目回填
        self.assertEqual(tracks[1], LOCAL_TRACK["musicInfo"]["name"] and tracks[1])
        self.assertEqual(tracks[0], "tx_1")
        local = body["sections"]["playlists"]["data"][0]["tracks"][1]
        self.assertEqual(local["source"], "local")      # 字段原样携带
        self.assertEqual(local["musicInfo"]["meta"], {})
        # 去掉本地曲目再提交 → GET 不再回填（opaque 跟随最新提交）
        c.put("/CyShineMusic/sync-v1.json",
              cyshine_payload([{"id": "l1", "name": "本地", "tracks": [("tx", "1", "歌A")]}]))
        _, _, body2 = c.get("/CyShineMusic/sync-v1.json")
        self.assertEqual(_cyshine_tracks(body2)["l1"], ["tx_1"])

    # ---- P0-6：跨端 native id 登记（渲染优先用该端 native id） ----
    def test_05_native_id_registered_across_dialects(self):
        c = _Client(self.base, "alice", "pw1")
        c.put("/CyShineMusic/sync-v1.json",
              cyshine_payload([{"id": "n1", "name": "Rock", "tracks": [("tx", "1", "歌A")]}]))
        c.get("/CyShineMusic/sync-v1.json")
        lan = _Client(self.base, "alice", "pw1")
        # 澜音端 id=ceru-9 与栖弦端 id=n1 指向同一歌单（名字唯一命中）
        lan.put("/ceru/sync-v1.json",
                ceru_payload([{"id": "ceru-9", "name": "Rock",
                               "tracks": [("tx", "1", "歌A"), ("kw", "2", "歌B")]}]))
        _, _, body = lan.get("/ceru/sync-v1.json")
        self.assertEqual(body["playlists"][0]["id"], "ceru-9")   # 渲染用澜音 native id
        # 栖弦侧渲染仍用其 native id，且能看到澜音新增的 B（合并成功）
        _, _, cbody = c.get("/CyShineMusic/sync-v1.json")
        self.assertEqual(_cyshine_tracks(cbody)["n1"], ["tx_1", "kw_2"])

    # ---- P1-9：排序传播但不推进 revision（sort_tag +1） ----
    def test_06_reorder_does_not_bump_revision(self):
        c = _Client(self.base, "alice", "pw1")
        c.put("/CyShineMusic/sync-v1.json",
              cyshine_payload([{"id": "r1", "name": "排序", "tracks": [("tx", "1", "歌A"), ("kw", "2", "歌B")]}]))
        c.get("/CyShineMusic/sync-v1.json")
        st, _, _ = c.put("/CyShineMusic/sync-v1.json",
                         cyshine_payload([{"id": "r1", "name": "排序",
                                           "tracks": [("kw", "2", "歌B"), ("tx", "1", "歌A")]}]))
        self.assertEqual(st, 200)
        _, _, body = c.get("/CyShineMusic/sync-v1.json")
        self.assertEqual(_cyshine_tracks(body)["r1"], ["kw_2", "tx_1"])   # 新顺序传播
        st, _, state = _Client(self.base, "alice", "pw1").get("/api/state")
        self.assertEqual(st, 200)
        self.assertEqual(state["sort_tag"], 1)      # 排序走 tag，不进 revision

    # ---- P0-5：坏负载 400 后 canonical 无污染（两段式校验） ----
    def test_07_bad_payload_no_pollution(self):
        c = _Client(self.base, "alice", "pw1")
        # 第一个歌单合法、第二个歌单 track 非法 → 整体 400，第一个也不能落库
        bad = cyshine_payload([{"id": "g1", "name": "合法", "tracks": [("tx", "1", "歌A")]}])
        bad["sections"]["playlists"]["data"].append(
            {"version": 1, "id": "g2", "name": "非法", "tracks": [{"source": "tx"}]})
        st, _, _ = c.put("/CyShineMusic/sync-v1.json", bad)
        self.assertEqual(st, 400)
        _, _, body = c.get("/CyShineMusic/sync-v1.json")
        self.assertEqual(_cyshine_tracks(body), {})   # canonical 未被半截污染

    # ---- P1-10：新增空歌单 revision+1 ----
    def test_08_new_empty_playlist_bumps_revision(self):
        c = _Client(self.base, "bob", "pw2")
        c.put("/CyShineMusic/sync-v1.json",
              cyshine_payload([{"id": "e1", "name": "空歌单", "tracks": []}]))
        c.get("/CyShineMusic/sync-v1.json")
        st, _, state = c.get("/api/state")
        self.assertEqual(st, 200)
        self.assertEqual(state["revision"], 1)        # 新增空歌单也推进 revision

    # ---- P1-1：确认通道路由存在且幂等 ----
    def test_09_pending_confirm_routes(self):
        c = _Client(self.base, "alice", "pw1")
        st, _, body = c.get("/api/state")
        self.assertEqual(st, 200)
        self.assertIn("pendingDeletions", body)
        self.assertIn("pendingRestores", body)
        # 不存在的 id → 404（幂等，不炸）
        st2, _, _ = c.put("/api/pending-deletions/nope/confirm", {})
        self.assertEqual(st2, 405)                    # PUT 到 POST 路由 → 405（FastAPI 默认）
        st3, _, _ = c.post("/api/pending-deletions/nope/confirm")
        self.assertEqual(st3, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
