"""多设备改造 P0 用例（多设备同步改造方案.md §7 的 M1/M2/M8）。

修的是两条失效：
  M1：一台栖弦手机"没采纳"的新增，被另一台手机判成"已删除"（真丢数据）。
      根因不是身份没拆开，而是删除判据 = base_served − cur 对"整段覆盖"客户端不成立；
      规则 R1 改为 owned = base_served ∩ base_submitted（自己提交过才算拥有）⇒ 修掉。
  M2：一台设备的确认替另一台确认 ⇒ 墓碑被提前 GC ⇒ 删除被推回（S15 在多设备下复发）。
      身份拆开后每台设备各自 ack，M2 自然消失——本用例断言其不复发。
  M8：老空间（client key = `账号:方言`）加载后，无设备标识请求仍映射到旧 key，
      行为与升级前完全一致（§5 别名继承）。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "src"))

from engine.engine import SyncSpace  # noqa: E402
from hub.store import SpaceRuntime   # noqa: E402


def sub(pairs, name="P", nid="lx-1"):
    return {"playlists": [{"native_id": nid, "name": name,
                           "tracks": [{"source": s, "songId": i} for s, i in pairs]}]}


def canon_keys(sp, client):
    view, _ = sp.render_for(sp.clients[client])
    out = set()
    for e in view.values():
        out |= set(e["tracks"])
    return out


def canon_playlists(sp, client):
    view, _ = sp.render_for(sp.clients[client])
    return set(view.keys())


class MultiDeviceEngineTest(unittest.TestCase):

    def _space(self):
        sp = SyncSpace(f"space:multidevice-{os.getpid()}-{id(self)}")
        # 第四轮 P0-C：测试默认关闭冷静期（恢复/GC 语义立即生效）；专项测试单独设大值
        sp.restore_grace_seconds = 0
        return sp

    def _dev(self, sp, did, dialect="cyshine-v1"):
        return sp.register_client(f"acct:{dialect}:{did}", dialect=dialect,
                                  identity_verified=True, can_delete=True)

    # ---- M1-a：曲目级。B 加歌 s，A stale 整段 PUT（没有 s）。
    #      第三轮拍板（同空间成员互信）：s ∉ owned(A) 的缺席 = 缺席即删（直删写墓碑）；
    #      B 侧残留由恢复机制兜底（看过删除结果后重加 = 显式恢复，见 P0-C 用例）----
    def test_m1a_stale_put_does_not_delete_others_track(self):
        sp = self._space()
        A = self._dev(sp, "d-a")
        B = self._dev(sp, "d-b")
        self.assertEqual(sp.merge(A.client_id, sub([("tx", "t1")])).status, 201)
        self.assertEqual(sp.merge(B.client_id, sub([("tx", "t1")])).status, 201)

        # B 加歌 s
        sp.merge(B.client_id, sub([("tx", "t1"), ("tx", "s")]))
        self.assertIn("tx:s", canon_keys(sp, A.client_id))

        # A 的同步轮先 GET（交付视图已含 s）
        sp.deliver(A.client_id)
        self.assertIn("tx:s", {k for e in A.base_served.view.values() for k in e["tracks"]})
        self.assertNotIn("tx:s", {k for e in A.base_submitted.values() for k in e["tracks"]})

        # A stale PUT：本地 section LWW 保留自己的旧段（没有 s）
        rev_before = sp.revision
        r = sp.merge(A.client_id, sub([("tx", "t1")]))
        # 第四轮：s ∉ owned(A) ⇒ never_owned 缺席即删（同空间成员互信，无确认卡）
        flat_removed = [k for ks in r.meta["removed_tracks"].values() for k in ks]
        self.assertIn("tx:s", flat_removed)
        self.assertNotIn("tx:s", canon_keys(sp, A.client_id))   # 已删除
        self.assertIn("tx:s", sp.tombstones)                    # 写墓碑
        self.assertGreater(r.meta["revision"], rev_before)      # 删除推进 revision

    # ---- M1-b：歌单级。B 新建歌单，A 的 stale 段没有它 ⇒ 缺席即删（同空间互信） ----
    def test_m1b_stale_put_does_not_delete_others_playlist(self):
        sp = self._space()
        A = self._dev(sp, "d-a")
        B = self._dev(sp, "d-b")
        sp.merge(A.client_id, sub([("tx", "t1")], nid="p1", name="P1"))
        sp.merge(B.client_id, sub([("tx", "t1")], nid="p1", name="P1"))

        # B 新建歌单 p2
        r = sp.merge(B.client_id, {
            "playlists": [
                {"native_id": "p1", "name": "P1", "tracks": [{"source": "tx", "songId": "t1"}]},
                {"native_id": "p2", "name": "P2", "tracks": [{"source": "tx", "songId": "t2"}]},
            ]})
        self.assertEqual(len(canon_playlists(sp, A.client_id)), 2)   # B 加完 = 2 张

        sp.deliver(A.client_id)   # A 交付视图已含 p2
        # A 的 stale 整段只有 p1 ⇒ p2 ∉ owned(A) ⇒ 缺席即删（直删写墓碑）
        r = sp.merge(A.client_id, sub([("tx", "t1")], nid="p1", name="P1"))
        self.assertEqual(len(r.meta["removed_playlists"]), 1)
        removed_pid = r.meta["removed_playlists"][0]
        self.assertEqual(sp.playlists[removed_pid].name, "P2")   # 删的是 B 新建的 p2
        self.assertEqual(len(canon_playlists(sp, A.client_id)), 1)   # p2 已删
        self.assertIn(removed_pid, sp.tombstones)

    # ---- M1-c：A 确实删掉自己加过的歌 ⇒ 照常删除（R1 不削弱 D22） ----
    def test_m1c_real_delete_still_works(self):
        sp = self._space()
        A = self._dev(sp, "d-a")
        B = self._dev(sp, "d-b")
        sp.merge(A.client_id, sub([("tx", "t1")]))
        sp.merge(B.client_id, sub([("tx", "t1")]))
        # A 加 t2（自己提交过），并完成一轮 GET（真实同步流程：GET→PUT）
        sp.merge(A.client_id, sub([("tx", "t1"), ("tx", "t2")]))
        sp.deliver(A.client_id)

        # A 删 t2：owned(A) = base_served ∩ base_submitted 含 t2 ⇒ 照常删除
        r = sp.merge(A.client_id, sub([("tx", "t1")]))
        removed = {k for v in r.meta["removed_tracks"].values() for k in v}
        self.assertIn("tx:t2", removed)
        self.assertNotIn("tx:t2", canon_keys(sp, A.client_id))
        self.assertIn("tx:t2", sp.tombstones)

    # ---- M2：一台设备确认删除、另一台未采纳 ⇒ 墓碑不被提前 GC（删除不推回） ----
    def test_m2_tombstone_not_gced_before_other_device_sees_it(self):
        sp = self._space()
        A = self._dev(sp, "d-a")
        B = self._dev(sp, "d-b")
        sp.merge(A.client_id, sub([("tx", "t1")]))
        sp.merge(B.client_id, sub([("tx", "t1")]))
        sp.deliver(A.client_id)   # A 完成一轮 GET（真实同步流程）

        # A 删自己提交过的 t1（R1 照常判删）
        sp.merge(A.client_id, sub([]))
        self.assertIn("tx:t1", sp.tombstones)

        # A 自己 GET：仅推进交付基线；B 尚未提交确认 ⇒ 墓碑必须还在
        # （第三轮修复：GET 不代表应用删除，删除确认一律来自提交内容）
        sp.deliver(A.client_id)
        self.assertIn("tx:t1", sp.tombstones)

        # B 提交确认应用该删除（提交视图不再携带 t1）⇒ 确认齐 ⇒ 墓碑才回收
        sp.merge(B.client_id, sub([]))
        self.assertNotIn("tx:t1", sp.tombstones)

    # ---- M8：老空间 key 兼容。无标识请求沿用旧 key，带标识才新建独立 client ----
    def test_m8_legacy_client_key_compat(self):
        rt = SpaceRuntime(f"space:m8-{os.getpid()}-{id(self)}")
        # 模拟老 pkl：直接注册 `账号:方言` 形式的 client（升级前唯一的身份）
        rt.engine.register_client("acct:cyshine-v1", dialect="cyshine-v1",
                                  identity_verified=True, can_delete=True)
        # 无设备标识 ⇒ 沿用旧 key（行为与升级前完全一致，删除判据的基线原样保留）
        self.assertEqual(rt.client_for("acct", "cyshine-v1"), "acct:cyshine-v1")
        # 带设备标识 ⇒ 新建独立 client，且不与旧 client 撞车
        cid = rt.client_for("acct", "cyshine-v1", "phone-a")
        self.assertEqual(cid, "acct:cyshine-v1:phone-a")
        self.assertNotEqual(cid, "acct:cyshine-v1")
        # 另一台设备也是独立 client
        self.assertEqual(rt.client_for("acct", "cyshine-v1", "phone-b"),
                         "acct:cyshine-v1:phone-b")

    # ---- M8b：全新空间（无老 key）无标识请求生成 `:default` 且可正常合并 ----
    def test_m8b_fresh_space_default_device(self):
        rt = SpaceRuntime(f"space:m8b-{os.getpid()}-{id(self)}")
        cid = rt.client_for("acct", "ceru-plugin")
        self.assertEqual(cid, "acct:ceru-plugin:default")
        # default 设备能正常建基线（只增不删）
        r = rt.engine.merge(cid, sub([("tx", "t1")]))
        self.assertIn(r.status, (200, 201))

    # ---- P0.5：R1 保护掉的"从未提交过的缺席" → 第三轮修改：同空间成员设备互信，
    #      不再出确认卡，直接删除写墓碑；澜音（can_delete=False）推回仍被墓碑压制 ----
    def test_never_owned_absence_directly_deletes(self):
        sp = self._space()
        A = self._dev(sp, "d-a")                       # 栖弦（can_delete）
        CE = sp.register_client("acct:ceru-plugin:default", dialect="ceru-plugin",
                                identity_verified=True, can_delete=False)
        sp.merge(A.client_id, sub([("tx", "1")], nid="p1", name="栖弦单"))
        sp.deliver(A.client_id)
        sp.merge(CE.client_id, sub([("tx", "1")], nid="p1", name="栖弦单"))
        # 澜音新建 p2（wy:7）
        sp.merge(CE.client_id, {"playlists": [
            {"native_id": "p1", "name": "栖弦单", "tracks": [{"source": "tx", "songId": "1"}]},
            {"native_id": "p2", "name": "澜音单", "tracks": [{"source": "wy", "songId": "7"}]}]})
        p2 = next(pid for pid, pl in sp.playlists.items() if pl.name == "澜音单")
        # 栖弦 GET：base_served 含 p2、base_submitted 不含（采纳后未回提）
        sp.deliver(A.client_id)
        self.assertIn(p2, A.base_served.view)
        self.assertNotIn(p2, A.base_submitted)
        rev = sp.revision
        # 栖弦删 p2（从未提交过）→ 直接删除写墓碑（不再出确认卡，同空间成员互信）
        r = sp.merge(A.client_id, sub([("tx", "1")], nid="p1", name="栖弦单"))
        self.assertIn(p2, r.meta["removed_playlists"])
        self.assertIn(p2, r.meta["never_owned"]["playlists"])
        self.assertNotIn(p2, canon_playlists(sp, A.client_id))
        self.assertIn(p2, sp.tombstones)
        self.assertFalse(sp.pending_deletions)         # 无确认卡
        self.assertGreater(sp.revision, rev)          # 删除已生效
        # 澜音（本地删不掉）推回 p2 → 墓碑压制，不复活
        # （第四轮 P1-1：默认无待确认恢复卡，抑制信息由 meta.suppressed_playlists 承载）
        r2 = sp.merge(CE.client_id, {"playlists": [
            {"native_id": "p1", "name": "栖弦单", "tracks": [{"source": "tx", "songId": "1"}]},
            {"native_id": "p2", "name": "澜音单", "tracks": [{"source": "wy", "songId": "7"}]}]})
        self.assertTrue(r2.meta["suppressed_playlists"])
        self.assertNotIn(p2, canon_playlists(sp, A.client_id))
        self.assertNotIn(p2, sp.pending_restores)

    # ---- P0.5 补充：I2′ 并发轮次的 never_owned 只并入 suspects、不出卡（防噪音） ----
    def test_never_owned_under_revision_gate_only_suspects(self):
        sp = self._space()
        A = self._dev(sp, "d-a")
        B = self._dev(sp, "d-b")
        sp.merge(A.client_id, sub([("tx", "x")]))
        sp.deliver(A.client_id)                      # A.GET1 → served=rev1（视图只有 x）
        sp.merge(B.client_id, sub([("tx", "x"), ("wy", "y")]))  # B 加 y → rev2
        sp.deliver(B.client_id)
        sp.deliver(A.client_id)                      # A.GET2 → 看到 y（base_served 含 y），served=rev2
        sp.merge(B.client_id, sub([("tx", "x"), ("wy", "y"), ("kw", "z")]))  # B 又加 z → rev3
        sp.deliver(B.client_id)
        # A 拿旧视图回写（无 y,z）：I2′ 触发（rev3 != served2）；y/z ∉ owned(A) = never_owned
        # → 只并入 suspects，不出卡
        r = sp.merge(A.client_id, sub([("tx", "x")]))
        self.assertTrue(r.meta["suspects"])
        flat = [k for ks in r.meta["suspects"].values() if isinstance(ks, list) for k in ks]
        self.assertIn("wy:y", flat)
        self.assertFalse(sp.pending_deletions)       # 不出卡
        self.assertIn("wy:y", canon_keys(sp, A.client_id))   # 不删

    # ---- 第四轮 P0-C：同空间成员互信，B 直接删除（无确认卡）后，A（曾提交者）
    #      GET 跨过删除点 + 越过冷静期 → 本地残留回推 = 显式恢复（不再限 origin_client）
    #      ——"看过删除结果 + 冷静期已过"视为用户意图恢复，直接生效清墓碑 ----
    def test_other_device_residue_after_confirm_delete_restores(self):
        sp = self._space()
        A = self._dev(sp, "d-a")                      # 栖弦（can_delete=True）
        B = self._dev(sp, "d-b")
        # A 新增歌单 p1 + x 并提交（A 是"曾经拥有者"）
        sp.merge(A.client_id, sub([("tx", "x")], nid="p1", name="歌单"))
        sp.deliver(A.client_id)
        # B 建基线（提交另一个歌单 p2），随后看到 p1 但从未提交过它 → 提交缺 p1 = 直接删
        sp.merge(B.client_id, sub([("kw", "0")], nid="p2", name="其他"))
        sp.deliver(B.client_id)
        pl_id = next(k for k, v in B.base_served.view.items() if v["name"] == "歌单")
        r = sp.merge(B.client_id, sub([("kw", "0")], nid="p2", name="其他"))
        self.assertIn(pl_id, r.meta["removed_playlists"])
        self.assertFalse(sp.pending_deletions)        # 无确认卡
        self.assertIn(pl_id, sp.tombstones)           # 直接写墓碑
        # A GET 跨过删除点（base_served 推进到删除后 revision）
        sp.deliver(A.client_id)
        # A 本地残留回推（提交视图仍带 p1+x）→ 第四轮 P0-C：看过删除结果 + 冷静期已过
        # （测试 grace=0）→ 显式恢复，直接生效清墓碑
        r2 = sp.merge(A.client_id, sub([("tx", "x")], nid="p1", name="歌单"))
        self.assertIn(pl_id, canon_playlists(sp, A.client_id))
        self.assertNotIn(pl_id, sp.tombstones)
        self.assertNotIn(pl_id, sp.pending_restores)   # 无待确认恢复卡

    # ---- 第四轮 P0-C：没看过删除结果的残留回推（stale 客户端）仍被压制 ----
    def test_stale_client_readd_still_suppressed(self):
        sp = self._space()
        A = self._dev(sp, "d-a")
        B = self._dev(sp, "d-b")
        sp.merge(A.client_id, sub([("tx", "x")], nid="p1", name="歌单"))
        sp.deliver(A.client_id)                       # A GET1 → served=rev1
        sp.merge(B.client_id, sub([("tx", "x")], nid="p1", name="歌单"))  # B 也提交过（rev2）
        # B 未 deliver → base_served None（从未看过删除结果）
        sp.deliver(A.client_id)                       # A GET2 → served=rev2（对齐，I2′ 放行）
        sp.merge(A.client_id, sub([], nid="p2", name="空"))  # A 删 p1（owned 直删）→ rev3
        pl_id = next(k for k, v in sp.playlists.items() if v.name == "歌单")
        self.assertIn(pl_id, sp.tombstones)
        # B 拿删除前的旧视图回推（从未看过删除结果）→ 压制、不复活
        rb = sp.merge(B.client_id, sub([("tx", "x")], nid="p1", name="歌单"))
        self.assertIn(pl_id, rb.meta["suppressed_playlists"])
        self.assertNotIn(pl_id, canon_playlists(sp, A.client_id))
        self.assertIn(pl_id, sp.tombstones)

    # ---- 第四轮 P0-A：交付 stamp 必须严格大于客户端刚提交的 modifiedAt ----
    def test_stamp_beats_submitted_modified_at(self):
        sp = self._space()
        A = self._dev(sp, "d-a")
        B = self._dev(sp, "d-b")
        sp.merge(A.client_id, sub([("tx", "1")]))
        sp.deliver(A.client_id)
        # B 提交时声明一个很新的 modifiedAt（模拟客户端本地时钟超前 → 根因 A）
        sp.merge(B.client_id, sub([("tx", "1")]),
                 client_modified_at="2030-01-01T00:00:00.000Z")
        # B 的交付 stamp 必须严格大于其刚提交的 modifiedAt（毫秒单调压过）
        st = sp.deliver(B.client_id).stamp
        self.assertGreater(st, "2030-01-01T00:00:00.000Z")  # 同格式固定宽度，字典序=时间序
        # 内容不变时 stamp 稳定（字节全同，幂等交付）
        self.assertEqual(st, sp.deliver(B.client_id).stamp)

    # ---- 第四轮 P0-C：冷静期内重加（即使看过删除结果）仍按残留压制 ----
    # 注意 P0-B：墓碑 GC = 全员看过删除视图。需一个从未看过的客户端钉住墓碑，
    # 否则 A 看过即触发 GC、重加无墓碑可压（单设备"删了又加"本就等价于恢复）。
    def test_grace_period_suppresses_immediate_readd(self):
        sp = self._space()
        sp.restore_grace_seconds = 3600   # 冷静期 1 小时：age≈0 必触发压制
        A = self._dev(sp, "d-a")
        C = self._dev(sp, "d-c")
        sp.merge(A.client_id, sub([("tx", "1")]))
        sp.deliver(A.client_id)
        sp.merge(C.client_id, sub([("tx", "1")]))   # C 也拥有 p1，之后不 GET → 未 ack → 墓碑保留
        sp.merge(A.client_id, sub([]))              # A 删除（无歌单视图 → 曲目级缺席即删）
        sp.deliver(A.client_id)                     # A 看过删除结果（C 未确认 → 不 GC）
        r = sp.merge(A.client_id, sub([("tx", "1")]))  # 冷静期内重加 → 曲目被压制
        self.assertIn("tx:1", r.meta["suppressed_tracks"])
        self.assertNotIn("tx:1", canon_keys(sp, A.client_id))   # 曲目不复活
        # 越过冷静期（把墓碑删除时间改老模拟时间流逝）→ 显式恢复直接生效
        for t in sp.tombstones.values():
            t.deleted_at = "2020-01-01T00:00:00.000Z"
        sp.merge(A.client_id, sub([("tx", "1")]))
        self.assertIn("tx:1", canon_keys(sp, A.client_id))
        self.assertNotIn("tx:1", sp.tombstones)

    # ---- 第四轮 P1-3：澜音（can_delete=False）的"缺席"只记录、不写删除 ----
    def test_never_owned_not_deleted_when_can_delete_false(self):
        sp = self._space()
        A = self._dev(sp, "d-a")
        CE = sp.register_client("acct:ceru-plugin:default", dialect="ceru-plugin",
                                identity_verified=True, can_delete=False)
        sp.merge(A.client_id, sub([("tx", "1")], nid="p1", name="栖弦单"))
        sp.deliver(A.client_id)
        sp.merge(CE.client_id, {"playlists": [
            {"native_id": "p2", "name": "澜音单", "tracks": [{"source": "wy", "songId": "7"}]}]})
        # 澜音 GET：看到 p1（base_served 有 p1）但从未提交过（base_submitted 无 p1）
        sp.deliver(CE.client_id)
        pl1 = next(k for k, v in sp.playlists.items() if v.name == "栖弦单")
        self.assertIn(pl1, CE.base_served.view)
        self.assertNotIn(pl1, CE.base_submitted)
        # 澜音提交缺 p1 → 第四轮 P1-3：不写删除、不写墓碑（无删除能力 = 被迫省略）
        r = sp.merge(CE.client_id, {"playlists": [
            {"native_id": "p2", "name": "澜音单", "tracks": [{"source": "wy", "songId": "7"}]}]})
        self.assertIn(pl1, r.meta["never_owned"]["playlists"])
        self.assertFalse(r.meta["never_owned_applied"])
        self.assertNotIn(pl1, sp.tombstones)
        self.assertIn(pl1, canon_playlists(sp, A.client_id))   # p1 仍存活

    # ---- 第四轮 P0-C：看过删除结果 + 越过冷静期的重加 = 显式恢复（无恢复卡） ----
    def test_seen_client_readd_restores_without_pending(self):
        sp = self._space()
        A = self._dev(sp, "d-a")
        B = self._dev(sp, "d-b")
        sp.merge(A.client_id, sub([("tx", "1")]))
        sp.deliver(A.client_id)
        sp.merge(B.client_id, sub([("tx", "1")]))
        sp.deliver(B.client_id)
        sp.merge(A.client_id, sub([]))     # A 删除
        sp.deliver(B.client_id)            # B 看过删除结果（grace=0 → 越冷静期）
        r = sp.merge(B.client_id, sub([("tx", "1")]))  # B 重加 → 显式恢复
        self.assertNotIn("tx:1", r.meta.get("suppressed_tracks", []))
        self.assertIn("tx:1", canon_keys(sp, B.client_id))
        self.assertNotIn("tx:1", sp.tombstones)
        self.assertNotIn("tx:1", sp.pending_restores)   # 无待确认恢复卡

    # ---- 第四轮 P1-2：untrusted（无删除能力）端点批量删除仍走安全阀挂起 ----
    def test_untrusted_bulk_delete_still_deferred(self):
        sp = self._space()
        sp.restore_grace_seconds = 0
        L = sp.register_client("acct:ceru-plugin:default", dialect="ceru-plugin",
                               identity_verified=True, can_delete=False)
        sp.merge(L.client_id, {"playlists": [
            {"native_id": "p1", "name": "歌单",
             "tracks": [{"source": "tx", "songId": f"{i}"} for i in range(12)]}]})
        sp.deliver(L.client_id)
        # 澜音"删 11 首"（它提交过这些曲目 → owned；但无删除能力 → 不可信）
        r = sp.merge(L.client_id, {"playlists": [
            {"native_id": "p1", "name": "歌单",
             "tracks": [{"source": "tx", "songId": "0"}]}]})
        self.assertTrue(r.meta["deferred"])           # 仍挂起安全阀
        self.assertTrue(sp.pending_deletions)
        self.assertEqual(len(sp.tombstones), 0)       # 未写墓碑


if __name__ == "__main__":
    unittest.main()
