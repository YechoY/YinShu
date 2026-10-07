"""26 条合并引擎边界用例（docs/05 §5.5），对应不变量 I1–I7 与验收 V1–V10。

每条用例一个 test_ 方法，编号注释对齐 docs/05 §5.5 表格。
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
        # 第四轮 P0-C：测试默认关闭删除冷静期（恢复语义立即生效）；
        # 冷静期专项测试单独设大值
        self.sp.restore_grace_seconds = 0
        self.sp.register_client("A", dialect="cyshine-v1", identity_verified=True)
        self.sp.register_client("B", dialect="cyshine-v1", identity_verified=True)

    # 用例 1：单端加歌 → 对端可见
    def test_01_add_track_propagates(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("B")
        self.assertIn("tx:x", delivered_tracks(self.sp, "B", "歌单"))

    # 用例 2：单端删歌 → 对端消失且不复活（多轮同步 + 新客户端接入都不复活）
    def test_02_delete_track_no_revive(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("B")
        self.sp.deliver("A")                     # A 删前先 GET（对齐最新 revision，I2'）
        self.sp.merge("A", sub(pl("p1", "歌单")))  # A 删除 x
        self.sp.deliver("B")
        self.assertNotIn("tx:x", delivered_tracks(self.sp, "B", "歌单"))
        # 多轮同步：A 再提交同一空视图，x 仍不复活
        self.sp.merge("A", sub(pl("p1", "歌单")))
        self.sp.deliver("B")
        self.assertNotIn("tx:x", delivered_tracks(self.sp, "B", "歌单"))
        # 第四轮 P0-B：A/B 均已看过删除视图（deliver 即 ack）→ 全员确认 → 墓碑已 GC
        # （删除成为空间事实）。此后新接入客户端 C 带残留 → 无墓碑可压制 → 复活 =
        # 显式恢复（文档 §2.4.3 语义：空间内设备"看过删除结果后重加"直接生效）。
        # 注：真实栖弦新设备首次接入先 GET（拿删除后视图）不会带残留 PUT。
        self.sp.register_client("C", dialect="cyshine-v1", identity_verified=False)
        rc = self.sp.merge("C", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("C")
        self.assertIn("tx:x", delivered_tracks(self.sp, "C", "歌单"))
        self.assertNotIn("tx:x", rc.meta["suppressed_tracks"])
        self.assertNotIn("tx:x", self.sp.tombstones)   # 墓碑已随全员确认收走

    # 用例 3：两端各加不同歌 → 并集
    def test_03_union(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.merge("B", sub(pl("p1", "歌单", ("wy", "y"))))
        self.sp.deliver("B")
        self.sp.deliver("A")
        self.assertEqual(delivered_tracks(self.sp, "A", "歌单"), {"tx:x", "wy:y"})
        self.assertEqual(delivered_tracks(self.sp, "B", "歌单"), {"tx:x", "wy:y"})

    # 用例 4：一端加、一端删同一首 → 删除优先，最终不存在
    def test_04_delete_wins(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("B")
        # B 删除 x（B 已 GET，I2' 放行）
        self.sp.merge("B", sub(pl("p1", "歌单")))
        # A 尚未被交付删除，基于旧视图回写（仍带 x）→ x 已被墓碑化，删除优先，最终不存在
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        self.assertNotIn("tx:x", live_track_keys(self.sp, pl_id_of(self.sp, "歌单")))
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")), set())

    # 用例 5：两端各删不同歌 → 都消失
    def test_05_both_delete(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"), ("wy", "y"))))
        self.sp.deliver("A")
        self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "x"), ("wy", "y"))))
        self.sp.deliver("B")
        self.sp.deliver("A")
        self.sp.merge("A", sub(pl("p1", "歌单", ("wy", "y"))))  # A 删 x
        self.sp.deliver("B")                                     # B 先 GET（学到 x 已删）
        self.sp.merge("B", sub(pl("p1", "歌单")))                # B 删 y
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

    # 用例 7：删除歌单 → 对端消失且不复活
    def test_07_delete_playlist_no_revive(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("B")
        self.sp.deliver("A")
        self.sp.merge("A", sub())
        self.sp.deliver("B")
        self.assertNotIn("歌单", {e["name"] for e in self.sp.clients["B"].base_served.view.values()})
        # 第四轮 P0-B：A/B 全员看过删除视图 → 墓碑已 GC → 新接入客户端 C 带残留
        # → 复活（显式恢复语义，无墓碑可压制）。真实栖弦新设备首接先 GET，不会直接带残留 PUT。
        self.sp.register_client("C", dialect="cyshine-v1", identity_verified=False)
        self.sp.merge("C", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("C")
        self.assertIn("歌单", {e["name"] for e in self.sp.clients["C"].base_served.view.values()})

    # 用例 8：首轮接入（空间已有数据）→ 只增不删，空间数据不减少
    def test_08_first_adoption_keeps_existing(self):
        self.sp.merge("A", sub(pl("p1", "歌单甲", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.register_client("C", dialect="ceru-plugin", identity_verified=False)
        self.sp.merge("C", sub(pl("p9", "歌单乙", ("kw", "k"))))
        self.sp.deliver("C")
        # 空间仍保留 A 的歌单
        self.assertIn("歌单甲", {e["name"] for e in self.sp.clients["C"].base_served.view.values()})
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单甲")), {"tx:x"})

    # 用例 9：客户端清数据重装 → 只增不删；已删的风控提示但不血洗
    def test_09_reinstall_no_wipe(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"), ("wy", "y"))))
        self.sp.deliver("A")
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))  # A 删 y
        self.sp.deliver("A")
        # 重装 = 新身份客户端 C 提交其本地残留（含已删的 y）
        self.sp.register_client("C", dialect="cyshine-v1", identity_verified=False)
        rc = self.sp.merge("C", sub(pl("p1", "歌单", ("tx", "x"), ("wy", "y"))))
        self.sp.deliver("C")
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")), {"tx:x"})
        self.assertIn("wy:y", rc.meta["suppressed_tracks"])

    # 用例 10：歌单改名 → 不复制歌单（靠 native-id 别名）
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

    # 用例 11：同名歌单来自两个平台 → 按名字别名合并，不产生重复
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

    # 用例 12：跨源同 songId → 视为两首，不撞车
    def test_12_cross_source_same_song_id(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("wy", "123"), ("kw", "123"))))
        self.sp.deliver("A")
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")),
                         {"wy:123", "kw:123"})
        # 删除 wy:123 不影响 kw:123
        self.sp.merge("A", sub(pl("p1", "歌单", ("kw", "123"))))
        self.sp.deliver("A")
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")), {"kw:123"})

    # 用例 13：坏 JSON / 截断 → 拒绝并回滚，基线不动
    def test_13_bad_payload_rejected(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        rev_before = self.sp.revision
        sub_before = self.sp.clients["A"].base_submitted
        # playlists 不是数组 / track 缺身份 → 400，基线不动
        r = self.sp.merge("A", {"playlists": "not-a-list"})
        self.assertEqual(r.status, 400)
        r2 = self.sp.merge("A", sub({"native_id": "p1", "name": "歌单",
                                     "tracks": [{"source": "", "songId": ""}]}))
        self.assertEqual(r2.status, 400)
        self.assertEqual(self.sp.revision, rev_before)
        self.assertIs(self.sp.clients["A"].base_submitted, sub_before)

    # 用例 14：删除比例 80% → 安全阀（不可信端点）：新增照常、删除挂起且不写墓碑、渲染仍含、200、待确认
    # （第四轮 P1-2：trusted_direct_delete=False 时恢复旧安全阀行为；可信客户端默认直删，见 test_14b）
    def test_14_safety_valve_80_percent(self):
        self.sp.trusted_direct_delete = False
        tracks = [("tx", f"t{i}") for i in range(12)]
        self.sp.merge("A", sub(pl("p1", "歌单", *tracks)))
        self.sp.deliver("A")
        # A 删 11 首（11/12=91.7% ≥50% 且 ≥10）→ 挂起
        r = self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "t0"))))
        self.assertEqual(r.status, 200)
        self.assertTrue(r.meta["deferred"])
        self.assertTrue(self.sp.pending_deletions)
        # 删除未写墓碑，渲染仍含全部曲目（D4）
        self.assertEqual(len(live_track_keys(self.sp, pl_id_of(self.sp, "歌单"))), 12)
        self.assertEqual(len(self.sp.tombstones), 0)
        # 用户确认后才真正删除
        pid = next(iter(self.sp.pending_deletions))
        self.sp.confirm_delete(pid)
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")), {"tx:t0"})

    # 用例 14b：可信客户端批量删除直接生效（第四轮 P1-2，trusted_direct_delete 默认 True）
    def test_14b_trusted_bulk_delete_applies_without_pending(self):
        tracks = [("tx", f"t{i}") for i in range(12)]
        self.sp.merge("A", sub(pl("p1", "歌单", *tracks)))
        self.sp.deliver("A")
        r = self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "t0"))))
        self.assertEqual(r.status, 200)
        self.assertFalse(r.meta["deferred"])          # 可信直删：不挂起
        self.assertFalse(self.sp.pending_deletions)   # 无确认卡
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")), {"tx:t0"})

    # 用例 14c（§4.1 点名）：trusted_direct_delete=False 恢复旧安全阀语义
    # （不可信端点/开关关闭时，批量删除仍挂起待确认，与 test_14 行为一致）
    def test_trusted_direct_delete_off_restores_old_valve(self):
        self.sp.trusted_direct_delete = False
        tracks = [("tx", f"t{i}") for i in range(12)]
        self.sp.merge("A", sub(pl("p1", "歌单", *tracks)))
        self.sp.deliver("A")
        # 可信客户端删 11/12 → 开关关 → 旧语义：挂起、不写墓碑、渲染仍含全部
        r = self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "t0"))))
        self.assertEqual(r.status, 200)
        self.assertTrue(r.meta["deferred"])
        self.assertTrue(self.sp.pending_deletions)
        self.assertEqual(len(live_track_keys(self.sp, pl_id_of(self.sp, "歌单"))), 12)
        self.assertEqual(len(self.sp.tombstones), 0)
        # 确认后才真删
        self.sp.confirm_delete(next(iter(self.sp.pending_deletions)))
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")), {"tx:t0"})

    # 用例 15：未知方言 → 隔离保存，其它客户端不受影响
    def test_15_unknown_dialect_isolated(self):
        self.sp.register_client("U", dialect="mystery-v1", identity_verified=False)
        r = self.sp.merge("U", sub(pl("u1", "怪格式", ("tx", "x"))))
        self.assertTrue(r.meta.get("isolated"))
        self.assertIn("U", self.sp.quarantine)
        self.assertEqual(self.sp.tracks, {})
        # 已知客户端不受影响
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")), {"tx:x"})

    # 用例 16：重复提交同一请求 → 幂等，无额外变化
    def test_16_idempotent(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        rev_before = self.sp.revision
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.assertEqual(self.sp.revision, rev_before)  # 内容未变不推进 revision（E1）
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")), {"tx:x"})

    # 用例 17：两端同时写 → 串行化，结果一致
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

    # 用例 18：本地文件曲目（opaque）round-trip → 原样带回，不被删也不参与合并
    def test_18_opaque_roundtrip(self):
        opaque = {"cyshine": {"tracks": [{"path": "file:///C:/music/a.mp3"}]}}
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x")), opaque=opaque))
        self.sp.deliver("A")
        self.assertEqual(self.sp.clients["A"].opaque, opaque)
        # 对端 B 提交，不触碰 A 的 opaque（不在 canonical，无法被删）
        self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("B")
        self.assertEqual(self.sp.clients["A"].opaque, opaque)
        # A 的曲目池不含本地文件（不入 canonical 曲目池）
        self.assertEqual(set(self.sp.identity_to_tr.keys()), {"tx:x"})

    # 用例 19：设备身份 —— 同一可信设备稳定不丢；重装(新身份)小比例缺失不触发删除
    def test_19_device_identity(self):
        # (a) 同一可信设备：两侧各自新增并集，稳定不丢
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"), ("wy", "y"))))
        self.sp.deliver("A")
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")), {"tx:x", "wy:y"})
        # (b) 重装 = 新身份未验证客户端，提交缺 1 首的旧副本 → 首次接入只增不删，不误删
        self.sp.register_client("R", dialect="cyshine-v1", identity_verified=False)
        self.sp.merge("R", sub(pl("p1", "歌单", ("tx", "x"))))  # 缺 wy:y
        self.sp.deliver("R")
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")), {"tx:x", "wy:y"})
        self.assertFalse(self.sp.pending_deletions)

    # 用例 20：三客户端交错 GET/PUT。第四轮"同空间成员互信"：
    # A GET 对齐最新后回写自己的旧段（本地 LWW 保留 p1）→ p2/p3 ∉ owned(A)
    # ⇒ 缺席即删（互信直删，写墓碑）；B/C 看过删除视图后回推 ⇒ 直接恢复（无卡）。
    def test_20_three_client_interleave(self):
        self.sp.register_client("C", dialect="cyshine-v1", identity_verified=True)
        self.sp.merge("A", sub(pl("p1", "歌单甲", ("tx", "x"))))
        self.sp.deliver("A")                       # rev1
        self.sp.merge("B", sub(pl("p2", "歌单乙", ("wy", "y"))))  # B 写但未 GET
        self.sp.merge("C", sub(pl("p3", "歌单丙", ("kw", "k"))))  # C 首接
        # A GET 对齐最新（含 p2/p3）后回写旧段
        self.sp.deliver("A")
        ra = self.sp.merge("A", sub(pl("p1", "歌单甲", ("tx", "x"))))
        self.assertEqual(len(ra.meta["removed_playlists"]), 2)      # p2/p3 缺席即删
        yb = next(pl for pl in self.sp.playlists.values() if pl.name == "歌单乙")
        self.assertIsNotNone(yb.deleted_at)                         # 已删（deleted_at 置位）
        jb = next(pl for pl in self.sp.playlists.values() if pl.name == "歌单丙")
        self.assertIsNotNone(jb.deleted_at)
        # B GET（看过删除视图，rev 已越过删除点）后回推完整本地视图（p1+p2）
        # → p2 显式恢复、无卡；p1 保留（整段覆盖客户端带全量）
        self.sp.deliver("B")
        rb = self.sp.merge("B", sub(pl("p1", "歌单甲", ("tx", "x")),
                                    pl("p2", "歌单乙", ("wy", "y"))))
        self.assertEqual(len(rb.meta["added_playlists"]), 1)        # p2 恢复
        self.assertFalse(rb.meta["suppressed_playlists"])
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单甲")), {"tx:x"})
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单乙")), {"wy:y"})
        jb2 = next(pl for pl in self.sp.playlists.values() if pl.name == "歌单丙")
        self.assertIsNotNone(jb2.deleted_at)   # p3 未被回推，仍处于已删状态
        self.assertFalse(self.sp.pending_restores)   # 全程无待确认卡

    # 用例 21：客户端只 GET 不 PUT → 无基线 GET 404（P0-1），不产生删除、不推进基线
    def test_21_get_only_no_delete(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.register_client("R", dialect="cyshine-v1", identity_verified=True)
        r = self.sp.deliver("R")                     # R 只 GET、从未提交
        self.assertEqual(r.status, 404)              # 无基线：从未交付且从未提交
        self.assertIsNone(self.sp.clients["R"].base_served)   # 404 不推进基线
        # 先 PUT 建基线后 GET 正常
        self.sp.merge("R", sub(pl("p1", "歌单", ("tx", "x"))))
        r2 = self.sp.deliver("R")
        self.assertEqual(r2.status, 200)
        self.assertIsNotNone(self.sp.clients["R"].base_served)
        self.assertEqual(delivered_tracks(self.sp, "R", "歌单"), {"tx:x"})
        # R 本地"以为删了 x"但从不 PUT → 不误判为用户删除
        self.sp.deliver("A")
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")), {"tx:x"})

    # 用例 22：客户端清数据重装后回写空视图 → 不血洗（首次接入只增不删）
    def test_22_rewrite_empty_not_wipe(self):
        self.sp.merge("A", sub(pl("p1", "歌单", *[("tx", f"t{i}") for i in range(10)])))
        self.sp.deliver("A")
        self.sp.register_client("R", dialect="cyshine-v1", identity_verified=False)
        self.sp.merge("R", sub(pl("p1", "歌单")))  # 空视图
        self.sp.deliver("R")
        self.assertEqual(len(live_track_keys(self.sp, pl_id_of(self.sp, "歌单"))), 10)
        self.assertFalse(self.sp.pending_deletions)

    # 用例 23：离线 90 天后回归 → 不复活、不误删（base_served 水位 + 墓碑压制）
    def test_23_offline_return_no_revive(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("B")                      # B 的 base_served = rev1（含 x）
        self.sp.deliver("A")
        self.sp.merge("A", sub(pl("p1", "歌单")))  # A 删 x → rev2 墓碑
        self.sp.deliver("A")
        # B 离线 90 天后拿旧视图（仍含 x）回归 → 未越过删除点 → 压制，不复活
        self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("B")
        self.assertNotIn("tx:x", live_track_keys(self.sp, pl_id_of(self.sp, "歌单")))
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")), set())

    # 用例 24：未适配方言客户端写入 → 隔离，不污染其它端
    def test_24_unadapted_dialect_no_pollution(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.register_client("U", dialect="ceru-v9-legacy", identity_verified=False)
        r = self.sp.merge("U", sub(pl("u1", "怪格式", ("kw", "k"))))
        self.assertTrue(r.meta.get("isolated"))
        # 已知客户端视图不受污染
        self.sp.deliver("A")
        names = {e["name"] for e in self.sp.clients["A"].base_served.view.values()}
        self.assertEqual(names, {"歌单"})
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")), {"tx:x"})

    # 用例 25：首次接入两端同时 PUT（If-None-Match:* 语义）
    # 审查 §8：栖弦把 412 当失败会整轮重跑，本阶段绝不主动回 412——
    # createOnly 且已有基线 ⇒ 按基线合并（200），绝不 412（P0 修法之一）
    def test_25_first_adoption_create_only(self):
        # 同一文件路径的 createOnly 创建：首次 201，再次按基线合并 200（不 412）
        r1 = self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x")), create_only=True))
        self.assertEqual(r1.status, 201)
        r2 = self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x")), create_only=True))
        self.assertEqual(r2.status, 200)
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")), {"tx:x"})
        # 两个全新客户端并发首次接入 → 都只增不删，串行化，无静默覆盖
        self.sp.register_client("Ceru", dialect="ceru-plugin", identity_verified=False)
        self.sp.merge("Ceru", sub(pl("p2", "歌单乙", ("wy", "y"))))
        self.sp.deliver("A")
        self.sp.deliver("Ceru")
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")), {"tx:x"})
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单乙")), {"wy:y"})

    # 用例 26：新身份设备提交含"已墓碑条目"的视图 → 不复活（压制）
    # （第四轮 P1-1：默认无待确认恢复卡；抑制信息由 meta.suppressed_* 承载）
    def test_26_new_identity_tombstone_suppressed(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"), ("wy", "y"))))
        self.sp.deliver("A")
        self.sp.merge("A", sub(pl("p1", "歌单", ("wy", "y"))))  # A 删 x
        self.sp.deliver("A")
        # 新身份（未验证）设备提交含已删 x 的旧副本 → 压制，y 正常并入
        self.sp.register_client("D", dialect="cyshine-v1", identity_verified=False)
        r = self.sp.merge("D", sub(pl("p1", "歌单", ("tx", "x"), ("wy", "y"))))
        self.sp.deliver("D")
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")), {"wy:y"})
        self.assertIn("tx:x", r.meta["suppressed_tracks"])
        self.assertNotIn("tx:x", self.sp.pending_restores)   # 无待确认恢复卡
        # 再次提交同一视图 → 幂等压制，不复活
        self.sp.merge("D", sub(pl("p1", "歌单", ("tx", "x"), ("wy", "y"))))
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")), {"wy:y"})

    # 补充 D21：已越过删除点的可信身份重加同一 key = 显式恢复，直接生效并清墓碑
    def test_26b_trusted_explicit_restore(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.merge("A", sub(pl("p1", "歌单")))  # A 删 x
        self.sp.deliver("A")                       # A 已越过删除点（rev 已交付）
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))  # A 显式重加
        self.sp.deliver("A")
        self.assertEqual(live_track_keys(self.sp, pl_id_of(self.sp, "歌单")), {"tx:x"})
        self.assertNotIn("tx:x", self.sp.tombstones)
        self.assertNotIn("tx:x", self.sp.pending_restores)

    # 排序同步：歌单内顺序在 merge + deliver 全链路保留（kg → tx → wy，不被 key 排序打乱）
    def test_order_preserved_through_merge_and_deliver(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("kg", "1"), ("tx", "2"), ("wy", "3"))))
        self.sp.deliver("A")
        self.assertEqual(self._served_order(self.sp, "A", "歌单"), ["kg:1", "tx:2", "wy:3"])
        # 对端 B 接入后交付顺序一致
        self.sp.merge("B", sub(pl("p1", "歌单", ("kg", "1"), ("tx", "2"), ("wy", "3"))))
        self.sp.deliver("B")
        self.assertEqual(self._served_order(self.sp, "B", "歌单"), ["kg:1", "tx:2", "wy:3"])

    # 排序同步：已有歌单追加新歌，原顺序保持，新歌追加到末尾
    def test_order_append_new_tracks_keeps_existing(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("kg", "1"), ("tx", "2"))))
        self.sp.deliver("A")
        self.assertEqual(self._served_order(self.sp, "A", "歌单"), ["kg:1", "tx:2"])
        self.sp.merge("A", sub(pl("p1", "歌单", ("kg", "1"), ("tx", "2"), ("mg", "9"))))
        self.sp.deliver("A")
        self.assertEqual(self._served_order(self.sp, "A", "歌单"),
                         ["kg:1", "tx:2", "mg:9"], "新歌追加末尾，原顺序不变")

    # 排序同步：已有歌单集合不变、顺序不同（纯重排）→ 应用提交顺序并传播
    def test_order_reorder_propagates(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("kg", "1"), ("tx", "2"))))
        self.sp.deliver("A")
        self.assertEqual(self._served_order(self.sp, "A", "歌单"), ["kg:1", "tx:2"])
        # A 重排：tx 放到最前
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "2"), ("kg", "1"))))
        self.sp.deliver("A")
        self.assertEqual(self._served_order(self.sp, "A", "歌单"), ["tx:2", "kg:1"], "纯重排应传播")

    def _served_order(self, space: SyncSpace, client_id: str, pl_name: str):
        c = space.clients[client_id]
        if c.base_served is None:
            return []
        for pl_id, e in c.base_served.view.items():
            if e["name"] == pl_name:
                return list(e["tracks"])
        return []

    # 第三轮 §3.1：peek_view 追认为"纯只读"——快照逐字节不变 + view 非空 + stamp 确定性
    def test_33_peek_view_is_pure_read(self):
        sp = SyncSpace("test")
        sp.register_client("A", dialect="cyshine-v1", identity_verified=True)
        sp.merge("A", sub(pl("p1", "歌单1", ("tx", "x"), ("tx", "y"))))
        sp.merge("A", sub(pl("p2", "歌单2", ("tx", "z"))))
        sp.deliver("A")
        # 调用前快照（浅拷贝 clients / 拷贝 tombstones ack 集合）
        rev0, tag0 = sp.revision, sp.sort_tag
        cc0, oc0 = sp.content_changed_at, sp.order_changed_at
        clients0 = dict(sp.clients)
        toms0 = {k: set(t.acked) for k, t in sp.tombstones.items()}
        jlen0 = len(sp.journal)
        # 调 peek_view
        view, _cleanup, stamp = sp.peek_view("cyshine-v1", can_delete=True)
        # view 非空（两首歌单都渲染出来）
        self.assertTrue(view)
        self.assertEqual(len(view), 2)
        # stamp 确定性 = max(内容/排序最后真变时刻)；任一为空则取另一（与实现同口径）
        cands = [x for x in (sp.content_changed_at, sp.order_changed_at) if x]
        self.assertEqual(stamp, max(cands))
        # 纯读：以下状态逐字节不变
        self.assertEqual(sp.revision, rev0)
        self.assertEqual(sp.sort_tag, tag0)
        self.assertEqual(sp.content_changed_at, cc0)
        self.assertEqual(sp.order_changed_at, oc0)
        self.assertEqual(dict(sp.clients), clients0)
        self.assertEqual({k: set(t.acked) for k, t in sp.tombstones.items()}, toms0)
        self.assertEqual(len(sp.journal), jlen0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
