# -*- coding: utf-8 -*-
"""P0.5 补丁 2：修正 never_owned_t 只覆盖歌单级缺席的 bug → 应遍历全部 base pl。"""
import io

p = r"C:\Users\Echo\Desktop\Workspace\Music\playlist-sync-hub\src\engine\engine.py"
s = io.open(p, encoding="utf-8").read()

old = """        never_owned_pl = (base_keys - cur_keys) - owned_pl
        never_owned_t: Dict[str, Set[str]] = {}
        for pl in base_keys - cur_keys:
            base = set(base_view[pl].get("tracks", ()))
            owned = owned_t.get(pl, set())
            cur = set(view.get(pl, {}).get("tracks", ()))
            never_owned_t[pl] = (base - cur) - owned
"""
new = """        never_owned_pl = (base_keys - cur_keys) - owned_pl
        never_owned_t: Dict[str, Set[str]] = {}
        # 注意：必须遍历**全部** base 歌单（含仍存在的歌单）——曲目级缺席（如 y 从 p1 里
        # 消失但 p1 仍在）只会在 base_keys ∩ cur_keys 里出现；只遍历 base_keys - cur_keys
        # 会漏掉这一层（补丁 2）。
        for pl in base_keys:
            base = set(base_view[pl].get("tracks", ()))
            owned = owned_t.get(pl, set())
            cur = set(view.get(pl, {}).get("tracks", ()))
            never_owned_t[pl] = (base - cur) - owned
"""
assert s.count(old) == 1, "old count=%d" % s.count(old)
s = s.replace(old, new, 1)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("OK: never_owned_t loop fixed to base_keys")
