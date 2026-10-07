import os, sys
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "src"))
sys.path.insert(0, _ROOT)
from engine.engine import SyncSpace
from tests.engine.helpers import pl, sub

def trackkeys(sp, pl_name):
    for pid,p in sp.playlists.items():
        if p is not None and p.deleted_at is None and p.name==pl_name:
            return {sp.tracks[t].key for t in p.track_ids if sp.tracks[t].deleted_at is None}
    return None

sp = SyncSpace("t02")
sp.register_client("A", dialect="cyshine-v1", identity_verified=True)
sp.register_client("B", dialect="cyshine-v1", identity_verified=True)
sp.merge("A", sub(pl("p1","歌单",("tx","x")))); sp.deliver("A")
sp.merge("B", sub(pl("p1","歌单",("tx","x")))); sp.deliver("B")
sp.deliver("A")
r = sp.merge("A", sub(pl("p1","歌单")))
print("A delete meta:", r.meta)
print("rev", sp.revision, "tombs", list(sp.tombstones))
print("live", trackkeys(sp,"歌单"))
sp.deliver("B")
print("B delivered:", sp.clients["B"].base_served.view)
print("after B del, tombs:", list(sp.tombstones))
sp.register_client("C", dialect="cyshine-v1", identity_verified=False)
sp.merge("C", sub(pl("p1","歌单",("tx","x"))))
print("C merged, tombs:", list(sp.tombstones), "pending_restores:", list(sp.pending_restores))
sp.deliver("C")
print("C delivered:", sp.clients["C"].base_served.view)
print("live", trackkeys(sp,"歌单"))
