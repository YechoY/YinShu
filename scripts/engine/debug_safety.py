import os, sys
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "src"))
sys.path.insert(0, _ROOT)
from engine.engine import SyncSpace
from tests.engine.helpers import pl, sub

sp = SyncSpace("dbg")
sp.register_client("A", dialect="cyshine-v1", identity_verified=True)
tracks = [("tx", f"t{i}") for i in range(12)]
r1 = sp.merge("A", sub(pl("p1", "歌单", *tracks)))
print("adopt status", r1.status, "rev", sp.revision, "playlists", list(sp.playlists), "order", sp.playlist_order)
print("aliases", sp.playlist_aliases)
d = sp.deliver("A")
print("deliver view", d.view)
print("base_served", sp.clients["A"].base_served.view)
r2 = sp.merge("A", sub(pl("p1", "歌单", ("tx", "t0"))))
print("delete merge meta", r2.meta)
print("rev", sp.revision, "tombstones", list(sp.tombstones), "pending_del", list(sp.pending_deletions))
print("SAFE consts", sp.SAFE_PL_DELETE_COUNT, sp.SAFE_PL_DELETE_RATIO, sp.SAFE_TR_DELETE_RATIO, sp.SAFE_TR_DELETE_MIN)
# 直接重放安全阀判定
sp2 = SyncSpace("dbg2"); sp2.register_client("A", dialect="cyshine-v1", identity_verified=True)
sp2.merge("A", sub(pl("p1", "歌单", *tracks))); sp2.deliver("A")
basev = sp2.clients["A"].base_served.view
plid = next(iter(basev))
removed_t = {plid: set(basev[plid]["tracks"]) - {"tx:t0"}}
print("removed_t", removed_t)
print("pl.track_ids len", len(sp2.playlists[plid].track_ids))
print("live tr_ids", len([t for t in sp2.playlists[plid].track_ids if t in sp2.identity_to_tr and sp2.tracks[t].deleted_at is None]))
print("trigger?", sp2._safety_trigger(set(), removed_t))

