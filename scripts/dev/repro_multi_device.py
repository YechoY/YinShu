"""多设备改造方案 §2 M1 的复现脚本（只读，不改任何代码）。

场景：两台栖弦手机 A、B（各自身份），B 加了一首歌 s；A 因为本地有改动、
section 级 LWW 选择保留自己的整段（没有 s），同轮 GET 之后 PUT 回来。

预期（当前代码）：s 被判成"A 的删除" ⇒ 写墓碑 ⇒ 两台手机都丢。
同时打印"规则 R1"的反事实：owned = base_served ∩ base_submitted 时 s 是否该被删。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from engine.engine import SyncSpace  # noqa: E402


def sub(pairs, name="P", nid="lx-1"):
    return {"playlists": [{"native_id": nid, "name": name,
                           "tracks": [{"source": s, "songId": i} for s, i in pairs]}]}


def canon_keys(sp, cid):
    view, _ = sp.render_for(sp.clients[cid])
    out = set()
    for e in view.values():
        out |= set(e["tracks"])
    return out


sp = SyncSpace("space:multidevice-repro")
A = sp.register_client("acct:cyshine-v1:d-a", dialect="cyshine-v1",
                       identity_verified=True, can_delete=True)
B = sp.register_client("acct:cyshine-v1:d-b", dialect="cyshine-v1",
                       identity_verified=True, can_delete=True)
base = [("tx", "t1")]

print("1) A 首次接入       :", sp.merge(A.client_id, sub(base)).status, "(201=只增不删建基线)")
print("2) B 首次接入       :", sp.merge(B.client_id, sub(base)).status)
print("3) B 加歌 s         :", sp.merge(B.client_id, sub(base + [("tx", "s")])).meta["added_tracks"])
print("   云端有 s 吗      :", "tx:s" in canon_keys(sp, A.client_id))

print("4) A 的同步轮先 GET :", sp.deliver(A.client_id).status, "(交付视图已含 s)")
print("   A.base_served    :", {k: sorted(v["tracks"]) for k, v in A.base_served.view.items()})
print("   A.base_submitted :", {k: sorted(v["tracks"]) for k, v in A.base_submitted.items()})

# A 的 stale 整段 PUT（本地 section 更新 ⇒ 栖弦保留自己那段，里面没有 s）
r = sp.merge(A.client_id, sub(base))
print("5) A 的 stale PUT   : status=%s removed_tracks=%s" % (r.status, r.meta["removed_tracks"]))
has_s = "tx:s" in canon_keys(sp, A.client_id)
print("6) 云端还有 s 吗    :", has_s, "⇒", "*** 丢了（M1 复现）***" if not has_s else "还在（未复现）")
print("   墓碑              :", {k: t.element for k, t in sp.tombstones.items()})

# 规则 R1 的反事实（不改代码，只按新判据计算）
owned = set()
for pl, e in A.base_served.view.items():
    if pl in A.base_submitted:
        owned |= set(e["tracks"]) & set(A.base_submitted[pl]["tracks"])
print("R1 owned(A)         :", sorted(owned))
print("R1 会判删除吗       :", ("tx:s" in owned), "⇒ s 不在 owned 里 ⇒ 不判删除（修复方向成立）")
