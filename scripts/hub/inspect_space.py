import pickle, os, sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "src"))
p = os.path.join(_ROOT, "data", "spaces", "space__main.pkl")

obj = pickle.load(open(p, "rb"))
print("类型:", type(obj).__name__, "len:", len(obj) if hasattr(obj, "__len__") else "-")

def show(d, depth=0, maxd=3, seen=None):
    seen = seen or set()
    ind = "  " * depth
    if depth > maxd:
        return
    if isinstance(d, dict):
        for k, v in list(d.items())[:30]:
            if isinstance(v, (dict, list)):
                print(f"{ind}{k}: {type(v).__name__} (len={len(v)})")
                if id(v) not in seen:
                    seen.add(id(v))
                    show(v, depth + 1, maxd, seen)
            else:
                print(f"{ind}{k}: {v!r}"[:160])
    elif isinstance(d, list):
        print(f"{ind}[list len={len(d)}]")
        for i, item in enumerate(d[:4]):
            print(f"{ind}  [{i}]: {type(item).__name__}")
            if id(item) not in seen:
                seen.add(id(item))
                show(item, depth + 1, maxd, seen)

show(obj)
print()
eng = obj.get("engine")
print("=== canonical 歌单视图（engine.playlists） ===")
if eng is not None and hasattr(eng, "playlists"):
    for pid, pl in eng.playlists.items():
        tracks = getattr(pl, "tracks", None) or {}
        print(f"  [{pid}] {pl.name!r} · 歌曲 {len(tracks)} 首 · deleted_at={getattr(pl,'deleted_at',None)!r}")
        for k in sorted(tracks)[:5]:
            print(f"      - {k}")
print("revision:", getattr(eng, "revision", "?"))
print("曲目池 engine.tracks:", len(getattr(eng, "tracks", {}) or {}))
print("身份映射 identity_to_tr:", len(getattr(eng, "identity_to_tr", {}) or {}))
print("歌单别名 playlist_aliases:", len(getattr(eng, "playlist_aliases", {}) or {}))
print("墓碑(tombstones):", len(getattr(eng, "tombstones", {}) or {}))
print()
print("=== 歌单实际歌曲引用（playlist.tracks） ===")
for pid, pl in eng.playlists.items():
    tr = getattr(pl, "tracks", None)
    if isinstance(tr, (set, frozenset)):
        print(f"  [{pid}] {pl.name!r} · {len(tr)} 首")
        for t in sorted(tr)[:6]:
            print(f"      - {t}")
    else:
        print(f"  [{pid}] {pl.name!r} · tracks={type(tr).__name__}")
print()
print("=== 客户端（clients） ===")
for cid, cl in eng.clients.items():
    b = getattr(cl, "base_served", None)
    print(f"  {cid} dialect={getattr(cl,'dialect','?')} served_rev={getattr(b,'served_revision',None) if b else None} view歌单={len(getattr(b,'view',{}) or {}) if b else '-'}")
