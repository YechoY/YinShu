"""故障注入与语义校验（D34 简化后）。

覆盖：坏 JSON 拒绝、段没动不判删（M1 保护）、untrusted 删 owned 生效、
base_served 语义（I1/F3）、幂等收敛（I4）、删除后加回来就恢复。
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

    # M1 保护：trusted 客户端段没 modified_at 变化 → 缺席不判删
    def test_stale_view_no_mod_change_no_delete(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "x"), ("wy", "y"))))
        self.sp.deliver("B")
        # A 拿旧视图回写"删掉 x"，但段没 modified_at 变化 → M1 保护 → 不删
        r = self.sp.merge("A", sub(pl("p1", "歌单")))
        self.assertEqual(live_keys(self.sp, "歌单"), {"tx:x", "wy:y"})  # 未删除

    # D34：untrusted 客户端删 owned 曲目 → 删生效（无安全阀拦截）
    def test_untrusted_bulk_delete(self):
        CE = self.sp.register_client("CE", dialect="ceru-plugin",
                                     identity_verified=True, can_delete=False)
        self.sp.merge("CE", sub(pl("p1", "歌单", *[("tx", f"t{i}") for i in range(12)])))
        self.sp.deliver("CE")
        # CE 删 11 首 + 新增 z → 删 owned(t1..t11) 生效，z 并入
        r = self.sp.merge("CE", sub(pl("p1", "歌单", ("tx", "t0"), ("tx", "z"))))
        self.assertEqual(r.status, 200)
        self.assertIn("tx:z", live_keys(self.sp, "歌单"))
        self.assertEqual(live_keys(self.sp, "歌单"), {"tx:t0", "tx:z"})

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
        # D34：删除后加回来就恢复（无冷静期压制）
        self.sp.merge("A", sub(pl("p1", "歌单", modified_at="2026-10-09T12:00:00Z")))
        self.sp.deliver("A")
        self.assertNotIn("tx:x", live_keys(self.sp, "歌单"))
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"),
                                 modified_at="2026-10-09T12:01:00Z")))
        self.assertIn("tx:x", live_keys(self.sp, "歌单"))  # 加回来就恢复

    # D34：删除后加回来就恢复（无墓碑、无冷静期）
    def test_delete_then_readd_restores(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("B")
        # A 删 x（段 modified_at 变化 → 判删）
        self.sp.merge("A", sub(pl("p1", "歌单", modified_at="2026-10-09T12:00:00Z")))
        self.assertNotIn("tx:x", live_keys(self.sp, "歌单"))
        # B 回推 x → 加回来就恢复
        self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "x"))))
        self.assertIn("tx:x", live_keys(self.sp, "歌单"))

    # D34：删除传播后保持删除，直到有人加回来
    def test_delete_propagates_and_stays(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("B")
        self.sp.deliver("A")
        # A 删 x（段没 modified_at → M1 保护 → 不删）
        # 需要让 A 提交时 modified_at 变化才能判删
        self.sp.merge("A", sub(pl("p1", "歌单", modified_at="2026-10-09T12:00:00Z")))
        self.assertNotIn("tx:x", live_keys(self.sp, "歌单"))
        # B 看到删除结果
        self.sp.deliver("B")
        self.assertNotIn("tx:x", live_keys(self.sp, "歌单"))
        # B PUT 不含 x → 删除保持（无墓碑压制，但也没人加回来）
        self.sp.merge("B", sub(pl("p1", "歌单")))
        self.assertNotIn("tx:x", live_keys(self.sp, "歌单"))

    # D34：持续回推 → 加回来就加回来（无压制）
    def test_readd_always_restores(self):
        self.sp.merge("A", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        self.sp.register_client("C", dialect="cyshine-v1", identity_verified=True)
        self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("B")
        self.sp.merge("C", sub(pl("p1", "歌单", ("tx", "x"))))
        self.sp.deliver("A")
        # A 删 x（段 modified_at 变化 → 判删）
        self.sp.merge("A", sub(pl("p1", "歌单", modified_at="2026-10-09T12:00:00Z")))
        self.assertNotIn("tx:x", live_keys(self.sp, "歌单"))
        # B 回推 x → 加回来就恢复（无冷静期压制）
        self.sp.merge("B", sub(pl("p1", "歌单", ("tx", "x"))))
        self.assertIn("tx:x", live_keys(self.sp, "歌单"))

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
