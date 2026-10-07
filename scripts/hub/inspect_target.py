import pickle, os, sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "src"))
p = os.path.join(_ROOT, "data", "spaces", "space__main.pkl")

obj = pickle.load(open(p, "rb"))
eng = obj["engine"]
print("=== 歌单 ===")
for pl_id, pl_ in eng.playlists.items():
    print(f"  [{pl_id}] {pl_.name!r} deleted_at={getattr(pl_,'deleted_at',None)!r} track_ids={len(pl_.track_ids)}")
print("=== 目标歌单 track 顺序（pl_5W5224YFNAK62ND5JVYW0PNSSN 或对应歌单） ===")
for pl_id, pl_ in eng.playlists.items():
    print(f"  [{pl_.name!r}] 歌曲顺序:")
    for tid in pl_.track_ids:
        tr = eng.tracks.get(tid)
        if tr:
            print(f"    - {tr.key} (title={tr.title!r})")
print("=== 目标歌曲 tx:002xIrgo1GyEaG ===")
key = "tx:002xIrgo1GyEaG"
tid = eng.identity_to_tr.get(key)
tr = eng.tracks.get(tid) if tid else None
print(f"  identity_to_tr[{key}] = {tid}")
if tr:
    print(f"  Track: tr_id={tr.tr_id} source={tr.source} song_id={tr.song_id} title={tr.title!r} deleted_at={tr.deleted_at!r}")
print("=== clients 状态 ===")
for cid, c in eng.clients.items():
    b = c.base_served
    print(f"  {cid}: served_rev={b.served_revision if b else None} 歌单数={len(b.view) if b else 0}")
