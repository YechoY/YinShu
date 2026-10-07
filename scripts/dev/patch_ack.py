import io

p = "src/engine/engine.py"
s = io.open(p, encoding="utf-8").read()
old = '''        submitted_keys = {k for e in view.values() for k in e["tracks"]}
        for key, t in self.tombstones.items():
            if not client.can_delete:
                continue   # P1-3 配套：澜音删不掉本地，其提交不参与确认水位（不 ack、不撤销）
            carried = (key in view) if t.element == "playlist" else (key in submitted_keys)
            if carried:
                t.carried.add(client_id)
                t.acked.discard(client_id)
            else:
                t.carried.discard(client_id)
                t.acked.add(client_id)'''
new = '''        submitted_keys = {k for e in view.values() for k in e["tracks"]}
        for key, t in self.tombstones.items():
            carried = (key in view) if t.element == "playlist" else (key in submitted_keys)
            if carried:
                t.carried.add(client_id)
                t.acked.discard(client_id)
            else:
                t.carried.discard(client_id)
                t.acked.add(client_id)   # 提交不再带该键 = 确认
                # 注：can_delete=False（澜音）也按"提交内容"确认（D21）——GET 看过不算数
                # （删不掉本地、必带回），但提交不再带 = 真采纳；deliver 段已对澜音关掉 ack。'''
assert old in s, "old not found"
io.open(p, "w", encoding="utf-8", newline="").write(s.replace(old, new))
print("patched")
