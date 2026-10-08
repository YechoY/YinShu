"""故障注入与语义校验（docs/07 §7.4 测试策略 + §5.6 错误与回滚）。

覆盖：坏 JSON 拒绝、I2' revision 闸门、安全阀挂起、base_served 语义（I1/F3）、
幂等收敛（I4）、墓碑压制（I7）、确认水位 GC（D21/T3）。
"""
from __future__ import annotations

import unittest

from engine.engine import SyncSpace
from engine.canonical import InvalidPayload

from .helpers import pl, sub, tr


def pl_id_of(space, name):
    for pid, p in space.playlists.items():
        if p is not None and p.deleted_at is None and p.name == name:
            return pid
    raise AssertionError(name)


def live_keys(space, pl_name):
    pid = pl_id_of(space, pl_name)
    return {space.tracks[t].key for t in space.playlists[pid].track_ids
            if space.tracks[t].deleted_at is None}


class TestFaultInjection(unittest.TestCase):

    def setUp(self):
        self.sp = SyncSpace("fi")
        # 第四轮 P0-C：测试默认关闭冷静期（GC 只靠确认水位）；冷静期专项测试单独设大值
        self.sp.restore_grace_seconds = 0
        self.sp.register_client("A", dialect="cyshine-v1", identity_verified=True)
        self.sp.register_client("B", dialect="cyshine-v1", identity_verified=True)

    # 坏 JSON / 截断：解析抛 InvalidPayload，被 engine 转 400，基线不动
    def test_bad_payload_400(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        rev = self.sp.revision
        for bad in (None, [], "garbage",
                    {"playlists": [{"native_id": "p1", "name": "歌单",
                                    "tracks": [{"source": "tx"}]}]}):  # 缺 songId
            r = self.sp.merge("A", bad)
            self.assertEqual(r.status, 400, msg=f"bad={bad!r}")
        self.assertEqual(self.sp.revision, rev)

    # I2' revision 闸门：期间有别端写过 → 本次禁止产生删除（只并集 + 记候选）
    def test_revision_gate_blocks_deletion(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")                      # A.base_served = rev1
        self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "x"), ("wy", "y"))))  # B 加 y → rev2
        self.sp.deliver("B")
        # A 拿旧视图(rev1)回写"删掉 x" → 闸门：不删除，只记 suspects
        r = self.sp.merge("A", sub(pl("p1", "歌单")))
        self.assertTrue(r.meta["suspects"])
        self.assertEqual(live_keys(self.sp, "歌单"), {"tx:x", "wy:y"})  # 未删除
        self.assertFalse(self.sp.pending_deletions)

    # 安全阀挂起（不可信端点）：单次删除超阈值 → 新增照常、删除挂起、渲染仍含、200、可确认
    # （第四轮 P1-2：trusted_direct_delete=False 时恢复旧安全阀行为；可信客户端默认直删）
    def test_safety_valve_defers_add_and_delete(self):
        self.sp.trusted_direct_delete = False
        self.sp.delete_cards_enabled = True    # 第八轮默认 False（永不挂卡）；显式测旧通道
        self.sp.merge("A", sub(pl("p1", "歌单", *[("tx", f"t{i}") for i in range(12)])))
        self.sp.deliver("A")
        # 同时删 11 首（触发）并新增 z → 新增照常应用，删除挂起
        r = self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "t0"), ("tx", "z"))))
        self.assertEqual(r.status, 200)
        self.assertTrue(r.meta["deferred"])
        self.assertIn("tx:z", live_keys(self.sp, "歌单"))   # 新增已应用
        self.assertEqual(len(self.sp.tombstones), 0)        # 删除未写墓碑
        self.assertEqual(len(live_keys(self.sp, "歌单")), 13)  # 12 原曲 + z，删除仍含（D4）
        pid = next(iter(self.sp.pending_deletions))
        self.sp.confirm_delete(pid)
        self.assertEqual(live_keys(self.sp, "歌单"), {"tx:t0", "tx:z"})

    # 安全阀：用户放弃（reject）→ 条目保留，不写墓碑（不可信端点）
    def test_safety_valve_reject_keeps(self):
        self.sp.trusted_direct_delete = False
        self.sp.delete_cards_enabled = True
        self.sp.merge("A", sub(pl("p1", "歌单", *[("tx", f"t{i}") for i in range(12)])))
        self.sp.deliver("A")
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "t0"))))
        pid = next(iter(self.sp.pending_deletions))
        self.sp.reject_delete(pid)
        self.assertEqual(len(live_keys(self.sp, "歌单")), 12)
        self.assertEqual(len(self.sp.tombstones), 0)

    # I1 / F3：PUT 响应体不算交付，base_served 只在 GET 后推进
    def test_base_served_advances_only_on_get(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.assertIsNone(self.sp.clients["A"].base_served)   # PUT 未交付
        self.sp.deliver("A")
        self.assertIsNotNone(self.sp.clients["A"].base_served)
        self.assertEqual(self.sp.clients["A"].base_served.served_revision, self.sp.revision)

    # I4：幂等收敛 —— 同一请求重复执行结果一致，revision 不漂移
    def test_idempotent_convergence(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        rev = self.sp.revision
        for _ in range(5):
            self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.assertEqual(self.sp.revision, rev)
        # 已删除的条目被未验证新身份反复提交 → 一致压制（幂等），不复活
        # （第四轮 P1-1：默认无待确认恢复卡，抑制信息由 meta.suppressed_tracks 承载）
        self.sp.merge("A", sub(pl("p1", "歌单")))
        self.sp.deliver("A")
        self.sp.register_client("D", dialect="cyshine-v1", identity_verified=False)
        for _ in range(3):
            r = self.sp.merge("D", sub(pl("p1", "歌单", ("tx", "x"))))
            self.assertNotIn("tx:x", live_keys(self.sp, "歌单"))
            self.assertIn("tx:x", r.meta["suppressed_tracks"])
            self.assertNotIn("tx:x", self.sp.pending_restores)

    # D21：确认水位 GC（第四轮 P0-B）——所有已注册客户端都**看过删除后视图**
    # （deliver 交付不含该键 → ack；merge 提交确认同样 ack）才 GC；
    # 回推残留（carried）会撤销 ack，墓碑继续存活压制
    def test_tombstone_gc_by_ack_watermark(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "x"))))  # B 已注册但未交付
        self.sp.merge("A", sub(pl("p1", "歌单")))  # A 删除 x（A 提交确认 ack）
        self.assertIn("tx:x", self.sp.tombstones)   # B 未看过删除视图 → 不 GC
        self.sp.deliver("B")                        # B 交付不含 x → 看过删除视图 → ack
        self.assertNotIn("tx:x", self.sp.tombstones)  # 全员确认 → GC
        # B 又把 x 带回（残留回推）→ 无墓碑 → 正常并入（恢复/重加按当前判据处理）
        r = self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "x"))))
        self.assertIn("tx:x", live_keys(self.sp, "歌单"))

    # 第四轮 P0-B（§4.1 点名）：全员看过删除视图后墓碑 GC，且该客户端之后 PUT
    # 不带该键（已应用删除，时间戳压过本地），空间不再保留任何删除痕迹
    def test_tombstone_gc_after_all_clients_saw_deletion(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("B")
        self.sp.deliver("A")                 # A 删前对齐
        self.sp.merge("A", sub(pl("p1", "歌单")))   # A 删除 x
        self.assertIn("tx:x", self.sp.tombstones)
        self.sp.deliver("B")                 # B 看过删除视图 → ack
        self.assertNotIn("tx:x", self.sp.tombstones)   # A/B 全员确认 → GC
        # 之后 B PUT 不含该键（删除已应用，根因 A 修复后时间戳压过本地）
        r = self.sp.merge("B", sub(pl("p1", "歌单")))
        self.assertNotIn("tx:x", r.meta["suppressed_tracks"])
        self.assertNotIn("tx:x", live_keys(self.sp, "歌单"))

    # 第四轮 P0-B：客户端持续回推已删条目 → 墓碑不被 GC、回推被压制
    # （构造：C 从未看过删除视图，钉住确认水位；B 看过但回推 → ack 撤销）
    def test_tombstone_survives_when_client_keeps_repushing(self):
        # 冷静期打开（回推按残留压制；0 秒冷静期下"看过+回推"会走显式恢复）
        self.sp.restore_grace_seconds = 3600
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.register_client("C", dialect="cyshine-v1", identity_verified=True)
        self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("B")
        self.sp.merge("C", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")   # A 对齐
        self.sp.merge("A", sub(pl("p1", "歌单")))   # A 删 x（A 提交确认）
        self.sp.deliver("B")                        # B 看过删除视图 → ack（C 未确认 → 不 GC）
        self.assertIn("tx:x", self.sp.tombstones)
        # B 回推 x → 冷静期内 → 压制；carried → ack(B) 撤销，墓碑继续存活
        r = self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "x"))))
        self.assertIn("tx:x", r.meta["suppressed_tracks"])
        self.assertIn("tx:x", self.sp.tombstones)
        self.assertNotIn("tx:x", live_keys(self.sp, "歌单"))
        # C 也看过删除视图 → 全员 ack？B 已撤销 → 未全员 → 墓碑仍存活
        self.sp.deliver("C")
        self.assertIn("tx:x", self.sp.tombstones)
        # B 最终删掉本地（提交空视图）→ ack(B) 恢复 → 全员看过 → GC（第四轮 P0-B 简单版）
        self.sp.merge("B", sub(pl("p1", "歌单")))
        self.assertNotIn("tx:x", self.sp.tombstones)

    # 并发串行化：多次交错 PUT 后，全客户端视图一致、revision 有界
    def test_serialized_interleave_bounded(self):
        self.sp.register_client("C", dialect="cyshine-v1", identity_verified=True)
        for i in range(20):
            self.sp.merge("A", sub(pl("p1", "歌单", ("tx", f"a{i}"))))
            self.sp.merge("B", sub(pl("p2", "歌单乙", ("wy", f"b{i}"))))
        self.sp.deliver("A"); self.sp.deliver("B"); self.sp.deliver("C")
        self.assertEqual(live_keys(self.sp, "歌单"), {f"tx:a{i}" for i in range(20)})
        self.assertEqual(live_keys(self.sp, "歌单乙"), {f"wy:b{i}" for i in range(20)})
        self.assertEqual(len(self.sp.identity_to_tr), 40)


if __name__ == "__main__":
    unittest.main(verbosity=2)
