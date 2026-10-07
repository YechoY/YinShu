import os, sys
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "src"))
sys.path.insert(0, _ROOT)
from engine.engine import SyncSpace
from tests.engine.helpers import pl, sub

sp = SyncSpace("t07")
sp.register_client("A", dialect="cyshine-v1", identity_verified=True)
sp.register_client("B", dialect="cyshine-v1", identity_verified=True)
sp.merge("A", sub(pl("p1","歌单",("tx","x")))); sp.deliver("A")
print("after A adopt rev", sp.revision, "playlists", list(sp.playlists), "order", sp.playlist_order)
sp.merge("B", sub(pl("p1","歌单",("tx","x")))); sp.deliver("B")
print("after B adopt rev", sp.revision, "B base", sp.clients["B"].base_served.view)
sp.deliver("A")
r = sp.merge("A", sub())
print("A delete playlist meta:", r.meta)
print("rev", sp.revision, "order", sp.playlist_order, "p1.deleted_at", sp.playlists.get("pl", None))
for pid,p in sp.playlists.items(): print("  ", pid, "deleted_at=", p.deleted_at, "in_order=", pid in sp.playlist_order)
print("tombstones", list(sp.tombstones))
d = sp.deliver("B")
print("B delivered view:", d.view)
print("B.base_served.view:", sp.clients["B"].base_served.view)
