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
from hub import adapters  # noqa: E402,F401  给 SyncSpace.KNOWN_DIALECTS 注入全部方言
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
    #      第七轮拍板（真机事故后收紧）：s ∉ owned(A) ⇒ **永不判删**，只记 meta.never_owned。
    #      理由：只有"自己提交过"的缺席才表达删除意图；"交付过、但从未提交过"只说明这台设备
    #      看不到/放不进它（导入失败、能力受限）——把它当删除会误删别人刚加的歌
    #      （第七轮真机事故：「Yes」里栖弦新加的 5 首被澜音设备一轮 PUT 带走）。----
    # ---- 用户要求（2026-10-08 m04142）：「还要无论什么平台，新增的歌曲都要在最顶部」。
    #      三种方言（栖弦/澜音/洛雪）各加一首，每次都必须落在最顶，老歌相对顺序不变；
    #      且客户端看不到别人新加的歌时**不得**把它判成删除（R1）。----
    def test_new_tracks_go_to_head_from_every_dialect(self):
        sp = self._space()
        A = self._dev(sp, "d-a", "cyshine-v1")
        B = self._dev(sp, "d-b", "ceru-plugin")
        C = self._dev(sp, "d-c", "lx-x")

        def order():
            view, _ = sp.render_for(sp.clients[A.client_id])
            return list(list(view.values())[0]["tracks"])

        sp.merge(A.client_id, sub([("tx", "t1"), ("tx", "t2")]))
        self.assertEqual(order(), ["tx:t1", "tx:t2"])
        # 栖弦端加 kw:k1：真实客户端把它追加在自己视图末尾 → 枢纽交付必须置顶
        sp.merge(B.client_id, sub([("tx", "t1"), ("tx", "t2"), ("kw", "k1")]))
        self.assertEqual(order(), ["kw:k1", "tx:t1", "tx:t2"])
        # 澜音端加 wy:w1（它看不到 kw:k1 —— R1 保证不判删）
        r = sp.merge(C.client_id, sub([("tx", "t1"), ("tx", "t2"), ("wy", "w1")]))
        self.assertEqual(order(), ["wy:w1", "kw:k1", "tx:t1", "tx:t2"])
        self.assertNotIn("kw:k1",
                         [k for ks in r.meta["removed_tracks"].values() for k in ks])
        # 洛雪端再加 kg:g1（此时它有基线，走常规合并路径）
        r2 = sp.merge(C.client_id, sub([("tx", "t1"), ("tx", "t2"), ("wy", "w1"), ("kg", "g1")]))
        self.assertEqual(order(), ["kg:g1", "wy:w1", "kw:k1", "tx:t1", "tx:t2"])
        self.assertNotIn("kw:k1",
                         [k for ks in r2.meta["removed_tracks"].values() for k in ks])
        # 集合不全的提交（这台设备看不到别人新加的歌）不得整体重排，否则会打乱别人的顺序
        sp.merge(C.client_id, sub([("tx", "t2"), ("tx", "t1"), ("wy", "w1"), ("kg", "g1")]))
        self.assertEqual(order(), ["kg:g1", "wy:w1", "kw:k1", "tx:t1", "tx:t2"])
        # 交付一轮拿到完整集合后，纯重排整体采用提交顺序（P1-9：顺序也是同步内容）
        sp.deliver(C.client_id)
        sp.merge(C.client_id, sub([("tx", "t2"), ("tx", "t1"), ("wy", "w1"), ("kg", "g1"),
                                   ("kw", "k1")]))
        self.assertEqual(order(), ["tx:t2", "tx:t1", "wy:w1", "kg:g1", "kw:k1"])

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
        r = sp.merge(A.client_id, sub([("tx", "t1")]))
        # 第七轮：s ∉ owned(A) ⇒ 不判删、不写墓碑、只记 never_owned
        flat_removed = [k for ks in r.meta["removed_tracks"].values() for k in ks]
        self.assertNotIn("tx:s", flat_removed)
        flat_never = [k for ks in r.meta["never_owned"]["tracks"].values() for k in ks]
        self.assertIn("tx:s", flat_never)
        self.assertIn("tx:s", canon_keys(sp, A.client_id))      # 仍在
        self.assertNotIn("tx:s", sp.tombstones)                 # 不写墓碑

    # ---- M1-b：歌单级。B 新建歌单，A 的 stale 段没有它。
    #      第十三轮（2026-10-08 真机：lx 加的歌单在栖弦里删掉却同步不上去）拍板 D32：
    #      栖弦的 `sections.playlists` 是**整份**导出（`exportForSync` 遍历本地全部歌单、无过滤）
    #      且整份 LWW ⇒ "交付过、这次没提交"对歌单就等于"这台设备上它没了"；而歌单的新增
    #      **不会**进 base_submitted（栖弦远端胜出那轮 applyFromSync 整份替换本地，`_syncOnce`
    #      见 merged == remote 直接 return、根本不上传）⇒ 旧口径下这种缺席永远落在 never_owned，
    #      歌单永远删不掉。故歌单级再放松一档给**可信客户端**（身份已验证 + can_delete + 未退休，
    #      与安全阀 trusted 同判据；澜音 can_delete=False 天然排除）：
    #      `owned_pl = base_served`，缺席即删除（写歌单墓碑；按 D28 不派生曲目墓碑）。
    #      曲目级 owned_t 不动（真机数据损失都发生在曲目层，见 M1-a）。
    def test_m1b_trusted_stale_put_deletes_others_playlist(self):
        sp = self._space()
        A = self._dev(sp, "d-a")
        B = self._dev(sp, "d-b")
        sp.merge(A.client_id, sub([("tx", "t1")], nid="p1", name="P1"))
        sp.merge(B.client_id, sub([("tx", "t1")], nid="p1", name="P1"))

        # B 新建歌单 p2
        sp.merge(B.client_id, {
            "playlists": [
                {"native_id": "p1", "name": "P1", "tracks": [{"source": "tx", "songId": "t1"}]},
                {"native_id": "p2", "name": "P2", "tracks": [{"source": "tx", "songId": "t2"}]},
            ]})
        p2 = next(pid for pid, pl in sp.playlists.items() if pl.name == "P2")
        self.assertEqual(len(canon_playlists(sp, A.client_id)), 2)   # B 加完 = 2 张

        sp.deliver(A.client_id)   # A 交付视图已含 p2，但 base_submitted 里没有它
        self.assertIn(p2, A.base_served.view)
        self.assertNotIn(p2, A.base_submitted)

        # A 的 stale 整段只有 p1 ⇒ 交付过、这次没有 ⇒ D32：判删（不再落 never_owned）
        r = sp.merge(A.client_id, sub([("tx", "t1")], nid="p1", name="P1"))
        self.assertIn(p2, r.meta["removed_playlists"])
        self.assertEqual(r.meta["never_owned"]["playlists"], [])
        self.assertIn(p2, sp.tombstones)                             # 写歌单墓碑
        self.assertNotIn(p2, canon_playlists(sp, B.client_id))       # 别端视图里也没了
        self.assertNotIn("tx:t2", sp.tombstones)                     # D28：不派生曲目墓碑

    # ---- D32 配套：栖弦删掉的歌单，原创建者（洛雪，本地还留着）回推时不得复活 ----
    def test_d32_deleted_playlist_stays_deleted_against_creator_readd(self):
        sp = self._space()
        sp.restore_grace_seconds = 3600      # 同步循环几秒一轮：远未越过冷静期
        A = self._dev(sp, "d-a")             # 栖弦：在本地删掉那张歌单
        L = self._lx(sp, "d-l")              # 洛雪：歌单的创建者，本地副本还带着它
        L2 = {
            "playlists": [
                {"native_id": "p1", "name": "P1", "tracks": [{"source": "tx", "songId": "t1"}]},
                {"native_id": "p2", "name": "P2", "tracks": [{"source": "tx", "songId": "t2"}]},
            ]}
        sp.merge(A.client_id, sub([("tx", "t1")], nid="p1", name="P1"))
        sp.merge(L.client_id, sub([("tx", "t1")], nid="p1", name="P1"))
        sp.merge(L.client_id, L2)                       # 洛雪新建 p2
        p2 = next(pid for pid, pl in sp.playlists.items() if pl.name == "P2")

        sp.deliver(A.client_id)                         # 栖弦看到 p2（但从未提交过它）
        self.assertIn(p2, A.base_served.view)
        r = sp.merge(A.client_id, sub([("tx", "t1")], nid="p1", name="P1"))
        self.assertIn(p2, r.meta["removed_playlists"])  # D32：交付过 + 可信 ⇒ 判删
        self.assertIn(p2, sp.tombstones)
        self.assertNotIn(p2, canon_playlists(sp, L.client_id))

        # 洛雪本地还留着这张歌单 → 回推：冷静期内被墓碑压制，不复活
        r2 = sp.merge(L.client_id, L2)
        self.assertIn(p2, r2.meta["suppressed_playlists"])
        self.assertNotIn(p2, canon_playlists(sp, A.client_id))
        self.assertIn(p2, sp.tombstones)
        # 洛雪这次提交里带着它 ⇒ 不算"看过即采纳"：计入 carried，墓碑不 GC（D21 后半句 + 第十二轮）
        self.assertIn(L.client_id, sp.tombstones[p2].carried)

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

    # ---- 第十一轮（2026-10-08 用户真机）：洛雪拉下来的歌，在洛雪里删掉却同步不上去 ----
    #      用户原话（m04646）：先在栖弦往 Yes 加一首 → lx 拉下来（正常）→ 在 lx 删掉 →
    #      同步无效。根因：lm 只在"自己提交过"（base_served ∩ base_submitted）时才判删，
    #      而 lx 拉取（GET）后从不上报，这首它从未提交过 ⇒ 缺席落进 never_owned，永不判删。
    #      lx 是"整份替换"客户端（overwriteListFull 后 PUT 内容 = 远端视图 + 本地操作重放），
    #      故对它放松 R1：owned = base_served 即可（见 Client.full_view_submit）。
    def _lx(self, sp, did):
        """洛雪客户端：整份替换 + 只对白名单音源判删（与 hub.store.client_for 一致）。"""
        return sp.register_client(
            f"acct:lx-x:{did}", dialect="lx-x", identity_verified=True, can_delete=True,
            full_view_submit=True,
            round_trip_sources=frozenset({"wy", "tx", "kw", "kg", "mg"}))

    def test_lx_pull_then_delete_propagates(self):
        sp = self._space()
        A = self._dev(sp, "d-a", "cyshine-v1")     # 栖弦：加歌方
        L = self._lx(sp, "d-l")                    # 洛雪：拉下来再删

        sp.merge(A.client_id, sub([("tx", "t1")]))
        sp.merge(L.client_id, sub([("tx", "t1")]))          # L 有基线
        # 栖弦在 Yes 里加一首（真机是 kw:165923393），并让 L 拉到
        sp.merge(A.client_id, sub([("tx", "t1"), ("kw", "k9")]))
        sp.deliver(L.client_id)
        served = {k for e in L.base_served.view.values() for k in e["tracks"]}
        self.assertIn("kw:k9", served)                      # 交付过
        self.assertNotIn("kw:k9", {k for e in L.base_submitted.values()
                                   for k in e["tracks"]})   # 从未提交过（旧口径就卡在这）

        # 用户在洛雪里删掉它 → 整份 PUT 里没有 kw:k9
        r = sp.merge(L.client_id, sub([("tx", "t1")]))
        removed = {k for v in r.meta["removed_tracks"].values() for k in v}
        self.assertIn("kw:k9", removed)                     # 放松后判删
        self.assertIn("kw:k9", sp.tombstones)
        self.assertNotIn("kw:k9", canon_keys(sp, A.client_id))   # 别端视图也没了
        # 歌单级同样成立（整份替换 ⇒ 缺席的歌单就是删掉的歌单）
        self.assertNotIn("kw:k9", {k for e in r.meta["never_owned"]["tracks"].values()
                                   for k in e})

    def test_full_view_relaxation_does_not_leak_to_cyshine(self):
        """曲目级：栖弦是 section 级 LWW（可能保留自己的旧段）⇒ 必须继续走旧口径。
        （歌单级已在第十三轮 D32 放松给可信客户端，见 test_m1b_*。）"""
        sp = self._space()
        A = self._dev(sp, "d-a", "cyshine-v1")
        B = self._dev(sp, "d-b", "cyshine-v1")
        sp.merge(A.client_id, sub([("tx", "t1")]))
        sp.merge(B.client_id, sub([("tx", "t1")]))
        sp.merge(A.client_id, sub([("tx", "t1"), ("kw", "k9")]))
        sp.deliver(B.client_id)
        r = sp.merge(B.client_id, sub([("tx", "t1")]))
        self.assertEqual(r.meta["removed_tracks"], {})
        self.assertIn("kw:k9", {k for v in r.meta["never_owned"]["tracks"].values() for k in v})
        self.assertIn("kw:k9", canon_keys(sp, B.client_id))

    def test_lx_relaxation_skips_sources_it_cannot_round_trip(self):
        """洛雪解析把白名单外的音源丢进 opaque ⇒ 那些缺席不能当删除（宁可不删）。"""
        sp = self._space()
        A = self._dev(sp, "d-a", "cyshine-v1")
        L = self._lx(sp, "d-l")
        sp.merge(A.client_id, sub([("tx", "t1")]))
        sp.merge(L.client_id, sub([("tx", "t1")]))
        sp.merge(A.client_id, sub([("tx", "t1"), ("zz", "z1")]))   # zz 不在洛雪白名单
        sp.deliver(L.client_id)
        self.assertIn("zz:z1", {k for e in L.base_served.view.values() for k in e["tracks"]})

        r = sp.merge(L.client_id, sub([("tx", "t1")]))
        self.assertEqual(r.meta["removed_tracks"], {})
        self.assertIn("zz:z1", {k for v in r.meta["never_owned"]["tracks"].values() for k in v})
        self.assertNotIn("zz:z1", sp.tombstones)
        self.assertIn("zz:z1", canon_keys(sp, A.client_id))

    def test_lx_relaxation_still_gated_by_concurrency(self):
        """I2′：交付之后别端又写过 ⇒ 只记 suspects，不判删（旧视图不判删）。"""
        sp = self._space()
        A = self._dev(sp, "d-a", "cyshine-v1")
        L = self._lx(sp, "d-l")
        sp.merge(A.client_id, sub([("tx", "t1")]))
        sp.merge(L.client_id, sub([("tx", "t1")]))
        sp.merge(A.client_id, sub([("tx", "t1"), ("kw", "k9")]))
        sp.deliver(L.client_id)
        sp.merge(A.client_id, sub([("tx", "t1"), ("kw", "k9"), ("wy", "w1")]))  # 并发写

        r = sp.merge(L.client_id, sub([("tx", "t1")]))
        self.assertEqual(r.meta["removed_tracks"], {})
        self.assertIn("kw:k9", r.meta["suspects"].get(
            next(pid for pid, pl in sp.playlists.items() if pl.name == "P"), []))
        self.assertNotIn("kw:k9", sp.tombstones)
        self.assertIn("kw:k9", canon_keys(sp, L.client_id))

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

    # ---- P0.5 边界（第十三轮 D32 后重写）：歌单级放松的前提是"**交付过**"——
    #      base_served 里根本没有的条目（这台设备还没 GET 到）不构成删除意图：既不判删，
    #      也进不了 never_owned（它压根不在 base_keys 里），revision 不动。
    #      "交付过、但从未提交过"的缺席只对**不可信**客户端仍受保护
    #      （澜音 can_delete=False，见 test_never_owned_not_deleted_when_can_delete_false）。----
    def test_absence_without_delivery_is_ignored(self):
        sp = self._space()
        A = self._dev(sp, "d-a")                       # 栖弦（可信：can_delete=True）
        CE = sp.register_client("acct:ceru-plugin:default", dialect="ceru-plugin",
                                identity_verified=True, can_delete=False)
        sp.merge(A.client_id, sub([("tx", "1")], nid="p1", name="栖弦单"))
        sp.deliver(A.client_id)                        # A.GET1：只看到 p1
        sp.merge(CE.client_id, sub([("tx", "1")], nid="p1", name="栖弦单"))
        # 澜音新建 p2（wy:7）
        sp.merge(CE.client_id, {"playlists": [
            {"native_id": "p1", "name": "栖弦单", "tracks": [{"source": "tx", "songId": "1"}]},
            {"native_id": "p2", "name": "澜音单", "tracks": [{"source": "wy", "songId": "7"}]}]})
        p2 = next(pid for pid, pl in sp.playlists.items() if pl.name == "澜音单")
        # 关键：A **没有**再 GET ⇒ p2 ∉ base_served（这台设备还不知道它存在）
        self.assertNotIn(p2, A.base_served.view)
        rev = sp.revision
        # 栖弦提交缺 p2：没交付过 ⇒ 不判删、不写墓碑、不出卡、不进 never_owned
        r = sp.merge(A.client_id, sub([("tx", "1")], nid="p1", name="栖弦单"))
        self.assertNotIn(p2, r.meta["removed_playlists"])
        self.assertEqual(r.meta["never_owned"]["playlists"], [])
        self.assertIn(p2, canon_playlists(sp, A.client_id))   # 仍存活
        self.assertNotIn(p2, sp.tombstones)
        self.assertEqual(sp.revision, rev)                    # 无删除 ⇒ revision 不动
        # 澜音照常提交带 p2 → 正常保留（没有任何人删过它）
        r2 = sp.merge(CE.client_id, {"playlists": [
            {"native_id": "p1", "name": "栖弦单", "tracks": [{"source": "tx", "songId": "1"}]},
            {"native_id": "p2", "name": "澜音单", "tracks": [{"source": "wy", "songId": "7"}]}]})
        self.assertIn(p2, canon_playlists(sp, A.client_id))
        self.assertFalse(r2.meta["suppressed_playlists"])

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
        self.assertIn("wy:y", canon_keys(sp, A.client_id))   # 不删

    # ---- 第四轮 P0-C：同空间成员互信，B 直接删除（无确认卡）后，A（曾提交者）
    #      GET 跨过删除点 + 越过冷静期 → 本地残留回推 = 显式恢复（不再限 origin_client）
    #      ——"看过删除结果 + 冷静期已过"视为用户意图恢复，直接生效清墓碑 ----
    #      第七轮：删除必须由"曾提交过 p1"的设备发起（owned 缺席才是删除意图），
    #      否则 B 的缺席只会被记成 never_owned、删不掉任何东西。
    def test_other_device_residue_after_bulk_delete_restores(self):
        sp = self._space()
        A = self._dev(sp, "d-a")                      # 栖弦（can_delete=True）
        B = self._dev(sp, "d-b")
        # A 新增歌单 p1 + x 并提交（A 是"曾经拥有者"）
        sp.merge(A.client_id, sub([("tx", "x")], nid="p1", name="歌单"))
        sp.deliver(A.client_id)
        # B 建基线：同时提交 p2 与 p1+x（B 也进入 p1 的 owned 水位）
        sp.merge(B.client_id, {"playlists": [
            {"native_id": "p2", "name": "其他", "tracks": [{"source": "kw", "songId": "0"}]},
            {"native_id": "p1", "name": "歌单", "tracks": [{"source": "tx", "songId": "x"}]}]})
        sp.deliver(B.client_id)
        pl_id = next(k for k, v in sp.playlists.items() if v.name == "歌单")
        # B 提交缺 p1（B 提交过它 = owned）⇒ 判删、写墓碑
        r = sp.merge(B.client_id, sub([("kw", "0")], nid="p2", name="其他"))
        self.assertIn(pl_id, r.meta["removed_playlists"])
        self.assertIn(pl_id, sp.tombstones)           # 直接写墓碑（无卡，D33）
        # A GET 跨过删除点（base_served 推进到删除后 revision）
        sp.deliver(A.client_id)
        # A 本地残留回推（提交视图仍带 p1+x）→ 第四轮 P0-C：看过删除结果 + 冷静期已过
        # （测试 grace=0）→ 显式恢复，直接生效清墓碑
        r2 = sp.merge(A.client_id, sub([("tx", "x")], nid="p1", name="歌单"))
        self.assertIn(pl_id, canon_playlists(sp, A.client_id))
        self.assertNotIn(pl_id, sp.tombstones)

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

    # ---- 第十二轮（2026-10-08 真机：洛雪删掉的歌在栖弦里删不掉，最后还被"显式恢复"复活）----
    # 现场序列（journal）：09:11:14 栖弦加歌并置顶 → 09:12:05 洛雪整份 PUT 删掉它（写墓碑）
    # → 09:12:13 栖弦 GET：它的 base_served 里**根本没有这首歌**（它加歌是 PUT，不更新交付
    # 基线）⇒ 交付视图与它上次拿到的**完全相同** ⇒ `_next_stamp` 的 same_view 短路返回旧戳
    # ⇒ 栖弦按 section 级 LWW「远端时间更大才采纳」判定远端没更新 ⇒ 本地副本一直留着
    # ⇒ 每轮回推一次（被墓碑压制）⇒ 越过 120s 冷静期后这次回推被当成"用户显式恢复"
    # （`_crossed_deletion_point`）⇒ 清墓碑 + 重新入列置顶 = 删除被撤销（真机 rev 21）。
    def test_stale_readd_after_remote_delete_does_not_revive(self):
        sp = self._space()
        sp.restore_grace_seconds = 3600     # 同步循环几秒一轮：远未越过冷静期
        A = self._dev(sp, "d-a", "cyshine-v1")
        L = self._lx(sp, "d-l")
        C = self._dev(sp, "d-c", "cyshine-v1")   # 第三台设备：本轮不 GET（钉住墓碑，
        #                                          复刻真机空间里那些从不 ack 的幽灵客户端）

        sp.merge(A.client_id, sub([("tx", "t1")]))          # 栖弦建歌单
        sp.merge(L.client_id, sub([("tx", "t1")]))          # 洛雪有基线（同一 native_id）
        sp.merge(C.client_id, sub([("tx", "t1")]))
        stale = sp.deliver(A.client_id).stamp               # 栖弦交付基线：只有 tx:t1
        sp.deliver(L.client_id)
        # 栖弦加歌（PUT 不更新它的交付基线），并声明一个很新的本地 modifiedAt
        sp.merge(A.client_id, sub([("tx", "t1"), ("tx", "k9")]),
                 client_modified_at="2030-01-01T00:00:00.000Z")
        sp.deliver(L.client_id)                             # 洛雪拉到这首歌
        r = sp.merge(L.client_id, sub([("tx", "t1")]))      # 洛雪删掉它 → 写墓碑
        self.assertIn("tx:k9", {k for v in r.meta["removed_tracks"].values() for k in v})
        self.assertIn("tx:k9", sp.tombstones)

        # 修复 A：这次 GET 的视图与它上次拿到的一模一样（都不含 k9），但它手上那份
        # （base_submitted）含 k9 ⇒ 交付戳必须推进、压过它自己声明的 modifiedAt，
        # 否则栖弦永远不采纳这次删除，删除在设备侧永远不生效。
        dr = sp.deliver(A.client_id)
        self.assertNotIn("tx:k9", {k for e in dr.view.values() for k in e["tracks"]})
        self.assertGreater(dr.stamp, "2030-01-01T00:00:00.000Z")
        self.assertGreater(dr.stamp, stale)

        # 栖弦本地还没应用删除，拿旧视图回推 → 压制、不复活
        r = sp.merge(A.client_id, sub([("tx", "t1"), ("tx", "k9")]))
        self.assertIn("tx:k9", r.meta["suppressed_tracks"])
        self.assertEqual(r.meta["added_tracks"], {})        # 被压制的键不算"新增"
        self.assertNotIn("tx:k9", canon_keys(sp, A.client_id))
        self.assertIn("tx:k9", sp.tombstones)

        # 修复 B：它还在回推这个键（在 carried 里）⇒ 不算"看过即采纳"（D21 判据的后半句）
        # ⇒ 它不能被记 ack，墓碑也就不可能因"全员确认"被 GC 收走。否则收走墓碑之后，
        # 它下一次回推就成了**无墓碑的普通重加** = 已确认的删除被复活。
        sp.deliver(A.client_id)
        self.assertIn("tx:k9", sp.tombstones)
        self.assertNotIn(A.client_id, sp.tombstones["tx:k9"].acked)

        # 再回推一次：仍然压制，歌曲不复活、墓碑仍在
        r = sp.merge(A.client_id, sub([("tx", "t1"), ("tx", "k9")]))
        self.assertIn("tx:k9", r.meta["suppressed_tracks"])
        self.assertNotIn("tx:k9", canon_keys(sp, A.client_id))
        self.assertIn("tx:k9", sp.tombstones)

    # ---- 第四轮 P1-2 / D33：untrusted（无删除能力）端点批量删除 → withheld ----
    def test_untrusted_bulk_delete_withheld(self):
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
        # D33：安全阀永不弹卡——不应用（条目保留、不写墓碑），只在
        # meta.safety_valve.withheld 留痕。
        self.assertTrue(r.meta["safety_valve"]["withheld"])
        self.assertIsNone(r.meta["safety_valve"]["applied"])
        self.assertEqual(len(sp.tombstones), 0)                # 未写墓碑
        # 注：sp.tracks 的键是内部 tr_… id，判"还剩几首活曲目"要用 Track.deleted_at
        self.assertEqual(len([t for t in sp.tracks.values() if t.deleted_at is None]), 12)

    # ---- 第九轮（2026-10-08 真机事故「lx 少传一张歌单，Yes 从 253 掉到 21」）----
    #      整份歌单消失（客户端不再提交它）只删**歌单本身**，不据此派生曲目级删除：
    #      派生/合并歌单里的曲目往往同时躺在来源歌单里（真机 Yes ∩ 合并歌单 = 232/235），
    #      逐曲判删会写全局墓碑（Track.deleted_at）⇒ 来源歌单里的同一批歌被一起清空。
    def test_removed_playlist_does_not_tombstone_its_tracks(self):
        sp = self._space()
        L = self._dev(sp, "lx", dialect="lx-x")
        shared = [("tx", "s1"), ("tx", "s2"), ("tx", "s3")]
        yes_only = [("wy", "y1")]
        mix_only = [("kg", "m1")]
        r0 = sp.merge(L.client_id, {"playlists": [
            {"native_id": "p-yes", "name": "Yes",
             "tracks": [{"source": s, "songId": i} for s, i in shared + yes_only]},
            {"native_id": "p-mix", "name": "合并歌单",
             "tracks": [{"source": s, "songId": i} for s, i in shared + mix_only]},
        ]})
        self.assertTrue(r0.meta["adopted"])                    # lx 首次提交（只增不删）
        pl_yes = next(pid for pid, pl in sp.playlists.items() if pl.name == "Yes")
        pl_mix = next(pid for pid, pl in sp.playlists.items() if pl.name == "合并歌单")
        sp.deliver(L.client_id)
        rev = sp.revision
        # 第二轮：整份缺少「合并歌单」（Yes 照旧 4 首）→ 旧逻辑会把 4 首共享曲全部墓碑化
        r = sp.merge(L.client_id, {"playlists": [
            {"native_id": "p-yes", "name": "Yes",
             "tracks": [{"source": s, "songId": i} for s, i in shared + yes_only]},
        ]})
        self.assertIn(pl_mix, r.meta["removed_playlists"])
        self.assertNotIn(pl_mix, r.meta["removed_tracks"])     # 不派生曲目级删除
        self.assertNotIn(pl_mix, canon_playlists(sp, L.client_id))   # 歌单确实没了
        # 曲目键一个都不进墓碑（真机事故被写掉的就是这 235 个曲目墓碑）；
        # 歌单墓碑只在这一个客户端没有回推时会被 ack-GC，故不强制它留在 sp.tombstones 里。
        for k in ["tx:s1", "tx:s2", "tx:s3", "wy:y1", "kg:m1"]:
            self.assertNotIn(k, sp.tombstones)
        self.assertTrue(all(t.element == "playlist" for t in sp.tombstones.values()))
        keys = canon_keys(sp, L.client_id)
        for k in ["tx:s1", "tx:s2", "tx:s3", "wy:y1"]:
            self.assertIn(k, keys)                            # Yes 里的共享曲一首没少
        tid = sp.identity_to_tr.get("kg:m1")                  # 只在「合并歌单」里的那首也还活着
        self.assertIsNotNone(tid)
        self.assertIsNone(sp.tracks[tid].deleted_at)
        view, _ = sp.render_for(sp.clients[L.client_id])
        self.assertEqual(len(view[pl_yes]["tracks"]), 4)       # 来源歌单仍完整
        self.assertEqual(sp.revision, rev + 1)                 # 删歌单照常推进 revision


if __name__ == "__main__":
    unittest.main()
