"""审查用：R1 判据对"删掉**别人**建的内容"的影响（只读，不改源码）。

对照 tmp/check_sync.py 的 S9→S11：
  S9  澜音新增歌单 P2 → 栖弦 GET 看得到
  S10 栖弦 PUT（不含 P2，即"我在手机上删了它"）→ 期望澜音侧消失
  S11 澜音把 P2 推回来（它本地删不掉）→ 期望被墓碑压住
R1 上线后 S10/S11 变成 FAIL，本脚本把机制逐步打出来。
"""
import os
import sys

ROOT = r"C:\Users\Echo\Desktop\Workspace\Music\playlist-sync-hub"
sys.path.insert(0, os.path.join(ROOT, "src"))

from engine.engine import SyncSpace  # noqa: E402


def sub(pls):
    return {"playlists": [{"native_id": nid, "name": name,
                           "tracks": [{"source": s, "songId": i} for s, i in keys]}
                          for nid, name, keys in pls]}


def live(sp):
    return {pid: {"name": pl.name,
                  "tracks": [sp.tracks[t].key for t in pl.track_ids
                             if t in sp.tracks and sp.tracks[t].deleted_at is None]}
            for pid, pl in sp.playlists.items() if pl.deleted_at is None}


def keys_of(base):
    return {k for e in (base or {}).values() for k in e["tracks"]}


sp = SyncSpace("space:review-r1")
CY = sp.register_client("acct:cyshine-v1:default", dialect="cyshine-v1", can_delete=True)
CE = sp.register_client("acct:ceru-plugin:default", dialect="ceru-plugin", can_delete=False)

P1 = [("p1", "栖弦单", [("tx", "1")])]
P2 = [("p1", "栖弦单", [("tx", "1")]), ("p2", "澜音单", [("wy", "7")])]

sp.merge(CY.client_id, sub(P1))          # 栖弦建 P1
sp.deliver(CY.client_id)                 # 栖弦 GET（基线）
sp.merge(CE.client_id, sub(P1))          # 澜音首次接入
r = sp.merge(CE.client_id, sub(P2))      # 澜音建 P2
p2 = [pid for pid, v in live(sp).items() if v["name"] == "澜音单"]
print(f"1) 澜音新建 P2 -> {r.meta.get('added_playlists')}；活歌单={[v['name'] for v in live(sp).values()]}")
P2_INTERNAL = p2[0]

sp.deliver(CY.client_id)                 # S9：栖弦 GET，看到 P2
print(f"2) 栖弦 GET 后 base_served={[e['name'] for e in CY.base_served.view.values()]}"
      f" / base_submitted={[e['name'] for e in CY.base_submitted.values()]}")
print(f"   P2 在 owned(栖弦) 里吗: {P2_INTERNAL in (set(CY.base_served.view) & set(CY.base_submitted))}")

for n in (1, 2):
    r = sp.merge(CY.client_id, sub(P1))   # S10：栖弦整段提交，不含 P2
    print(f"3.{n}) 栖弦 PUT 不含 P2 -> removed_playlists={r.meta['removed_playlists']} "
          f"；P2 还活着={P2_INTERNAL in live(sp)} ；墓碑={P2_INTERNAL in sp.tombstones} "
          f"；suspects={r.meta['suspects'].get('__playlists__')}")
    sp.deliver(CY.client_id)              # 再交付一轮：栖弦又"看见" P2，但不会回提它

cy_view, _ = sp.render_for(CY)
ce_view, _ = sp.render_for(CE)
print(f"4) 栖弦视图={ {v['name']: len(v['tracks']) for v in cy_view.values()} }")
print(f"   澜音视图={ {v['name']: len(v['tracks']) for v in ce_view.values()} }  <- S10 期望只剩『栖弦单』")
print(f"5) 澜音推回后 S11 检查：栖弦还能看到『澜音单』吗 = "
      f"{any(v['name'] == '澜音单' for v in sp.render_for(CY)[0].values())}")

# 反事实：只要栖弦曾经**提交**过含 P2 的一轮（真实场景＝栖弦采纳过 P2 后又做了一次本地改动），
# owned 才包含 P2，之后它的删除才会生效。
sp.merge(CY.client_id, sub(P2))
print(f"6) 栖弦提交一轮含 P2 后 -> base_submitted={[e['name'] for e in CY.base_submitted.values()]}"
      f"；P2 在 owned 里吗="
      f"{P2_INTERNAL in (set(CY.base_served.view) & set(CY.base_submitted))}")
sp.deliver(CY.client_id)
r = sp.merge(CY.client_id, sub(P1))
print(f"7) 此时再删 P2 -> removed_playlists={r.meta['removed_playlists']} "
      f"；P2 还活着={P2_INTERNAL in live(sp)} ；墓碑={P2_INTERNAL in sp.tombstones}")
