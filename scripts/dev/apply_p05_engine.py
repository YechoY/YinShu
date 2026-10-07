# -*- coding: utf-8 -*-
"""P0.5 补丁：engine.py 的 never_owned 缺席 → 待确认删除卡（一次性补丁脚本，保留于 tmp/ 备查）。"""
import io

p = r"C:\Users\Echo\Desktop\Workspace\Music\playlist-sync-hub\src\engine\engine.py"
s = io.open(p, encoding="utf-8").read()

anchor = '            meta["pending_delete_id"] = pid\n'
assert s.count(anchor) == 1, "anchor count=%d" % s.count(anchor)

insert = anchor + '''        # P0.5：never_owned 缺席 → 待确认删除卡（默认不删，用户确认后写墓碑 →
        # 墓碑压制另一端推回，保住 S10/S11 原意；确认前空间与 revision 均不动）。
        # 与安全阀卡互斥：同轮既有安全阀又有 never_owned 时优先只出安全阀卡（防卡泛滥）。
        if not (deferred and (deferred["playlists"] or deferred["tracks"])) and \\
           (never_owned_pl or any(never_owned_t.values())):
            pid = new_ulid("del")
            self.pending_deletions[pid] = {
                "space": self.space_id, "client": client_id, "reason": "never_owned",
                "playlists": set(never_owned_pl),
                "tracks": {pl: set(ks) for pl, ks in never_owned_t.items() if ks},
                "created_at": now,
            }
            meta["never_owned_delete_id"] = pid
        meta["never_owned"] = {
            "playlists": sorted(never_owned_pl),
            "tracks": {pl: sorted(ks) for pl, ks in never_owned_t.items() if ks},
        }
'''

s = s.replace(anchor, insert, 1)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("OK: never_owned card inserted")
