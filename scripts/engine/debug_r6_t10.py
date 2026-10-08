"""第六轮 §1 回归：复现 test_10 墓碑压制失效（澜音推回被删 tx2 不应复活）。"""
import sys
sys.path.insert(0, "src")
from engine.engine import SyncSpace

sp = SyncSpace("space-a")
sp.register_client("cyshine", dialect="cyshine-v1", identity_verified=True, can_delete=True,
                   deliver_ack_on_view=True)
sp.register_client("lan", dialect="ceru-plugin", identity_verified=True, can_delete=True,
                   deliver_ack_on_view=False)

def submit(client, playlists, name_map=None):
    pls = []
    for pid, name, tracks in playlists:
        pls.append({"native_id": pid, "name": name,
                    "tracks": [{"source": k.split(":", 1)[0], "songId": k.split(":", 1)[1]}
                               for k in tracks]})
    r = sp.merge(client, {"playlists": pls, "opaque": None, "create_only": False})
    print(f"[{client}] rev={sp.revision} mutated={r.meta.get('mutated')} "
          f"supp_pl={r.meta.get('suppressed_playlists')} supp_t={r.meta.get('suppressed_tracks')} "
          f"added_t={r.meta.get('added_tracks')} removed_t={r.meta.get('removed_tracks')} "
          f"status={r.status} isolated={r.meta.get('isolated')}")
    return r

def deliver(client):
    r = sp.deliver(client)
    view_keys = {k: list(e["tracks"]) for k, e in r.view.items()}
    print(f"[{client}] GET rev={sp.revision} stamp={r.stamp} view={view_keys} cleanup={r.pending_cleanup}")
    return r

# 1. 栖弦建歌单 d1 [tx1, tx2]
submit("cyshine", [("d1", "删", ["tx:1", "tx:2"])])
deliver("cyshine")
# 3. 澜音推同名歌单 ceru-d（名字唯一命中 → 合并到 d1）[tx1, tx2]（首次接入只增）
submit("lan", [("ceru-d", "删", ["tx:1", "tx:2"])])
deliver("lan")
# 5. 栖弦删 tx2
submit("cyshine", [("d1", "删", ["tx:1"])])
print("   tombstones:", {k: (t.element, t.created_at_revision, t.deleted_at, sorted(t.acked))
                         for k, t in sp.tombstones.items()})
deliver("lan")
print("   acked after lan GET:", {k: sorted(t.acked) for k, t in sp.tombstones.items()})
# 7. 澜音推回 tx2（本地删不掉被迫重加）
r = submit("lan", [("ceru-d", "删", ["tx:1", "tx:2"])])
print("   tombstone after lan pushback:", {k: (t.element, t.created_at_revision, sorted(t.acked), sorted(t.carried))
                                            for k, t in sp.tombstones.items()})
# 8. 栖弦视图
r = deliver("cyshine")
tracks = {k: list(e["tracks"]) for k, e in r.view.items()}
print("FINAL cyshine view:", tracks)
assert "tx:2" not in [t for ts in tracks.values() for t in ts], "FAIL: tx2 复活"
print("OK: tx2 被压制")
