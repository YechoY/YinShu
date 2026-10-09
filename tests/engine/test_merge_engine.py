"""合并引擎边界用例（D34 简化版）。

核心规则：段 updatedAt 变了 + 缺席 = 删；段没变 + 缺席 = 不动；在场的并集。
无墓碑、无冷静期、无 suspects、无安全阀。
"""
from __future__ import annotations

import unittest

from engine.engine import SyncSpace

from .helpers import pl, sub, tr


def pl_id_of(space: SyncSpace, name: str):
    for pl_id, pl_ in space.playlists.items():
        if pl_ is not None and pl_.deleted_at is None and pl_.name == name:
            return pl_id
    raise AssertionError(f"歌单 {name!r} 不存在")


def live_track_keys(space: SyncSpace, pl_id: str):
    pl_ = space.playlists[pl_id]
    out = set()
    for tid in pl_.track_ids:
        t = space.tracks[tid]
        if t.deleted_at is None:
            out.add(t.key)
    return out


def delivered_tracks(space: SyncSpace, client_id: str, pl_name: str):
    c = space.clients[client_id]
    if c.base_served is None:
        return set()
    for pl_id, e in c.base_served.view.items():
        if e["name"] == pl_name:
            return set(e["tracks"])
    return set()


class TestMergeEngine(unittest.TestCase):

    def setUp(self):
        self.sp = SyncSpace("test")
        self.sp.register_client("A", dialect="cyshine-v1", identity_verified=True)
        self.sp.register_client("B", dialect="cyshine-v1", identity_verified=True)

    # 用例 1：单端加歌 → 对端可见
    def test_01_add_track_propagates(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("B")
        self.assertIn("tx:x", delivered_tracks(self.sp, "B", "歌单"))

    # 用例 2：单端删歌 → 对端消失（段 updatedAt 变了 = 用户真删）
    def test_02_delete_track(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"),
                                 modified_at="2026-10-09T03:00:00Z")))
        self.sp.deliver("A")
        self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "x"),
                                 modified_at="2026-10-09T03:00:00Z")))
        self.sp.deliver("B")
        self.sp.deliver("A")
        # A 删 x（段 updatedAt 变了）
        self.sp.merge("A", sub(pl("p1", "歌单", modified_at="2026-10-09T04:00:00Z")))
        self.sp.deliver("B")
        self.assertNotIn("tx:x", delivered_tracks(self.sp, "B", "歌单"))

    # 用例 3：两端各加不同歌 → 并集
    def test_03_union(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.merge("B", sub(pl("p1", "歌单", ("wy", "y"))))
        self.sp.deliver("B")
        self.sp.deliver("A")
        self.assertEqual(delivered_tracks(self.sp, "A", "歌单"), {"tx:x", "wy:y"})
        self.assertEqual(delivered_tracks(self.sp, "B", "歌单"), {"tx:x", "wy:y"})

    # 用例 4：一端删、一端还留着（段没动） → 不删（M1 保护）
    # 段没动过 = 客户端可能未采纳远端内容，缺席≠删除意图
    def test_04_delete_vs_keep_without_mod_change(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("B")
        self.sp.deliver("A")
        # B 段没动过（无 modified_at），缺席不算删
        r = self.sp.merge("B", sub(pl("p1", "歌单")))
        self.assertEqual(r.meta["removed_tracks"], {})
        # 但如果段动过 → 删
        r2 = self.sp.merge("B", sub(pl("p1", "歌单", modified_at="2026-10-09T04:00:00Z")))
        pid = pl_id_of(self.sp, "歌单")
        self.assertEqual(r2.meta["removed_tracks"], {pid: ["tx:x"]})

    # 用例 5：两端各删不同歌 → 都消失
    def test_05_both_delete(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"), ("wy", "y"))))
        self.sp.deliver("A")
        self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "x"), ("wy", "y"))))
        self.sp.deliver("B")
        self.sp.deliver("A")
        # A 删 x（段动过）
        self.sp.merge("A", sub(pl("p1", "歌单", ("wy", "y"), modified_at="2026-10-09T03:00:00Z")))
        self.sp.deliver("B")
        # B 删 y（段动过）
        self.sp.merge("B", sub(pl("p1", "歌单", modified_at="2026-10-09T04:00:00Z")))
        self.sp.deliver("A")
        self.sp.deliver("B")
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")), set())

    # 用例 6：新增歌单 → 对端出现
    def test_06_add_playlist(self):
        self.sp.merge("A", sub(pl("p1", "歌单甲", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.merge("B", sub(pl("p2", "歌单乙", ("tx", "z"))))
        self.sp.deliver("B")
        names = {e["name"] for e in self.sp.clients["B"].base_served.view.values()}
        self.assertIn("歌单甲", names)
        self.assertIn("歌单乙", names)

    # 用例 7：删除歌单 → 对端消失
    def test_07_delete_playlist(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("B")
        self.sp.deliver("A")
        self.sp.merge("A", sub())
        self.sp.deliver("B")
        self.assertNotIn("歌单", {e["name"] for e in self.sp.clients["B"].base_served.view.values()})

    # 用例 8：首轮接入（空间已有数据）→ 只增不删
    def test_08_first_adoption_keeps_existing(self):
        self.sp.merge("A", sub(pl("p1", "歌单甲", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.register_client("C", dialect="ceru-plugin", identity_verified=False)
        self.sp.merge("C", sub(pl("p9", "歌单乙", ("kw", "k"))))
        self.sp.deliver("C")
        self.assertIn("歌单甲", {e["name"] for e in self.sp.clients["C"].base_served.view.values()})
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单甲")), {"tx:x"})

    # 用例 8b：首次接入新增曲目置顶
    def test_08b_first_adoption_new_tracks_to_head(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "o1"), ("tx", "o2"), ("tx", "o3"))))
        self.sp.deliver("A")
        self.sp.register_client("C", dialect="cyshine-v1", identity_verified=True)
        rc = self.sp.merge("C", sub(pl("p1", "歌单", ("tx", "n1"), ("tx", "n2"),
                                     ("tx", "o1"), ("tx", "o2"), ("tx", "o3"))))
        self.assertTrue(rc.meta.get("adopted"))
        pid = pl_id_of(self.sp, "歌单")
        order = [self.sp.tracks[t].key for t in self.sp.playlists[pid].track_ids
                 if self.sp.tracks[t].deleted_at is None]
        self.assertEqual(order, ["tx:n1", "tx:n2", "tx:o1", "tx:o2", "tx:o3"])

    # 用例 9：客户端清数据重装 → 只增不删
    def test_09_reinstall_no_wipe(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"), ("wy", "y"))))
        self.sp.deliver("A")
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"), modified_at="2026-10-09T03:00:00Z")))
        self.sp.deliver("A")
        # 重装 = 新身份客户端
        self.sp.register_client("C", dialect="cyshine-v1", identity_verified=False)
        rc = self.sp.merge("C", sub(pl("p1", "歌单", ("tx", "x"), ("wy", "y"))))
        self.sp.deliver("C")
        # 首次接入只增不删 → y 回来了
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")), {"tx:x", "wy:y"})

    # 用例 10：歌单改名 → 不复制歌单
    def test_10_rename_no_duplicate(self):
        self.sp.merge("A", sub(pl("p1", "旧名", ("tx", "x"))))
        self.sp.deliver("A")
        n_before = len([p for p in self.sp.playlists.values() if p.deleted_at is None])
        self.sp.merge("A", sub(pl("p1", "新名", ("tx", "x"))))
        self.sp.deliver("A")
        n_after = len([p for p in self.sp.playlists.values() if p.deleted_at is None])
        self.assertEqual(n_before, n_after)
        self.assertEqual(self.sp.playlists[pl_id_of(self.sp, "新名")].name, "新名")
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "新名")), {"tx:x"})

    # 用例 11：同名歌单来自两个平台 → 按名字合并
    def test_11_same_name_two_platforms_merge(self):
        self.sp.merge("A", sub(pl("cyshine-n1", "Rock", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.register_client("Ceru", dialect="ceru-plugin", identity_verified=True)
        self.sp.merge("Ceru", sub(pl("ceru-n9", "Rock", ("kw", "k"))))
        self.sp.deliver("Ceru")
        rock = pl_id_of(self.sp, "Rock")
        self.assertEqual(len([p for p in self.sp.playlists.values()
                              if p.deleted_at is None and p.name == "Rock"]), 1)
        self.assertEqual(live_track_keys(self.sp, rock), {"tx:x", "kw:k"})

    # 用例 12：跨源同 songId → 视为两首
    def test_12_cross_source_same_song_id(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("wy", "123"), ("kw", "123"))))
        self.sp.deliver("A")
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")),
                         {"wy:123", "kw:123"})
        self.sp.merge("A", sub(pl("p1", "歌单", ("kw", "123"), modified_at="2026-10-09T03:00:00Z")))
        self.sp.deliver("A")
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")), {"kw:123"})

    # 用例 13：坏 JSON → 拒绝，基线不动
    def test_13_bad_payload_rejected(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        rev_before = self.sp.revision
        sub_before = self.sp.clients["A"].base_submitted
        r = self.sp.merge("A", {"playlists": "not-a-list"})
        self.assertEqual(r.status, 400)
        r2 = self.sp.merge("A", sub({"native_id": "p1", "name": "歌单",
                                     "tracks": [{"source": "", "songId": ""}]}))
        self.assertEqual(r2.status, 400)
        self.assertEqual(self.sp.revision, rev_before)
        self.assertIs(self.sp.clients["A"].base_submitted, sub_before)

    # 用例 14：批量删除直接生效（无安全阀）
    def test_14_bulk_delete_applies(self):
        tracks = [("tx", f"t{i}") for i in range(12)]
        self.sp.merge("A", sub(pl("p1", "歌单", *tracks)))
        self.sp.deliver("A")
        r = self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "t0"), modified_at="2026-10-09T03:00:00Z")))
        self.assertEqual(r.status, 200)
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")), {"tx:t0"})

    # 用例 15：未知方言 → 隔离
    def test_15_unknown_dialect_isolated(self):
        self.sp.register_client("U", dialect="mystery-v1", identity_verified=False)
        r = self.sp.merge("U", sub(pl("u1", "怪格式", ("tx", "x"))))
        self.assertTrue(r.meta.get("isolated"))
        self.assertIn("U", self.sp.quarantine)
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")), {"tx:x"})

    # 用例 16：重复提交 → 幂等
    def test_16_idempotent(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        rev_before = self.sp.revision
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.assertEqual(self.sp.revision, rev_before)
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")), {"tx:x"})

    # 用例 17：两端同时写 → 串行化
    def test_17_concurrent_write_serialized(self):
        self.sp.merge("A", sub(pl("p1", "歌单甲", ("tx", "x"))))
        self.sp.merge("B", sub(pl("p2", "歌单乙", ("wy", "y"))))
        self.sp.deliver("A")
        self.sp.deliver("B")
        namesA = {e["name"] for e in self.sp.clients["A"].base_served.view.values()}
        namesB = {e["name"] for e in self.sp.clients["B"].base_served.view.values()}
        self.assertEqual(namesA, namesB)
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单甲")), {"tx:x"})
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单乙")), {"wy:y"})

    # 用例 18：opaque round-trip
    def test_18_opaque_roundtrip(self):
        opaque = {"cyshine": {"tracks": [{"path": "file:///C:/music/a.mp3"}]}}
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x")), opaque=opaque))
        self.sp.deliver("A")
        self.assertEqual(self.sp.clients["A"].opaque, opaque)
        self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("B")
        self.assertEqual(self.sp.clients["A"].opaque, opaque)
        self.assertEqual(set(self.sp.identity_to_tr.keys()), {"tx:x"})

    # 用例 19：设备身份
    def test_19_device_identity(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"), ("wy", "y"))))
        self.sp.deliver("A")
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")), {"tx:x", "wy:y"})
        self.sp.register_client("R", dialect="cyshine-v1", identity_verified=False)
        self.sp.merge("R", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("R")
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")), {"tx:x", "wy:y"})

    # 用例 20：三客户端交错
    def test_20_three_client_interleave(self):
        self.sp.register_client("C", dialect="cyshine-v1", identity_verified=True)
        self.sp.merge("A", sub(pl("p1", "歌单甲", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.merge("B", sub(pl("p2", "歌单乙", ("wy", "y"))))
        self.sp.merge("C", sub(pl("p3", "歌单丙", ("kw", "k"))))
        self.sp.deliver("A")
        self.sp.merge("A", sub(pl("p1", "歌单甲", ("tx", "x")),
                               pl("p2", "歌单乙", ("wy", "y")),
                               pl("p3", "歌单丙", ("kw", "k"))))
        ra = self.sp.merge("A", sub(pl("p1", "歌单甲", ("tx", "x"))))
        self.assertEqual(len(ra.meta["removed_playlists"]), 2)
        yb = next(pl for pl in self.sp.playlists.values() if pl.name == "歌单乙")
        self.assertIsNotNone(yb.deleted_at)
        # B 回推 p2 → 恢复（删除就是删除，加回来就是加回来）
        self.sp.deliver("B")
        rb = self.sp.merge("B", sub(pl("p1", "歌单甲", ("tx", "x")),
                                    pl("p2", "歌单乙", ("wy", "y"))))
        self.assertEqual(len(rb.meta["added_playlists"]), 1)
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单甲")), {"tx:x"})
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单乙")), {"wy:y"})

    # 用例 21：只 GET 不 PUT → 404
    def test_21_get_only_no_delete(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.register_client("R", dialect="cyshine-v1", identity_verified=True)
        r = self.sp.deliver("R")
        self.assertEqual(r.status, 404)
        self.assertIsNone(self.sp.clients["R"].base_served)
        self.sp.merge("R", sub(pl("p1", "歌单", ("tx", "x"))))
        r2 = self.sp.deliver("R")
        self.assertEqual(r2.status, 200)
        self.assertEqual(delivered_tracks(self.sp, "R", "歌单"), {"tx:x"})

    # 用例 22：清数据重装后回写空视图 → 不血洗
    def test_22_rewrite_empty_not_wipe(self):
        self.sp.merge("A", sub(pl("p1", "歌单", *[("tx", f"t{i}") for i in range(10)])))
        self.sp.deliver("A")
        self.sp.register_client("R", dialect="cyshine-v1", identity_verified=False)
        self.sp.merge("R", sub(pl("p1", "歌单")))
        self.sp.deliver("R")
        self.assertEqual(len(live_track_keys(self.sp, pl_id_of(self.sp, "歌单"))), 10)

    # 用例 23：离线回归 → 已删的歌加回来就恢复（无冷静期）
    def test_23_offline_return_restore(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("B")
        self.sp.deliver("A")
        self.sp.merge("A", sub(pl("p1", "歌单", modified_at="2026-10-09T03:00:00Z")))
        self.sp.deliver("A")
        # B 回归，带着旧视图（含 x）→ x 恢复（加回来就是加回来）
        self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("B")
        self.assertIn("tx:x", live_track_keys(self.sp, pl_id_of(self.sp, "歌单")))

    # 用例 24：未适配方言 → 隔离
    def test_24_unadapted_dialect_no_pollution(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.register_client("U", dialect="ceru-v9-legacy", identity_verified=False)
        r = self.sp.merge("U", sub(pl("u1", "怪格式", ("kw", "k"))))
        self.assertTrue(r.meta.get("isolated"))
        self.sp.deliver("A")
        names = {e["name"] for e in self.sp.clients["A"].base_served.view.values()}
        self.assertEqual(names, {"歌单"})
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")), {"tx:x"})

    # 用例 25：首次接入 createOnly
    def test_25_first_adoption_create_only(self):
        r1 = self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x")), create_only=True))
        self.assertEqual(r1.status, 201)
        r2 = self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x")), create_only=True))
        self.assertEqual(r2.status, 200)
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")), {"tx:x"})
        self.sp.register_client("Ceru", dialect="ceru-plugin", identity_verified=False)
        self.sp.merge("Ceru", sub(pl("p2", "歌单乙", ("wy", "y"))))
        self.sp.deliver("A")
        self.sp.deliver("Ceru")
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")), {"tx:x"})
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单乙")), {"wy:y"})

    # 用例 26：删了又加回来 → 直接生效（无冷静期）
    def test_26_delete_then_restore(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.merge("A", sub(pl("p1", "歌单", modified_at="2026-10-09T03:00:00Z")))
        self.sp.deliver("A")
        # 加回来
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"), modified_at="2026-10-09T04:00:00Z")))
        self.sp.deliver("A")
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")), {"tx:x"})

    # 排序同步
    def test_order_preserved_through_merge_and_deliver(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("kg", "1"), ("tx", "2"), ("wy", "3"))))
        self.sp.deliver("A")
        order = list(self.sp.clients["A"].base_served.view[pl_id_of(self.sp, "歌单")]["tracks"])
        self.assertEqual(order, ["kg:1", "tx:2", "wy:3"])
        self.sp.merge("B", sub(pl("p1", "歌单", ("kg", "1"), ("tx", "2"), ("wy", "3"))))
        self.sp.deliver("B")
        order_b = list(self.sp.clients["B"].base_served.view[pl_id_of(self.sp, "歌单")]["tracks"])
        self.assertEqual(order_b, ["kg:1", "tx:2", "wy:3"])

    def test_order_new_tracks_go_to_head(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "o1"), ("tx", "o2"))))
        self.sp.deliver("A")
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "n1"), ("tx", "n2"),
                                 ("tx", "o1"), ("tx", "o2"))))
        self.sp.deliver("A")
        pid = pl_id_of(self.sp, "歌单")
        order = [self.sp.tracks[t].key for t in self.sp.playlists[pid].track_ids
                 if self.sp.tracks[t].deleted_at is None]
        self.assertEqual(order, ["tx:n1", "tx:n2", "tx:o1", "tx:o2"])

    def test_order_reorder_propagates(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "a"), ("tx", "b"), ("tx", "c"))))
        self.sp.deliver("A")
        self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "c"), ("tx", "a"), ("tx", "b"))))
        self.sp.deliver("B")
        self.sp.deliver("A")
        pid = pl_id_of(self.sp, "歌单")
        order_a = [self.sp.tracks[t].key for t in self.sp.playlists[pid].track_ids
                   if self.sp.tracks[t].deleted_at is None]
        self.assertEqual(order_a, ["tx:c", "tx:a", "tx:b"])

    # peek_view 纯只读
    def test_33_peek_view_is_pure_read(self):
        sp = SyncSpace("test")
        sp.register_client("A", dialect="cyshine-v1", identity_verified=True)
        sp.merge("A", sub(pl("p1", "歌单1", ("tx", "x"), ("tx", "y"),
                            modified_at="2026-10-09T03:00:00Z"),
                          pl("p2", "歌单2", ("tx", "z"),
                            modified_at="2026-10-09T03:00:00Z")))
        sp.deliver("A")
        rev0, tag0 = sp.revision, sp.sort_tag
        cc0, oc0 = sp.content_changed_at, sp.order_changed_at
        jlen0 = len(sp.journal)
        view, _cleanup, stamp = sp.peek_view("cyshine-v1", can_delete=True)
        self.assertTrue(view)
        self.assertEqual(len(view), 2)
        self.assertEqual(sp.revision, rev0)
        self.assertEqual(sp.sort_tag, tag0)
        self.assertEqual(sp.content_changed_at, cc0)
        self.assertEqual(sp.order_changed_at, oc0)
        self.assertEqual(len(sp.journal), jlen0)

    # 第十五轮：段 updatedAt 变了 + 缺席 = 删
    def test_34_track_delete_after_get_with_modified_at_change(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"),
                                 modified_at="2026-10-09T03:00:00Z")))
        self.sp.deliver("A")
        self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "x"), ("tx", "y"))))
        self.sp.deliver("B")
        self.sp.deliver("A")
        r = self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"),
                                      modified_at="2026-10-09T03:45:00Z")))
        pid = pl_id_of(self.sp, "歌单")
        self.assertEqual(r.meta["removed_tracks"], {pid: ["tx:y"]})
        self.assertNotIn("tx:y", live_track_keys(self.sp, pid))

    # 段没变 + 缺席 = 不动
    def test_35_track_absence_without_modified_at_change_not_deleted(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"),
                                 modified_at="2026-10-09T03:00:00Z")))
        self.sp.deliver("A")
        self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "x"), ("tx", "y"))))
        self.sp.deliver("B")
        self.sp.deliver("A")
        r = self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"),
                                      modified_at="2026-10-09T03:00:00Z")))
        self.assertEqual(r.meta["removed_tracks"], {})
        pid = pl_id_of(self.sp, "歌单")
        self.assertIn("tx:y", live_track_keys(self.sp, pid))

    # 不带 modified_at → 新判据不生效
    def test_36_track_absence_without_modified_at_field_not_deleted(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "x"), ("tx", "y"))))
        self.sp.deliver("B")
        self.sp.deliver("A")
        r = self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.assertEqual(r.meta["removed_tracks"], {})
        pid = pl_id_of(self.sp, "歌单")
        self.assertIn("tx:y", live_track_keys(self.sp, pid))


if __name__ == "__main__":
    unittest.main(verbosity=2)
