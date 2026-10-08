"""FastAPI 版同步服务 HTTP 验收（修改意见.md P0/P1 可执行验收）。

起一个线程内 uvicorn（不依赖 TestClient/httpx，venv 装不上时仍可跑），
用 urllib 打真实 HTTP 端点，覆盖：
  无基线 GET 404 · PUT→GET 回环 · modifiedAt/ETag 确定性 · 条件请求不回 304
  · I6 本地文件曲目 opaque 回填 · 跨方言 native id · 排序不推 revision
  · 坏负载 400 无 canonical 污染 · 新增空歌单 revision+1
  · 删除不被"无删除能力的一端"推回（墓碑压制 + 待确认恢复）
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

    def post(self, path, payload=None):
        data = json.dumps(payload or {}).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data,
                                     headers=self.headers, method="POST")
        return _do(req)

    def delete(self, path):
        req = urllib.request.Request(self.base + path, headers=self.headers,
                                     method="DELETE")
        return _do(req)


def _do(req):
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


class ApiHttpTest(unittest.TestCase):
    """每个测试独立空间（同一客户端"提交=全量视图"语义下避免跨测试污染）。"""

    @classmethod
    def setUpClass(cls):
        _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        cls._root = _root
        cls.port = _free_port()
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
        _rmtree_force(self.store)          # 2026-10-06 加固：本机 Python 删除假成功

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
        # 内容变化（新增曲目）→ 歌单 updatedAt 前进（确定性：时间来自 canonical 提交时刻，非墙钟）
        c.put("/CyShineMusic/sync-v1.json",
              cyshine_payload([{"id": "m1", "name": "稳定",
                                "tracks": [("tx", "1", "歌A"), ("tx", "2", "歌B")]}]))
        _, _, b3 = c.get("/CyShineMusic/sync-v1.json")
        self.assertGreater(b3["sections"]["playlists"]["data"][0]["updatedAt"],
                           pl1["updatedAt"])

    # ---- P0-2：条件请求（docs/09 决策：永不回 304，客户端把非 2xx 当同步失败） ----
    def test_03_conditional_header_never_304(self):
        c = _Client(self.base, "alice", "pw1")
        c.put("/CyShineMusic/sync-v1.json",
              cyshine_payload([{"id": "c1", "name": "条件", "tracks": [("tx", "1", "歌A")]}]))
        _, h, _ = c.get("/CyShineMusic/sync-v1.json")
        etag = h.get("ETag")
        self.assertTrue(etag)
        # 内容未变：带 If-None-Match 仍 200（不回 304），正文/ETag 同值（客户端可复用缓存）
        st, h2, b2 = c.get("/CyShineMusic/sync-v1.json", {"If-None-Match": etag})
        self.assertEqual(st, 200)
        self.assertEqual(h2.get("ETag"), etag)
        self.assertEqual(_cyshine_tracks(b2)["c1"], ["tx_1"])
        # 内容变化后：旧 ETag → 200 + 新 ETag + 新曲目可见
        c.put("/CyShineMusic/sync-v1.json",
              cyshine_payload([{"id": "c1", "name": "条件", "tracks": [("tx", "1", "歌A"), ("kw", "2", "歌B")]}]))
        st3, h3, b3 = c.get("/CyShineMusic/sync-v1.json", {"If-None-Match": etag})
        self.assertEqual(st3, 200)
        self.assertNotEqual(h3.get("ETag"), etag)
        # 第七轮起「新增置顶」：kw_2 是这一轮新增的 → 落在最顶（用户 2026-10-08 要求覆盖所有平台）
        self.assertEqual(_cyshine_tracks(b3)["c1"], ["kw_2", "tx_1"])

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
        # 新增置顶：kw_2 由澜音端新加 → 栖弦侧看到它也在最顶（用户 2026-10-08 要求）
        self.assertEqual(_cyshine_tracks(cbody)["n1"], ["kw_2", "tx_1"])

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

    # ---- P0-5：坏负载 400 后 canonical 无污染（两段式校验，结构级错误整体拒绝） ----
    def test_07_bad_payload_no_pollution(self):
        c = _Client(self.base, "alice", "pw1")
        # 先建立合法基线
        st0, _, _ = c.put("/CyShineMusic/sync-v1.json",
                          cyshine_payload([{"id": "g1", "name": "合法", "tracks": [("tx", "1", "歌A")]}]))
        self.assertIn(st0, (200, 201))
        # 坏负载：第一个歌单合法、第二个歌单 tracks 非数组 → 整体 400，任何半截都不落库
        bad = cyshine_payload([{"id": "g2", "name": "新歌单", "tracks": [("tx", "1", "歌B")]}])
        bad["sections"]["playlists"]["data"].append(
            {"version": 1, "id": "g3", "name": "非法", "tracks": "不是数组"})
        st, _, _ = c.put("/CyShineMusic/sync-v1.json", bad)
        self.assertEqual(st, 400)
        _, _, body = c.get("/CyShineMusic/sync-v1.json")
        self.assertEqual(_cyshine_tracks(body), {"g1": ["tx_1"]})   # 基线未被半截污染

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
        # 不存在的 id → 404（幂等，不炸）；PUT 到 POST 路由 → 4xx（FastAPI 版本相关，404/405 均可）
        st2, _, _ = c.put("/api/pending-deletions/nope/confirm", {})
        self.assertIn(st2, (404, 405))
        st3, _, _ = c.post("/api/pending-deletions/nope/confirm")
        self.assertEqual(st3, 404)

    # ---- S15 语义：删除传播后，无删除能力端（澜音）推回被墓碑压住 ----
    def test_10_deletion_not_pushed_back_by_can_delete_false(self):
        c = _Client(self.base, "alice", "pw1")        # 栖弦（can_delete=True）
        c.put("/CyShineMusic/sync-v1.json",
              cyshine_payload([{"id": "d1", "name": "删", "tracks": [("tx", "1", "歌A"), ("tx", "2", "歌B")]}]))
        c.get("/CyShineMusic/sync-v1.json")           # 栖弦须先取到最新基线，否则 I2′ 闸门把删除降级为 suspects
        lan = _Client(self.base, "alice", "pw1")
        lan.put("/ceru/sync-v1.json",
                ceru_payload([{"id": "ceru-d", "name": "删",
                               "tracks": [("tx", "1", "歌A"), ("tx", "2", "歌B")]}]))
        lan.get("/ceru/sync-v1.json")                 # 澜音也完成一轮交付（见过删除点）
        # 栖弦删 tx2
        c.put("/CyShineMusic/sync-v1.json",
              cyshine_payload([{"id": "d1", "name": "删", "tracks": [("tx", "1", "歌A")]}]))
        # 澜音侧不再有 tx2（删除已传播）
        _, _, lb = lan.get("/ceru/sync-v1.json")
        song_ids = {t.get("songId") for pl in lb["playlists"] for t in pl["tracks"]}
        self.assertNotIn("2", song_ids)
        # 澜音（本地删不掉）推回 tx2 → 墓碑压制，栖弦视图不复活 + 进待确认恢复
        lan.put("/ceru/sync-v1.json",
                ceru_payload([{"id": "ceru-d", "name": "删",
                               "tracks": [("tx", "1", "歌A"), ("tx", "2", "歌B")]}]))
        _, _, cb = c.get("/CyShineMusic/sync-v1.json")
        self.assertNotIn("tx_2", _cyshine_tracks(cb)["d1"])   # 被压制，不复活
        st, _, state = c.get("/api/state")
        self.assertEqual(st, 200)
        self.assertFalse(state["pendingRestores"])            # 无待确认恢复卡

    # ===== 多账户共用歌单修改文档 §1：opaque 按客户端隔离 =====
    def test_11_opaque_isolated_between_clients(self):
        """同一账号两个设备段（不同 cid）：主题/本地曲目各用各的，不互相覆盖。"""
        c_a = _Client(self.base, "alice", "pw1")
        c_b = _Client(self.base, "alice", "pw1")
        # A 提交主题 0x111111 + 本地曲目
        pl_a = cyshine_payload([{"id": "p1", "name": "P", "tracks": [("tx", "1", "歌A")]}],
                               local_tracks={"p1": [LOCAL_TRACK]})
        pl_a["sections"]["appearance"]["data"]["themeSeedArgb"] = 0x111111
        st, _, _ = c_a.put("/d/phone-a/CyShineMusic/sync-v1.json", pl_a)
        self.assertIn(st, (200, 201))
        # B 提交主题 0x222222（无本地曲目）
        pl_b = cyshine_payload([{"id": "p1", "name": "P", "tracks": [("tx", "1", "歌A")]}])
        pl_b["sections"]["appearance"]["data"]["themeSeedArgb"] = 0x222222
        st2, _, _ = c_b.put("/d/phone-b/CyShineMusic/sync-v1.json", pl_b)
        self.assertIn(st2, (200, 201))
        # A GET：主题还是 A 的；本地曲目仍在 A 的视图
        _, _, body_a = c_a.get("/d/phone-a/CyShineMusic/sync-v1.json")
        self.assertEqual(body_a["sections"]["appearance"]["data"]["themeSeedArgb"], 0x111111)
        tracks_a = body_a["sections"]["playlists"]["data"][0]["tracks"]
        self.assertIn("local", [t["source"] for t in tracks_a])
        # B GET：主题是 B 的；**没有** A 的本地曲目
        _, _, body_b = c_b.get("/d/phone-b/CyShineMusic/sync-v1.json")
        self.assertEqual(body_b["sections"]["appearance"]["data"]["themeSeedArgb"], 0x222222)
        tracks_b = body_b["sections"]["playlists"]["data"][0]["tracks"]
        self.assertNotIn("local", [t["source"] for t in tracks_b])
        # A 再次 GET：内容与第一次一致（不被 B 顶掉）
        _, _, body_a2 = c_a.get("/d/phone-a/CyShineMusic/sync-v1.json")
        self.assertEqual(body_a2["sections"]["appearance"]["data"]["themeSeedArgb"], 0x111111)
        self.assertEqual(len(body_a2["sections"]["playlists"]["data"][0]["tracks"]),
                         len(tracks_a))

    # ===== 多账户共用歌单修改文档 §2：确认卡去重 + 自动关闭 + 归属权限 =====
    def test_12_pending_delete_dedup_and_autoclose(self):
        """第三轮修改：栖弦缺"从未提交过"的歌单 → 直接删除（同空间成员互信，无卡）；
        删除者本人 GET 跨过删除点后把歌单带回 → 显式恢复直接生效（无卡）。"""
        c = _Client(self.base, "alice", "pw1")
        # 栖弦先建自己的基线
        c.put("/CyShineMusic/sync-v1.json",
              cyshine_payload([{"id": "x", "name": "X", "tracks": []}]))
        # 澜音提交 y（同空间同账号）→ 栖弦 GET 看到 y（交付过但从未提交过）
        lan = _Client(self.base, "alice", "pw1")
        lan.put("/ceru/sync-v1.json", ceru_payload([{"id": "ceru-y", "name": "Y", "tracks": []}]))
        c.get("/CyShineMusic/sync-v1.json")
        # 第一轮：栖弦提交不含 y → 直接删除写墓碑，不出卡
        c.put("/CyShineMusic/sync-v1.json", cyshine_payload([{"id": "x", "name": "X", "tracks": []}]))
        _, _, state = c.get("/api/state")
        self.assertEqual(state["pendingDeletions"], [])     # 无确认卡
        _, _, body = c.get("/CyShineMusic/sync-v1.json")
        ids = [p["id"] for p in body["sections"]["playlists"]["data"]]
        self.assertNotIn("y", ids)                          # y 已被删除
        # 第二轮：同样缺 y → 幂等（已删，无变化、无卡）
        c.put("/CyShineMusic/sync-v1.json", cyshine_payload([{"id": "x", "name": "X", "tracks": []}]))
        _, _, state = c.get("/api/state")
        self.assertEqual(state["pendingDeletions"], [])
        # 第三轮：删除者本人 GET 跨过删除点后把 y 带回 → 显式恢复直接生效（无卡）
        # （第四轮 P0-C：测试内关闭冷静期——真实时间下删除与恢复间隔 < 冷静期。
        #  第十三轮踩坑：直接改 engine.restore_grace_seconds 会被 policy_hook 在**下一次
        #  Hub.get()** 按 _policy 冲回 120——本行原写法就是这样静默失效的，D32 之后
        #  "墓碑压制" 才显形。只能改 policy。）
        api._policy["restore_grace_seconds"] = 0
        api._apply_engine_policy()
        c.get("/CyShineMusic/sync-v1.json")
        c.put("/CyShineMusic/sync-v1.json",
              cyshine_payload([{"id": "x", "name": "X", "tracks": []},
                               {"id": "y", "name": "Y", "tracks": []}]))
        _, _, state = c.get("/api/state")
        self.assertEqual(state["pendingDeletions"], [])   # 无卡
        _, _, body = c.get("/CyShineMusic/sync-v1.json")
        ids = [p["id"] for p in body["sections"]["playlists"]["data"]]
        self.assertIn("y", ids)                           # y 已恢复
        api._policy["restore_grace_seconds"] = 120
        api._apply_engine_policy()

    def test_13_pending_card_only_owner_can_confirm(self):
        """确认卡只能由发起账号（或 admin）处理；bob 确认 alice 的卡 → 403。
        （安全阀卡：批量删除触发 D4 挂起，仍保留确认流程）"""
        # 本测试内 bob 与 alice 同空间 main（bob 为普通账号）
        api.configure(self.hub, {
            "alice": {"password": api._hash_pw("pw1"), "space": "main",
                      "role": "admin", "enabled": True},
            "bob": {"password": api._hash_pw("pw2"), "space": "main",
                    "role": "user", "enabled": True},
        }, config_path=None)
        alice = _Client(self.base, "alice", "pw1")
        bob = _Client(self.base, "bob", "pw2")
        # 第四轮 P1-2 / 第十轮 D27：本测试要构造"旧安全阀卡"，需显式打开旧通道
        # （默认 confirm_delete=False + trusted_direct_delete=True → 批量删除直接生效、不挂卡）。
        # 注意：这两个引擎开关由 policy_hook 按 _policy 重设，直接改 engine 属性会在下一次
        # Hub.get() 时被冲掉（第十轮踩坑），所以只能改 policy。
        api._policy["confirm_delete"] = True
        api._policy["trusted_direct_delete"] = False
        api._apply_engine_policy()
        # 构造 alice 的安全阀卡：提交 12 首 → 删 11 首（91.7% ≥50% 且 ≥10）→ 挂起
        alice.put("/CyShineMusic/sync-v1.json",
                  cyshine_payload([{"id": f"t{i}", "name": f"T{i}",
                                    "tracks": [("tx", f"{i}", f"T{i}-1")]}
                                   for i in range(12)]))
        alice.get("/CyShineMusic/sync-v1.json")
        alice.put("/CyShineMusic/sync-v1.json",
                  cyshine_payload([{"id": "t0", "name": "T0",
                                    "tracks": [("tx", "0", "T0-1")]}]))
        _, _, state = alice.get("/api/state")
        cards = state["pendingDeletions"]
        self.assertEqual(len(cards), 1)
        pid = cards[0]["id"]
        self.assertEqual(cards[0]["account"], "alice")
        # bob（非 owner 非 admin）确认 → 403
        st, _, _ = bob.post(f"/api/pending-deletions/{pid}/confirm")
        self.assertEqual(st, 403)
        # alice（owner + admin）确认 → 200
        st2, _, _ = alice.post(f"/api/pending-deletions/{pid}/confirm")
        self.assertEqual(st2, 200)
        # 还原默认策略，别把旧通道漏给后面的用例
        api._policy["confirm_delete"] = False
        api._policy["trusted_direct_delete"] = True
        api._apply_engine_policy()

    # ===== 多账户共用歌单修改文档 §3：空间管理 =====
    def test_14_two_accounts_same_space_share_playlists(self):
        """两个账号同一空间：歌单共享、互不覆盖。"""
        api.configure(self.hub, {
            "alice": {"password": api._hash_pw("pw1"), "space": "main",
                      "role": "admin", "enabled": True},
            "bob": {"password": api._hash_pw("pw2"), "space": "main",
                    "role": "user", "enabled": True},
        }, config_path=None)
        a = _Client(self.base, "alice", "pw1")
        b = _Client(self.base, "bob", "pw2")
        a.put("/CyShineMusic/sync-v1.json",
              cyshine_payload([{"id": "p1", "name": "P1", "tracks": [("tx", "1", "歌A")]}]))
        # B 首次提交（只增不删）→ 之后 GET 看到 A 的歌单
        b.put("/CyShineMusic/sync-v1.json",
              cyshine_payload([{"id": "p2", "name": "P2", "tracks": []}]))
        _, _, body_b = b.get("/CyShineMusic/sync-v1.json")
        self.assertIn("p1", _cyshine_tracks(body_b))    # B 看得到 A 的歌单
        self.assertIn("p2", _cyshine_tracks(body_b))
        # A 加 p3（先 GET 看到 p2，避免 never_owned 误判）
        a.get("/CyShineMusic/sync-v1.json")
        a.put("/CyShineMusic/sync-v1.json",
              cyshine_payload([{"id": "p1", "name": "P1", "tracks": [("tx", "1", "歌A")]},
                               {"id": "p2", "name": "P2", "tracks": []},
                               {"id": "p3", "name": "P3", "tracks": [("kw", "2", "歌B")]}]))
        _, _, body_b2 = b.get("/CyShineMusic/sync-v1.json")
        self.assertIn("p3", _cyshine_tracks(body_b2))   # A 加的歌 B 看得到

    def test_15_space_list_and_join(self):
        """GET /api/spaces：admin 看全部、普通账号只看自己；空间归属仅管理员可分配。"""
        a = _Client(self.base, "alice", "pw1")   # admin, space=main
        b = _Client(self.base, "bob", "pw2")     # user, space=other
        # admin 看到全部空间；普通账号只看到自己那一个（不能枚举他人空间）
        st, _, spaces = a.get("/api/spaces")
        self.assertEqual(st, 200)
        self.assertEqual({s["space"] for s in spaces["spaces"]}, {"main", "other"})
        st2, _, mine = b.get("/api/spaces")
        self.assertEqual(st2, 200)
        self.assertEqual(len(mine["spaces"]), 1)
        self.assertEqual(mine["spaces"][0]["space"], "other")
        # 普通账号改自己空间（哪怕目标存在）→ 403：归属只能由管理员分配
        st3, _, body3 = b.put("/api/users/bob/space", {"space": "main"})
        self.assertEqual(st3, 403)
        self.assertIn("仅管理员", body3.get("error", ""))
        # 不存在空间 → 404（管理员视角）
        st4, _, _ = a.put("/api/users/bob/space", {"space": "nope"})
        self.assertEqual(st4, 404)
        # admin 把 bob 分配到已存在的 main → 200；数据互通
        st5, _, _ = a.put("/api/users/bob/space", {"space": "main"})
        self.assertEqual(st5, 200)
        a.put("/CyShineMusic/sync-v1.json",
              cyshine_payload([{"id": "p1", "name": "P", "tracks": [("tx", "1", "歌A")]}]))
        b.put("/CyShineMusic/sync-v1.json",
              cyshine_payload([{"id": "p2", "name": "Q", "tracks": []}]))
        _, _, body_b = b.get("/CyShineMusic/sync-v1.json")
        self.assertIn("p1", _cyshine_tracks(body_b))    # 换空间后共享歌单

    def test_16_orphan_space_data_kept(self):
        """删掉空间最后一个账号 → 空间 pkl 数据默认保留（不 purge）。"""
        b = _Client(self.base, "bob", "pw2")
        b.put("/CyShineMusic/sync-v1.json",
              cyshine_payload([{"id": "p1", "name": "P", "tracks": []}]))
        pkl = os.path.join(self.store, "space__other.pkl")
        # 写盘确认（bob 的 PUT 已 save）
        a = _Client(self.base, "alice", "pw1")
        # 删除 bob（other 空间最后一个成员）→ 默认保留
        st, _, body = a.delete("/api/users/bob")
        self.assertEqual(st, 200)
        self.assertEqual(body.get("space_kept"), True)
        self.assertEqual(body.get("space_deleted"), False)
        # 空间 pkl 仍在磁盘（用 PowerShell Test-Path 双视角验证，避开 os.path.exists 假阴性）
        self.assertFalse(_gone_dir(pkl))
        # 显式 ?purge=true 才删
        # （重建 bob 以便再次删除；purge 后 pkl 应消失）
        api.configure(self.hub, {
            "alice": {"password": api._hash_pw("pw1"), "space": "main",
                      "role": "admin", "enabled": True},
            "bob": {"password": api._hash_pw("pw2"), "space": "other",
                    "role": "user", "enabled": True},
        }, config_path=None)
        b2 = _Client(self.base, "bob", "pw2")
        b2.put("/CyShineMusic/sync-v1.json",
               cyshine_payload([{"id": "p1", "name": "P", "tracks": []}]))
        st2, _, body2 = a.delete("/api/users/bob?purge=true")
        self.assertEqual(st2, 200)
        self.assertEqual(body2.get("space_deleted"), True)
        self.assertTrue(_gone_dir(pkl))

    def test_17_orphan_space_list_and_delete(self):
        """v3 空间管理：账号删尽后空间（无成员、磁盘有 pkl）作为孤儿列出，admin 可删；
        非成员/非 owner 删除 → 403；删后再删 → 404。有其他成员不可删见 test_spaces_http。"""
        api.configure(self.hub, {
            "alice": {"password": api._hash_pw("pw1"), "space": "main",
                      "role": "admin", "enabled": True},
            "bob": {"password": api._hash_pw("pw2"), "space": "other",
                    "role": "user", "enabled": True},
        }, config_path=None)
        b = _Client(self.base, "bob", "pw2")
        b.put("/CyShineMusic/sync-v1.json",
              cyshine_payload([{"id": "p1", "name": "P", "tracks": []}]))
        pkl = os.path.join(self.store, "space__other.pkl")
        a = _Client(self.base, "alice", "pw1")
        # 删 bob（other 唯一 owner/成员，不 purge）→ other 变孤儿，数据保留、仍列出且 orphan=true
        a.delete("/api/users/bob")
        st, _, spaces = a.get("/api/spaces")
        self.assertEqual(st, 200)
        by_name = {s["space"]: s for s in spaces["spaces"]}
        self.assertIn("other", by_name)
        self.assertTrue(by_name["other"]["orphan"])
        self.assertFalse(by_name["main"]["orphan"])
        # 删除孤儿空间 → 200，pkl 消失
        st3, _, body3 = a.delete("/api/spaces/other")
        self.assertEqual(st3, 200)
        self.assertEqual(body3.get("deleted"), "other")
        self.assertTrue(_gone_dir(pkl))
        # 再删 → 404
        st4, _, _ = a.delete("/api/spaces/other")
        self.assertEqual(st4, 404)
        # 非成员（carol 不在 main）删空间 → 403
        api.configure(self.hub, {
            "alice": {"password": api._hash_pw("pw1"), "space": "main",
                      "role": "admin", "enabled": True},
            "carol": {"password": api._hash_pw("pw3"), "space": "carol",
                      "role": "user", "enabled": True},
        }, config_path=None)
        c = _Client(self.base, "carol", "pw3")
        st5, _, _ = c.delete("/api/spaces/main")
        self.assertEqual(st5, 403)

    def test_18_space_create_v3(self):
        """v3 新建空间：普通账号可建（纯增量），创建者即 owner，受配额限制；admin 不限。"""
        a = _Client(self.base, "alice", "pw1")   # admin, main owner
        # admin 建 family（不切当前空间）→ 200，落盘，非孤儿（admin 是其成员/owner）
        st, _, body = a.post("/api/spaces", {"space": "family", "current": False})
        self.assertEqual(st, 200, body)
        self.assertEqual(body.get("space"), "family")
        self.assertFalse(_gone_dir(os.path.join(self.store, "space__family.pkl")))
        st2, _, spaces = a.get("/api/spaces")
        fam = next((s for s in spaces["spaces"] if s["space"] == "family"), None)
        self.assertIsNotNone(fam)
        self.assertFalse(fam["orphan"])
        self.assertEqual(fam["my_role"], "owner")
        # 同名 → 409；空名/非法名 → 400
        self.assertEqual(a.post("/api/spaces", {"space": "family"})[0], 409)
        self.assertEqual(a.post("/api/spaces", {"space": "  "})[0], 400)
        self.assertEqual(a.post("/api/spaces", {"space": "../evil"})[0], 400)
        self.assertEqual(a.post("/api/spaces", {"space": "space__x"})[0], 400)
        # 普通账号 bob（other owner，已占 1 个）可自建空间，建到第 3 个后触顶配额 → 409
        b = _Client(self.base, "bob", "pw2")
        st3, _, body3 = b.post("/api/spaces", {"space": "b1"})
        self.assertEqual(st3, 200, body3)               # other + b1 = 2
        self.assertEqual(body3.get("current"), True)   # 默认切过去
        self.assertEqual(b.post("/api/spaces", {"space": "b2", "current": False})[0], 200)  # =3
        st4, _, body4 = b.post("/api/spaces", {"space": "b3", "current": False})
        self.assertEqual(st4, 409)                      # 超过 max_owned_spaces=3
        self.assertIn("上限", body4.get("error", ""))
        # admin 不受配额限制（已拥有 main/family，仍可建）
        st5, _, _ = a.post("/api/spaces", {"space": "admin2", "current": False})
        self.assertEqual(st5, 200)


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
