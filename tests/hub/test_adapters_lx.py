"""第六轮 §2.9：洛雪 lx-x 适配器专项测试。

覆盖计划 §2.9 六条（真机样例精简副本在 tests/data/）：
1. 真实样例解析统计：大文件 2 歌单 / 131 首 userList 曲目 / 全文件 1386 首（1169:69:99:49）；
   小文件 1 歌单 / 235 首 / 200:15:10:10。
2. 身份规则：kg 老格式原样 id / songmid 例外 / 未知音源与无 id 走 opaque（I6）不抛错。
3. opaque 往返：三张个人列表 + playHistory/downloadTasks 原样进出（分桶隔离在路由测试里）。
4. 渲染外形：version=="2"、lastModified 整数、interval mm:ss、meta.id==0 不崩、lx_native_id 优先。
5. WebDAV 路由：PUT 后 GET 合并结果；文件名兜底命中；/d/pad/ 与默认设备 opaque 分桶互不影响。
6. 幂等：同一 payload 连续 PUT，revision 不变。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from hub import api
from hub.adapters import REGISTRY
from hub.adapters.base import RenderContext, identity_key
from hub.adapters.lx import parse_lx, render_lx

from tests.hub.helpers import HttpClient

_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load(name: str) -> dict:
    with open(os.path.join(_DATA, name), encoding="utf-8") as f:
        return json.load(f)


def _ctx(view, meta_pool, opaque, native_ids=None, generated_at="2026-10-08T05:23:40.658Z"):
    return RenderContext(
        view=view, meta_pool=meta_pool,
        native_ids=native_ids or {}, pending_cleanup=None, opaque=opaque,
        opaque_tracks=None, revision=1, generated_at=generated_at, playlist_times={})


class LxParseTest(unittest.TestCase):
    """§2.9.1-4：纯模块级 parse/render 断言。"""

    @classmethod
    def setUpClass(cls):
        cls.dialect = REGISTRY["lx-x"]
        cls.full = _load("lx_playlists_full.json")
        cls.small = _load("lx_playlists_small.json")
        cls.identity = _load("lx_identity.json")

    def test_01_full_parse_stats(self):
        sub, meta, opaque, warns = parse_lx(self.full)
        self.assertEqual(len(sub["playlists"]), 2)
        self.assertEqual([p["name"] for p in sub["playlists"]], ["YHK", "纯音乐"])
        total = sum(len(p["tracks"]) for p in sub["playlists"])
        self.assertEqual(total, 131)                      # userList 只参与合并
        # 全文件曲目统计（含三张个人列表）：1386 / 1169:69:99:49（计划 §2.1 口径）
        import collections
        counter = collections.Counter()
        for k in ("defaultList", "loveList", "tempList"):
            for t in self.full["data"].get(k, []):
                counter[t.get("source")] += 1
        for p in sub["playlists"]:
            for t in p["tracks"]:
                counter[t["source"]] += 1
        self.assertEqual(sum(counter.values()), 1386)
        self.assertEqual(counter["tx"], 1169)
        self.assertEqual(counter["wy"], 69)
        self.assertEqual(counter["kg"], 49)
        self.assertEqual(counter["kw"], 99)
        # opaque：三张个人列表原样 + 顶层其余键原样
        self.assertEqual(len(opaque["lx_lists"]["defaultList"]), 722)
        self.assertEqual(len(opaque["lx_lists"]["loveList"]), 232)
        self.assertEqual(len(opaque["lx_lists"]["tempList"]), 301)
        self.assertEqual(len(opaque["lx_extra"]["playHistory"]), 18)
        self.assertEqual(opaque["lx_extra"]["downloadTasks"],
                         self.full.get("downloadTasks"))
        # meta_delta 带展示字段 + 洛雪专用回写字段
        self.assertTrue(meta)
        first = next(iter(meta.values()))
        self.assertIn("lx_native_id", first)
        self.assertIn("title", first)
        self.assertIn("duration_ms", first)

    def test_02_small_parse_stats(self):
        sub, _, opaque, _ = parse_lx(self.small)
        self.assertEqual(len(sub["playlists"]), 1)
        total = sum(len(p["tracks"]) for p in sub["playlists"])
        self.assertEqual(total, 235)
        import collections
        c = collections.Counter(t["source"] for p in sub["playlists"] for t in p["tracks"])
        self.assertEqual(dict(c), {"tx": 200, "wy": 15, "kg": 10, "kw": 10})
        # 精简导出无个人列表/无顶层附加键 → opaque 相应为空
        self.assertEqual(opaque["lx_extra"], {})

    def test_03_identity_rules(self):
        sub, meta, opaque, warns = parse_lx(self.identity)
        self.assertEqual(len(sub["playlists"]), 1)
        tracks = sub["playlists"][0]["tracks"]
        self.assertEqual(len(tracks), 3)                  # kg 老格式 / songmid 例外 / tx 标准
        keys = {t["source"]: t["songId"] for t in tracks}
        # kg 老格式：顶层 id 无 <source>_ 前缀 → 原样用顶层 id
        kg_key = identity_key("kg", "545879713_E3426FF0609426305A4A4BB9DC2AD29B")
        self.assertEqual(keys["kg"], "545879713_E3426FF0609426305A4A4BB9DC2AD29B")
        self.assertEqual(meta[kg_key]["lx_native_id"], "545879713_E3426FF0609426305A4A4BB9DC2AD29B")
        self.assertNotIn("lx_num_id", meta[kg_key])       # meta.id==0 → 不写
        # 无顶层 id → 退 meta.songmid
        wy_key = identity_key("wy", "ABC123")
        self.assertEqual(keys["wy"], "ABC123")
        self.assertEqual(meta[wy_key]["title"], "无顶层id退songmid")
        # 标准 tx：去前缀 + 最高档 quality + lx_num_id
        tx_key = identity_key("tx", "abcdef1234567890")
        self.assertEqual(keys["tx"], "abcdef1234567890")
        self.assertEqual(meta[tx_key]["quality"], "flac")
        self.assertEqual(meta[tx_key]["lx_num_id"], 888)
        # 无法定身份 / 非白名单音源 → opaque["tracks"][pl_id] 原样（I6，不丢）
        opaque_tracks = opaque["tracks"]["userlist_identity01"]
        self.assertEqual(len(opaque_tracks), 3)           # local / xx / kw 无 id
        self.assertEqual({t["source"] for t in opaque_tracks}, {"local", "xx", "kw"})
        self.assertEqual(warns, [])

    def test_04_opaque_roundtrip(self):
        # parse → render：三张个人列表与顶层附加键原样进出
        _, _, opaque, _ = parse_lx(self.full)
        ctx = _ctx(view={"pl": {"name": "A", "tracks": []}}, meta_pool={},
                   opaque=opaque, native_ids={"pl": "pl_native"})
        out = render_lx(ctx)
        self.assertEqual(out["data"]["defaultList"], opaque["lx_lists"]["defaultList"])
        self.assertEqual(out["data"]["loveList"], opaque["lx_lists"]["loveList"])
        self.assertEqual(out["data"]["tempList"], opaque["lx_lists"]["tempList"])
        self.assertEqual(out["playHistory"], opaque["lx_extra"]["playHistory"])
        self.assertEqual(out["downloadTasks"], opaque["lx_extra"]["downloadTasks"])

    def test_05_render_shape(self):
        ctx = _ctx(
            view={"pl1": {"name": "A", "tracks": ["tx:1", "kg:545879713_E3426"]}},
            meta_pool={
                "tx:1": {"source": "tx", "title": "歌", "singer": "S",
                         "duration_ms": 207000, "quality": "flac", "lx_native_id": "tx_1",
                         "lx_num_id": 888, "album": "专辑", "pic_url": "http://x/p.jpg"},
                "kg:545879713_E3426": {"source": "kg", "title": "旧", "singer": "Q",
                                       "duration_ms": 3720000, "lx_native_id":
                                       "545879713_E3426FF0609426305A4A4BB9DC2AD29B",
                                       "album": "翻唱", "pic_url": ""},
            },
            opaque={"lx_lists": {"defaultList": [{"id": 1}]}, "lx_extra": {"playHistory": [1]}},
            native_ids={"pl1": "userlist_1"},
        )
        out = render_lx(ctx)
        self.assertEqual(out["version"], "2")
        self.assertIsInstance(out["lastModified"], int)
        pl = out["data"]["userList"][0]
        self.assertEqual(pl["id"], "userlist_1")          # native id
        self.assertEqual(pl["origin"], "created")         # 枢纽新建歌单标记
        t1 = pl["list"][0]
        self.assertEqual(t1["id"], "tx_1")                # lx_native_id 优先原样
        self.assertEqual(t1["interval"], "03:27")
        self.assertEqual(t1["meta"]["id"], 888)
        # §2.8.1（第十轮）：已知最高档 + 128k 下限，且 `_qualitys` 必须存在（客户端点播依赖）
        self.assertEqual(t1["meta"]["qualitys"], [{"type": "flac"}, {"type": "128k"}])
        self.assertEqual(t1["meta"]["_qualitys"], {"flac": {"size": ""}, "128k": {"size": ""}})
        t2 = pl["list"][1]
        # ≥1 小时 → h:mm:ss；meta.id 缺失 → 0（不崩，§2.8.2）
        self.assertEqual(t2["interval"], "1:02:00")
        self.assertEqual(t2["meta"]["id"], 0)
        # 无 lx_native_id 时合成 f"{source}_{pid}"（澜音/栖弦带进来的歌）
        ctx2 = _ctx(view={"pl1": {"name": "A", "tracks": ["wy:q1"]}},
                    meta_pool={"wy:q1": {"source": "wy", "title": "外来", "singer": "X"}},
                    opaque={}, native_ids={})
        out2 = render_lx(ctx2)
        self.assertEqual(out2["data"]["userList"][0]["list"][0]["id"], "wy_q1")

    def test_05b_lx_raw_meta_roundtrip(self):
        """§2.8.1（第十轮）：原生 meta 九键原样往返，音质明细不丢。"""
        native_meta = {
            "id": 0, "songId": "abcdef1234567890", "strMediaMid": "abcdef1234567890",
            "albumName": "专", "albumId": 42, "albumMid": "mid", "picUrl": "http://p",
            "qualitys": [{"type": "128k", "size": "3.59 MB"},
                         {"type": "320k", "size": "8.98 MB"},
                         {"type": "flac", "size": "46.78 MB"}],
            "_qualitys": {"128k": {"size": "3.59 MB"}, "320k": {"size": "8.98 MB"},
                          "flac": {"size": "46.78 MB"}},
        }
        payload = {"version": "2", "lastModified": 1791284114614,
                   "data": {"userList": [{"id": "userlist_rt", "name": "A", "list": [
                       {"id": "tx_abcdef1234567890", "name": "歌", "singer": "S",
                        "source": "tx", "interval": "03:00", "meta": native_meta}]}]}}
        sub, meta_delta, opaque, _w = parse_lx(payload)
        key = identity_key("tx", "abcdef1234567890")
        self.assertEqual(meta_delta[key]["lx_raw_meta"]["qualitys"], native_meta["qualitys"])
        out = render_lx(_ctx(view={"pl": {"name": "A", "tracks": [key]}},
                             meta_pool=meta_delta, opaque=opaque,
                             native_ids={"pl": "userlist_rt"}))
        tm = out["data"]["userList"][0]["list"][0]["meta"]
        self.assertEqual(tm["qualitys"], native_meta["qualitys"])       # size 也保住
        self.assertEqual(tm["_qualitys"], native_meta["_qualitys"])
        self.assertEqual(tm["albumId"], 42)
        self.assertEqual(tm["albumMid"], "mid")
        self.assertEqual(tm["id"], 0)                                   # meta.id==0 仍回写 0

    def test_05c_small_fixture_every_track_has_playable_meta(self):
        """第十轮回归：整份样例（235 首）交付后每首都有非空 `_qualitys`/`qualitys`。

        真机事故（2026-10-08）：缺 `_qualitys` → 洛雪 `getPlayQuality()` TypeError → 列表点歌无反应。
        """
        payload = _load("lx_playlists_small.json")
        sub, meta, opaque, _w = parse_lx(payload)
        view, native_ids = {}, {}
        for i, p in enumerate(sub["playlists"]):
            pid = f"pl{i}"
            view[pid] = {"name": p["name"],
                         "tracks": [identity_key(t["source"], t["songId"]) for t in p["tracks"]]}
            native_ids[pid] = p["native_id"]
        out = render_lx(_ctx(view=view, meta_pool=meta, opaque=opaque, native_ids=native_ids))
        n = 0
        for pl in out["data"]["userList"]:
            for t in pl["list"]:
                self.assertTrue(t["meta"].get("_qualitys"), t)
                self.assertTrue(t["meta"].get("qualitys"), t)
                n += 1
        self.assertEqual(n, 235)
        # 原生多档音质必须原样保住（不再只剩最高档）
        tx_track = out["data"]["userList"][0]["list"][0]
        self.assertGreater(len(tx_track["meta"]["_qualitys"]), 1)
        self.assertIn("albumId", tx_track["meta"])

    def test_05d_foreign_track_gets_128k_floor(self):
        """第十轮：澜音/栖弦带进来的歌没有原生音质线索 → 至少声明 128k，保证取得到 URL。"""
        out = render_lx(_ctx(view={"pl": {"name": "A", "tracks": ["wy:q1"]}},
                             meta_pool={"wy:q1": {"source": "wy", "title": "外来"}},
                             opaque={}, native_ids={}))
        tm = out["data"]["userList"][0]["list"][0]["meta"]
        self.assertEqual([x["type"] for x in tm["qualitys"]], ["128k"])
        self.assertEqual(tm["_qualitys"], {"128k": {"size": ""}})

    def test_05e_lists_always_present_for_overwrite_flow(self):
        """回归（2026-10-10 真机）：洛雪"从云端数据覆盖本地"校验备份结构完整性，
        defaultList/loveList 缺键即拒绝覆盖。opaque 没有三列表（新账号/新客户端
        首次接入）时 render 必须输出空数组兜底；opaque 已有内容原样输出。
        顶层 playHistory/downloadTasks 同样兜底空数组（上传结构里有这两键）。"""
        # 无 lx_lists → defaultList/loveList/tempList 空数组，顶层 extras 空数组
        out = render_lx(_ctx(view={"pl": {"name": "A", "tracks": []}},
                             meta_pool={}, opaque={}, native_ids={}))
        self.assertEqual(out["data"]["defaultList"], [])
        self.assertEqual(out["data"]["loveList"], [])
        self.assertEqual(out["data"]["tempList"], [])
        self.assertEqual(out["playHistory"], [])
        self.assertEqual(out["downloadTasks"], [])
        self.assertEqual([p["name"] for p in out["data"]["userList"]], ["A"])
        # opaque 有 lx_lists → 原样输出，不改写
        out2 = render_lx(_ctx(
            view={"pl": {"name": "A", "tracks": []}}, meta_pool={},
            opaque={"lx_lists": {"defaultList": [{"id": "d1"}],
                                 "loveList": [{"id": "l1"}],
                                 "tempList": [{"id": "t1"}]}},
            native_ids={}))
        self.assertEqual(out2["data"]["defaultList"], [{"id": "d1"}])
        self.assertEqual(out2["data"]["loveList"], [{"id": "l1"}])
        self.assertEqual(out2["data"]["tempList"], [{"id": "t1"}])
        # 已有顶层 extras 不被空数组覆盖
        out3 = render_lx(_ctx(
            view={"pl": {"name": "A", "tracks": []}}, meta_pool={},
            opaque={"lx_extra": {"playHistory": [{"id": "h1"}]}}, native_ids={}))
        self.assertEqual(out3["playHistory"], [{"id": "h1"}])


class LxHttpTest(unittest.TestCase):
    """§2.9.5-6：WebDAV 路由 + 旁路 + 幂等（uvicorn 线程实测）。"""

    @classmethod
    def setUpClass(cls):
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
        self.store = tempfile.mkdtemp(prefix="lx_", dir=os.path.join(_ROOT, "tmp"))
        self.hub = api.Hub(self.store)
        api.configure(self.hub, {
            "alice": {"password": api._hash_pw("pw1"), "space": "main",
                      "role": "admin", "enabled": True},
        })
        self.c = HttpClient(self.base, "alice", "pw1")

    @staticmethod
    def lx_payload(pl_id, pl_name, tracks, lists=None, extra=None):
        payload = {
            "version": "2",
            "lastModified": 1790838848951,
            "data": {
                "defaultList": (lists or {}).get("defaultList", []),
                "loveList": (lists or {}).get("loveList", []),
                "tempList": (lists or {}).get("tempList", []),
                "userList": [{"id": pl_id, "name": pl_name,
                              "locationUpdateTime": None, "list": tracks}],
            },
        }
        if extra:
            payload.update(extra)
        return payload

    @staticmethod
    def track(tid, name="歌", source="tx", interval="03:27", mid="m1", meta_id=1):
        return {"id": tid, "name": name, "singer": "S", "source": source,
                "interval": interval,
                "meta": {"id": meta_id, "songId": tid, "strMediaMid": mid,
                         "albumName": "专辑", "picUrl": "http://x/p.jpg"}}

    def test_06_webdav_routes(self):
        # PUT 建基线 → GET 合并结果（version/lastModified/lx_native_id 原样/opaque 原样）
        p1 = self.lx_payload("userlist_a", "A", [self.track("tx_1")],
                             lists={"defaultList": [{"a": 1}]}, extra={"playHistory": [1]})
        st, _, body = self.c.put("/lx-x/playlists.json", p1)
        self.assertEqual(st, 200, body)
        st2, _, out = self.c.get("/lx-x/playlists.json")
        self.assertEqual(st2, 200)
        self.assertEqual(out["version"], "2")
        self.assertIsInstance(out["lastModified"], int)
        pl = out["data"]["userList"][0]
        self.assertEqual(pl["id"], "userlist_a")
        self.assertEqual(pl["list"][0]["id"], "tx_1")
        self.assertEqual(out["data"]["defaultList"], [{"a": 1}])
        self.assertEqual(out["playHistory"], [1])
        # 文件名兜底：/任意目录/lx-x-playlists.json 命中（file_fallback）。
        # 同一 cid 整份提交是"缺席即删"语义——用同内容重放验证兜底路由本身命中。
        st3, _, _ = self.c.put("/music/lx-x-playlists.json", p1)
        self.assertEqual(st3, 200)
        st4, _, out2 = self.c.get("/lx-x/lx-x-playlists.json")
        self.assertEqual(st4, 200)
        ids = [p["id"] for p in out2["data"]["userList"]]
        self.assertIn("userlist_a", ids)
        # /d/pad/ 与默认设备：共享视图，但 opaque（个人列表）按客户端分桶隔离
        p3 = self.lx_payload("userlist_c", "C", [self.track("tx_3", "歌3")],
                             lists={"defaultList": [{"pad": 1}]})
        st5, _, _ = self.c.put("/d/pad/lx-x/playlists.json", p3)
        self.assertEqual(st5, 200)
        _, _, out3 = self.c.get("/lx-x/playlists.json")
        self.assertEqual(out3["data"]["defaultList"], [{"a": 1}])    # 默认桶不被 pad 覆盖
        _, _, out4 = self.c.get("/d/pad/lx-x/playlists.json")
        self.assertEqual(out4["data"]["defaultList"], [{"pad": 1}])  # pad 桶原样
        self.assertIn("userlist_c", [p["id"] for p in out4["data"]["userList"]])

    def test_07_bypass_files(self):
        # settings.json 整文件旁路：原样收、原样还（不解析、不合并）
        st, _, body = self.c.put("/lx-x/settings.json", {"theme": "dark", "volume": 80})
        self.assertEqual(st, 200, body)
        st2, _, out = self.c.get("/lx-x/settings.json")
        self.assertEqual(st2, 200)
        self.assertEqual(out, {"theme": "dark", "volume": 80})
        # 未上传的文件 → 404（洛雪按"云端未找到"处理）
        st3, _, _ = self.c.get("/lx-x/user_apis.json")
        self.assertEqual(st3, 404)
        # 分桶隔离：pad 上传自己的设置，不影响默认设备
        st4, _, _ = self.c.put("/d/pad/lx-x/settings.json", {"theme": "light"})
        self.assertEqual(st4, 200)
        _, _, out5 = self.c.get("/lx-x/settings.json")
        self.assertEqual(out5, {"theme": "dark", "volume": 80})
        _, _, out6 = self.c.get("/d/pad/lx-x/settings.json")
        self.assertEqual(out6, {"theme": "light"})
        # 兜底路径同样命中旁路（任意目录名）
        st7, _, _ = self.c.put("/any/dir/settings.json", {"a": 1})
        self.assertEqual(st7, 200)
        _, _, out7 = self.c.get("/lx-x/settings.json")
        self.assertEqual(out7["a"], 1)

    def test_08_idempotent(self):
        p = self.lx_payload("userlist_i", "I", [self.track("tx_9", "歌9")])
        st, _, body = self.c.put("/lx-x/playlists.json", p)
        self.assertEqual(st, 200)
        st2, _, body2 = self.c.put("/lx-x/playlists.json", p)
        self.assertEqual(st2, 200)
        self.assertEqual(body["revision"], body2["revision"])


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
