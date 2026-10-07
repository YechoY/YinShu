# -*- coding: utf-8 -*-
"""P0.5 补丁：test_multidevice.py 追加 never_owned 用例（一次性补丁脚本，保留于 tmp/ 备查）。"""
import io

p = r"C:\Users\Echo\Desktop\Workspace\Music\playlist-sync-hub\tests\engine\test_multidevice.py"
s = io.open(p, encoding="utf-8").read()

anchor = """        r = rt.engine.merge(cid, sub([("tx", "t1")]))
        self.assertIn(r.status, (200, 201))
"""
assert s.count(anchor) == 1, "anchor count=%d" % s.count(anchor)

insert = anchor + """
    # ---- P0.5：R1 保护掉的"从未提交过的缺席" → 出待确认删除卡，默认不删；
    #      确认后真删 + 写墓碑 → 墓碑压制澜音（can_delete=False）推回 ----
    def test_never_owned_absence_generates_pending_card(self):
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
        # 栖弦删 p2（从未提交过）→ 不自动删 + 出 never_owned 卡 + revision 不动
        r = sp.merge(A.client_id, sub([("tx", "1")], nid="p1", name="栖弦单"))
        self.assertEqual(r.meta["removed_playlists"], [])
        self.assertEqual(sp.revision, rev)
        self.assertIn(p2, r.meta["never_owned"]["playlists"])
        self.assertTrue(sp.pending_deletions)
        p = next(iter(sp.pending_deletions.values()))
        self.assertEqual(p["reason"], "never_owned")
        self.assertIn(p2, canon_playlists(sp, A.client_id))     # 默认不删
        # 确认 → 真删 + 墓碑
        pid = next(iter(sp.pending_deletions))
        sp.confirm_delete(pid)
        self.assertNotIn(p2, canon_playlists(sp, A.client_id))
        self.assertIn(p2, sp.tombstones)
        # 澜音（本地删不掉）推回 p2 → 墓碑压制，不复活，进待确认恢复
        r2 = sp.merge(CE.client_id, {"playlists": [
            {"native_id": "p1", "name": "栖弦单", "tracks": [{"source": "tx", "songId": "1"}]},
            {"native_id": "p2", "name": "澜音单", "tracks": [{"source": "wy", "songId": "7"}]}]})
        self.assertTrue(r2.meta["suppressed_playlists"])
        self.assertNotIn(p2, canon_playlists(sp, A.client_id))
        self.assertIn(p2, sp.pending_restores)

    # ---- P0.5 补充：I2′ 并发轮次的 never_owned 只并入 suspects、不出卡（防噪音） ----
    def test_never_owned_under_revision_gate_only_suspects(self):
        sp = self._space()
        A = self._dev(sp, "d-a")
        B = self._dev(sp, "d-b")
        sp.merge(A.client_id, sub([("tx", "x")]))
        sp.deliver(A.client_id)                      # A.base_served = rev1
        sp.merge(B.client_id, sub([("tx", "x"), ("wy", "y")]))  # B 加 y → rev2
        sp.deliver(B.client_id)
        # A 拿旧视图回写（不含 y）：y 是 never_owned 缺席，但 I2′ 触发 → 只并入 suspects，不出卡
        r = sp.merge(A.client_id, sub([("tx", "x")]))
        self.assertTrue(r.meta["suspects"])
        flat = [k for ks in r.meta["suspects"].values() if isinstance(ks, list) for k in ks]
        self.assertIn("wy:y", flat)
        self.assertFalse(sp.pending_deletions)       # 不出卡
        self.assertIn("wy:y", canon_keys(sp, A.client_id))   # 不删
"""

s = s.replace(anchor, insert, 1)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("OK: never_owned tests appended")
