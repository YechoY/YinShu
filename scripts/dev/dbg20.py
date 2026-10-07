import sys; sys.path.insert(0, "src")
from engine.engine import SyncSpace

sp = SyncSpace("t")

def reg(c):
    sp.register_client(c, dialect="cyshine-v1", identity_verified=True)

def pl(nid, name, pairs):
    return {"playlists": [{"native_id": nid, "name": name,
                           "tracks": [{"source": s, "songId": i} for s, i in pairs]}]}

reg("A"); reg("B"); reg("C")
print("A1:", sp.merge("A", pl("p1", "歌单甲", [("tx", "x")])).meta["revision"])
sp.deliver("A")
r = sp.merge("B", pl("p2", "歌单乙", [("wy", "y")]))
print("B2 adopt?", r.meta.get("adopted"), "rev", sp.revision)
r = sp.merge("C", pl("p3", "歌单丙", [("kw", "k")]))
print("C3 adopt?", r.meta.get("adopted"), "rev", sp.revision)
rev_after = sp.revision
sp.deliver("A")
r = sp.merge("A", pl("p1", "歌单甲", [("tx", "x")]))
print("A back: mutated", r.meta["mutated"], "removed_pl", r.meta["removed_playlists"],
      "never_owned", r.meta["never_owned"], "rev", sp.revision)
sp.deliver("B")
r = sp.merge("B", pl("p2", "歌单乙", [("wy", "y")]))
print("B back: mutated", r.meta["mutated"], "removed_pl", r.meta["removed_playlists"],
      "never_owned", r.meta["never_owned"], "rev", sp.revision)
print("final", sp.revision, "expected", rev_after)
