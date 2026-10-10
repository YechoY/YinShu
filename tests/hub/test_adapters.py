"""第六轮 §1.7：适配器插件化专项测试。

覆盖计划 §1.7 六条：
1. REGISTRY 各方言 roots/files 均能被路由命中（含 /d/{device}/ 形式）
2. 注册表自检：name/roots/files/parse/render/capabilities 齐全；files 不重复
3. file_fallback 只对开启的方言生效（当前两方言未开启 → 文件名兜底仍 404）
4. 兼容层：from hub import adapters 的旧符号仍在，且与注册表是同一个对象
5. 引擎注入后 KNOWN_DIALECTS == 注册表 keys；未知方言仍被隔离
6. 回归：关键路径（含 /d/x/）GET/PUT 响应（状态码 + 正文 + ETag + 无基线 404/200 空视图）逐条一致
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error

from engine.engine import SyncSpace
from hub import adapters
from hub import api
from hub.adapters import REGISTRY
from hub.store import Hub

from tests.hub.helpers import (HttpClient, cyshine_payload, ceru_payload,
                               rmtree_force)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_ADAPTER_FILES = {"sync-v1.json"}   # 当前全部方言的 files（§2 洛雪加入后由注册表自检保证不重复）


class RegistryTest(unittest.TestCase):
    """§1.7.1-2：路由命中 + 注册表自检（纯模块级断言，不起服务器）。"""

    def test_01_routes_registered_for_every_dialect(self):
        # 同 path 的 GET/PUT 各注册一条 → 收集时按 path 合并 methods
        routes: dict = {}
        for r in api.app.routes:
            p = getattr(r, "path", None)
            m = set(getattr(r, "methods", set()) or set())
            if p:
                routes.setdefault(p, set()).update(m)
        for d in REGISTRY.values():
            for root in d.roots:
                for f in d.files:
                    p = f"/{root}/{f}"
                    dp = f"/d/{{device}}/{root}/{f}"
                    self.assertIn(p, routes, f"{d.name} 根路径 {p} 未注册")
                    self.assertIn(dp, routes, f"{d.name} 设备路径 {dp} 未注册")
                    self.assertTrue({"GET", "PUT"} <= routes[p], f"{p} 缺 GET/PUT")
                    self.assertTrue({"GET", "PUT"} <= routes[dp], f"{dp} 缺 GET/PUT")

    def test_02_registry_entries_self_check(self):
        self.assertIn("cyshine-v1", REGISTRY)
        self.assertIn("ceru-plugin", REGISTRY)
        seen_paths = set()
        for name, d in REGISTRY.items():
            self.assertEqual(d.name, name)
            self.assertIsInstance(d.roots, tuple) and self.assertTrue(d.roots)
            self.assertIsInstance(d.files, tuple) and self.assertTrue(d.files)
            self.assertTrue(callable(d.parse))
            self.assertTrue(callable(d.render))
            self.assertIsInstance(d.capabilities, dict)
            self.assertIn("delete_track", d.capabilities)
            # 方言内 files 不重复；跨方言 (root, file) 路径组合不冲突（路由唯一）
            self.assertEqual(len(d.files), len(set(d.files)), f"{name} files 重复")
            for root in d.roots:
                for f in d.files:
                    p = f"/{root}/{f}"
                    self.assertNotIn(p, seen_paths, f"路由路径跨方言重复: {p}")
                    seen_paths.add(p)

    def test_03_capabilities_derived_from_registry(self):
        # 兼容层能力表与注册表条目是同一份数据（单一来源，路由/删除判定都用它）
        for name, d in REGISTRY.items():
            self.assertEqual(adapters.CAPABILITIES[name], d.capabilities)


class CompatLayerTest(unittest.TestCase):
    """§1.7.4：兼容层旧符号仍在且指向注册表同一对象。"""

    def test_04_legacy_symbols_are_registry_objects(self):
        # parse 无包装：注册表条目与兼容层旧符号是同一个函数
        self.assertIs(adapters.parse_cyshine, REGISTRY["cyshine-v1"].parse)
        self.assertIs(adapters.parse_ceru, REGISTRY["ceru-plugin"].parse)
        # render 在注册表里是 RenderContext 薄包装：同一输入行为等价于旧符号
        from hub.adapters.base import RenderContext
        ctx = RenderContext(
            view={"p1": {"name": "A", "tracks": ["tx:1"]}}, meta_pool={"tx:1": {"title": "歌A"}},
            native_ids={"p1": "n1"}, pending_cleanup=None, opaque={"appearance": None},
            opaque_tracks=None, revision=1, generated_at="2026-10-08T00:00:00.000Z",
            playlist_times={},
        )
        expected = adapters.render_cyshine(
            ctx.view, ctx.meta_pool, ctx.native_ids, ctx.opaque, ctx.revision,
            ctx.generated_at, pl_times=ctx.playlist_times, opaque_tracks=ctx.opaque_tracks)
        self.assertEqual(REGISTRY["cyshine-v1"].render(ctx), expected)
        expected_ceru = adapters.render_ceru(ctx.view, ctx.meta_pool, ctx.native_ids,
                                             ctx.pending_cleanup, ctx.generated_at)
        self.assertEqual(REGISTRY["ceru-plugin"].render(ctx), expected_ceru)
        self.assertEqual(adapters.KEY_SEP, ":")
        self.assertTrue(callable(adapters.identity_key))
        self.assertTrue(issubclass(adapters.ParseError, Exception))


class EngineInjectionTest(unittest.TestCase):
    """§1.7.5：引擎 KNOWN_DIALECTS 由注册表注入；未知方言仍隔离。"""

    def test_05_known_dialects_injected_and_unknown_isolated(self):
        self.assertEqual(SyncSpace.KNOWN_DIALECTS, set(REGISTRY))
        sp = SyncSpace("inject-test")
        sp.register_client("ghost", dialect="ghost-v99", identity_verified=True)
        r = sp.merge("ghost", {"playlists": [{"native_id": "p", "name": "P",
                                              "tracks": [{"source": "tx", "songId": "1"}]}],
                               "opaque": None, "create_only": False})
        self.assertTrue(r.meta.get("isolated"))     # 未知方言：隔离存储、不参与删除判定
        self.assertIn("ghost", sp.quarantine)
        # 既有方言不被隔离
        sp.register_client("ok", dialect="cyshine-v1", identity_verified=True)
        r2 = sp.merge("ok", {"playlists": [{"native_id": "p", "name": "P",
                                            "tracks": [{"source": "tx", "songId": "1"}]}],
                             "opaque": None, "create_only": False})
        self.assertFalse(r2.meta.get("isolated"))


class FileFallbackTest(unittest.TestCase):
    """§1.7.3：file_fallback 只对开启的方言生效（当前两方言未开启 → 兜底仍 404）。

    用 uvicorn 线程实测：地址带任意目录名但 basename 命中 files 且方言未开
    file_fallback → 404（不静默分派）；而常规 /api/state 与既有行为不受影响。
    """

    @classmethod
    def setUpClass(cls):
        _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        cls._root = _root
        cls.port = _free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        import uvicorn
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
        self.store = _tf.mkdtemp(prefix="adapters_", dir=os.path.join(self._root, "tmp"))
        self.hub = api.Hub(self.store)
        api.configure(self.hub, {
            "alice": {"password": api._hash_pw("pw1"), "space": "main",
                      "role": "admin", "enabled": True},
        })
        self.c = HttpClient(self.base, "alice", "pw1")

    def test_03_fallback_404_for_non_enabled_dialect(self):
        # basename 命中 files（sync-v1.json）但当前无方言开启 file_fallback → 兜底不分派，404
        st, _, body = self.c.put("/arbitrary/dir/sync-v1.json",
                                 ceru_payload([{"id": "p", "name": "P",
                                               "tracks": [("tx", "1", "歌A")]}]))
        self.assertEqual(st, 404, body)
        st2, _, _ = self.c.get("/whatever/nested/sync-v1.json")
        self.assertEqual(st2, 404)
        # /api/* 既有行为不变（state 仅 GET）
        st3, _, _ = self.c.get("/api/state")
        self.assertNotEqual(st3, 404)


class RegressionHttpTest(unittest.TestCase):
    """§1.7.6：改造前后关键路径响应逐条一致（权威 = 既有 test_api_http 全绿；
    这里抽关键路径做快照断言）。"""

    @classmethod
    def setUpClass(cls):
        _root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        cls._root = _root
        cls.port = _free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        import uvicorn
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
        self.store = _tf.mkdtemp(prefix="adapters_reg_", dir=os.path.join(self._root, "tmp"))
        self.hub = api.Hub(self.store)
        api.configure(self.hub, {
            "alice": {"password": api._hash_pw("pw1"), "space": "main",
                      "role": "admin", "enabled": True},
        })
        self.c = HttpClient(self.base, "alice", "pw1")

    def test_06_key_paths_behavior_unchanged(self):
        # 无基线：栖弦 404、澜音 200 空视图（探测兼容）——与改造前逐字一致
        st, _, body = self.c.get("/CyShineMusic/sync-v1.json")
        self.assertEqual(st, 404)
        self.assertEqual(body, {"error": "no baseline"})
        st2, _, body2 = self.c.get("/ceru/sync-v1.json")
        self.assertEqual(st2, 200)
        self.assertEqual(body2["schemaVersion"], 1)
        self.assertEqual(body2["playlists"], [])
        # 首 PUT 建基线 → 200；确定性交付 ETag 在 GET 响应（P0-2：内容不变同字节同值）
        st3, _, _ = self.c.put("/CyShineMusic/sync-v1.json",
                               cyshine_payload([{"id": "d1", "name": "A",
                                                 "tracks": [("tx", "1", "歌A")]}]))
        self.assertEqual(st3, 200)
        st3g, h3, _ = self.c.get("/CyShineMusic/sync-v1.json")
        self.assertEqual(st3g, 200)
        self.assertIn("ETag", h3)
        # 设备段命中：/d/x/… 是新客户端（无基线），栖弦 404；澜音走 200 空视图探测兼容
        st4, _, body4 = self.c.get("/d/x/CyShineMusic/sync-v1.json")
        self.assertEqual(st4, 404)
        self.assertEqual(body4, {"error": "no baseline"})
        st5, _, body5 = self.c.get("/d/x/ceru/sync-v1.json")
        self.assertEqual(st5, 200)
        self.assertEqual(body5["playlists"], [])
        st6, _, _ = self.c.put("/d/x/ceru/sync-v1.json",
                               ceru_payload([{"id": "p", "name": "A",
                                              "tracks": [("tx", "1", "歌A")]}]))
        self.assertEqual(st6, 200)
        st7, _, _ = self.c.get("/d/x/ceru/sync-v1.json")
        self.assertEqual(st7, 200)
        # 设备身份归一化：非法设备名 → 400（M7；X-Hub-Device 头优先）
        from urllib.parse import quote
        st8, _, _ = self.c.get(f"/d/{quote('非法名')}/CyShineMusic/sync-v1.json")
        self.assertEqual(st8, 400)
        st9, _, _ = self.c.get("/CyShineMusic/sync-v1.json",
                               extra_headers={"X-Hub-Device": "x/y"})
        self.assertEqual(st9, 400)


class CyshineRenderMetaTest(unittest.TestCase):
    """回归（2026-10-10 真机）：洛雪提交 → 栖弦渲染，musicInfo.meta 必须是完整元数据。

    栖弦列表页读曲目顶层 picUrl（一直正常），播放详情页读
    music.meta.picUrl（封面）与 meta.qualitys（可切换音质）——meta 只回填
    {songId} 时表现为"列表页有封面、进播放页封面和音质全没"。
    """

    def test_lx_origin_track_renders_full_music_meta(self):
        from hub.adapters.cyshine import render_cyshine
        from hub.adapters.lx import parse_lx
        payload = {"version": "2", "lastModified": 1700000000000, "data": {
            "userList": [{"id": "1", "name": "Rock", "list": [{
                "id": "wy_25906124", "name": "歌A", "singer": "歌手A",
                "source": "wy", "interval": "03:27",
                "meta": {"id": 25906124, "songId": 25906124,
                         "albumName": "专辑A", "picUrl": "http://pic/a.jpg",
                         "qualitys": [{"type": "flac", "size": "31MB"},
                                      {"type": "128k", "size": "3MB"}],
                         "_qualitys": {"flac": {"size": "31MB"},
                                       "128k": {"size": "3MB"}},
                         "strMediaMid": "002abc", "albumId": "123"},
            }]}]}}
        submission, meta_delta, _opaque, _w = parse_lx(payload)
        from hub.adapters.base import identity_key
        view = {"pl1": {"name": "Rock", "tracks": [
            identity_key("wy", "25906124")]}}
        out = render_cyshine(view, meta_delta, {"pl1": "1"}, None, 1,
                             "2026-10-10T00:00:00Z")
        track = out["sections"]["playlists"]["data"][0]["tracks"][0]
        mm = track["musicInfo"]["meta"]
        self.assertEqual(mm["songId"], "25906124")           # 规范身份
        self.assertEqual(mm["picUrl"], "http://pic/a.jpg")   # 播放页封面
        self.assertEqual(mm["albumName"], "专辑A")
        self.assertEqual(mm["id"], 25906124)                 # 平台原生数字 id
        self.assertTrue(mm["qualitys"])                      # 可切换音质非空
        self.assertEqual(track["picUrl"], "http://pic/a.jpg")   # 列表页封面
        self.assertEqual(track["musicId"], "wy_25906124")
        self.assertEqual(track["source"], "wy")

    def test_bare_pool_falls_back_to_quality_floor(self):
        """池里无任何音质明细（如澜音提交）时合成 [已知最高档, 128k]，切换列表不空。"""
        from hub.adapters.base import identity_key
        from hub.adapters.cyshine import render_cyshine
        key = identity_key("kw", "9")
        view = {"pl": {"name": "N", "tracks": [key]}}
        pool = {key: {"title": "歌B", "singer": "S", "album": "Al",
                      "quality": "flac", "pic_url": "http://p/b.jpg"}}
        out = render_cyshine(view, pool, {"pl": "pl"}, None, 1,
                             "2026-10-10T00:00:00Z")
        track = out["sections"]["playlists"]["data"][0]["tracks"][0]
        mm = track["musicInfo"]["meta"]
        self.assertEqual(mm["qualitys"], [{"type": "flac"}, {"type": "128k"}])
        self.assertEqual(mm["picUrl"], "http://p/b.jpg")
        self.assertEqual(mm["albumName"], "Al")
        self.assertNotIn("id", mm)          # 无平台原生 id 就不写，让脚本回退 songId
        self.assertEqual(track["musicInfo"]["meta"]["songId"], "9")

    def test_qualitys_floor_128k_only_once(self):
        """已知最高档就是 128k 时不重复垫下限。"""
        from hub.adapters.cyshine import _ensure_qualitys_array
        mm = {"songId": "1"}
        _ensure_qualitys_array(mm, "128k")
        self.assertEqual(mm["qualitys"], [{"type": "128k"}])
        _ensure_qualitys_array(mm, None)
        self.assertEqual(mm["qualitys"], [{"type": "128k"}])

    def test_qualitys_uses_lx_raw_meta_when_raw_meta_bare(self):
        """回归（2026-10-10 真机）：栖弦 raw_meta 只有 songId（非本音源歌曲无法解析
        完整 meta）时，音质明细必须继续取 lx_raw_meta——否则播放页 qualitys 只剩合成
        下限 [master,128k]，栖弦在线设 flac 也播 128k。"""
        from hub.adapters.base import identity_key
        from hub.adapters.cyshine import render_cyshine
        key = identity_key("tx", "001iJcCp1q0kej")
        view = {"pl": {"name": "N", "tracks": [key]}}
        pool = {key: {
            "title": "下完这场雨", "singer": "S", "album": "Al",
            "quality": "master", "pic_url": "http://p/a.jpg",
            "raw_meta": {"songId": "001iJcCp1q0kej"},   # 栖弦对洛雪源歌曲只解析出这个
            "lx_raw_meta": {"songId": "001iJcCp1q0kej", "strMediaMid": "x",
                            "_qualitys": {"master": {"size": ""},
                                          "128k": {"size": ""}}},
        }}
        out = render_cyshine(view, pool, {"pl": "pl"}, None, 1,
                             "2026-10-10T00:00:00Z")
        mm = out["sections"]["playlists"]["data"][0]["tracks"][0]["musicInfo"]["meta"]
        self.assertTrue(mm["qualitys"], "qualitys 必须非空")
        types = [q["type"] for q in mm["qualitys"]]
        self.assertIn("master", types)
        self.assertIn("128k", types)
        # 有明细时保真，不再走 fallback 合成路径——也不应凭空造 flac（拿不到 URL 更糟）
        self.assertNotIn("flac", types)
        self.assertEqual(mm["strMediaMid"], "x")   # lx_raw_meta 补充字段进入渲染


class SnapshotMetaMergeTest(unittest.TestCase):
    """第六轮（2026-10-10）：客户端原样快照字段（lx_raw_meta/raw_meta）
    "更完整才整体替换"——首次残缺提交不得赖住后续完整提交。

    背景：通用合并规则"缺失字段不覆盖已有值"（P4）对普通增量字段是对的；
    但洛雪/栖弦每次提交 lx_raw_meta/raw_meta 都是"当前完整快照"，P4 会让
    首次残缺 _qualitys（如 [master,128k]）永久赖住，用户重搜出 flac 后
    重新提交也无法刷新 → 全局 flac 却播 128k（真机）。
    """

    def setUp(self):
        self.store = tempfile.mkdtemp(prefix="snap_merge_",
                                      dir=os.path.join(_ROOT, "tmp"))
        self.hub = Hub(self.store)

    def tearDown(self):
        rmtree_force(self.store)

    def _merge_meta(self, meta_delta):
        rt = self.hub.get("main")
        with rt.lock:
            if "c" not in rt.engine.clients:
                rt.engine.register_client("c", dialect="lx-x", identity_verified=True)
            rt.merge("c", "lx-x",
                     {"playlists": [], "opaque": None, "create_only": False},
                     meta_delta, None)
        return rt

    def test_richer_lx_raw_meta_replaces_poor(self):
        """残缺 [master,128k] → 完整 [128k,320k,flac,master]：整体替换。"""
        key = "tx:001iJcCp1q0kej"
        self._merge_meta({key: {"lx_raw_meta": {
            "_qualitys": {"master": {"size": ""}, "128k": {"size": ""}}}}})
        rt = self._merge_meta({key: {"lx_raw_meta": {
            "_qualitys": {"128k": {"size": ""}, "320k": {"size": ""},
                          "flac": {"size": ""}, "master": {"size": ""}}}}})
        got = set(rt.meta_pool[key]["lx_raw_meta"]["_qualitys"])
        self.assertEqual(got, {"128k", "320k", "flac", "master"})

    def test_poor_lx_raw_meta_does_not_replace_richer(self):
        """客户端数据回退（完整 → 残缺）不覆盖：保护已有数据。"""
        key = "tx:x1"
        self._merge_meta({key: {"lx_raw_meta": {
            "_qualitys": {"128k": {}, "320k": {}, "flac": {}}}}})
        rt = self._merge_meta({key: {"lx_raw_meta": {
            "_qualitys": {"128k": {}}}}})
        got = set(rt.meta_pool[key]["lx_raw_meta"]["_qualitys"])
        self.assertEqual(got, {"128k", "320k", "flac"})

    def test_ordinary_fields_still_p4_merge(self):
        """非快照字段仍走 P4"缺失不覆盖"：title 已存在时新提交的 title 不覆盖。"""
        key = "tx:x2"
        rt = self._merge_meta({key: {"title": "旧名", "singer": "S1"}})
        rt2 = self._merge_meta({key: {"title": "新名"}})
        self.assertEqual(rt2.meta_pool[key]["title"], "旧名")
        self.assertEqual(rt2.meta_pool[key]["singer"], "S1")


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
