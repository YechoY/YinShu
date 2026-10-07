import pickle, os, sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "src"))
p = os.path.join(_ROOT, "data", "spaces", "space__main.pkl")

obj = pickle.load(open(p, "rb"))
eng = obj["engine"]
print("revision:", eng.revision)
print("=== journal 最近 12 条 ===")
for j in eng.journal[-12:]:
    m = j.get("meta", {})
    print(f"[{j.get('at','?')}] client={j.get('client','?')} rev={j.get('revision','?')} "
          f"action={j.get('action','merge')} mutated={m.get('mutated')} "
          f"added_t={m.get('added_tracks')} removed_t={m.get('removed_tracks')} "
          f"added_pl={m.get('added_playlists')} removed_pl={m.get('removed_playlists')} "
          f"suspects={m.get('suspects')} deferred={m.get('deferred')} "
          f"supp_pl={m.get('suppressed_playlists')} supp_tr={m.get('suppressed_tracks')}")
print("=== 墓碑 ===")
for k, t in eng.tombstones.items():
    print(f"  {k}: {t.element} deleted_at={t.deleted_at} origin={t.origin_client}")
print("=== 待确认恢复 ===")
for k, r in eng.pending_restores.items():
    print(f"  {k}: {r.get('reason')} playlists={r.get('playlists')}")
