# -*- coding: utf-8 -*-
"""从 trajectory.jsonl 恢复被我误伤的 test_api_http.py / test_devices_http.py。

策略：按时间顺序收集对该文件的 Write(content) 与 Edit(old/new)，
从最后一个 Write 的 content 开始，依序应用后续 Edit（replace），重建最终版本。
"""
import json
import io
import os

TR = r"C:\Users\Echo\AppData\Local\Doubao\User Data\Default\.doubao\agent_mode\workspace\.sessions\38445445754569730\agents\m_0cwpP45zw7s\system\trajectory.jsonl"
ROOT = r"C:\Users\Echo\Desktop\Workspace\Music\playlist-sync-hub"
TARGETS = {
    os.path.normpath(os.path.join(ROOT, "tests\\hub\\test_api_http.py")): "test_api_http.py",
    os.path.normpath(os.path.join(ROOT, "tests\\hub\\test_devices_http.py")): "test_devices_http.py",
}

events = {t: [] for t in TARGETS}   # (kind, payload)
with io.open(TR, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        role = ev.get("role")
        tcs = ev.get("tool_calls") or []
        for tc in tcs:
            fn = (tc.get("function") or {}).get("name")
            args = (tc.get("function") or {}).get("arguments") or {}
            fp = args.get("file_path")
            if fn in ("Write", "Edit") and fp:
                nfp = os.path.normpath(fp)
                if nfp in TARGETS:
                    if fn == "Write":
                        events[nfp].append(("write", args.get("content") or ""))
                    else:
                        events[nfp].append(("edit", (args.get("old_string"), args.get("new_string"))))

for fp, name in TARGETS.items():
    evs = events[fp]
    # 找最后一个 write
    last_write_idx = None
    for i, (k, _) in enumerate(evs):
        if k == "write":
            last_write_idx = i
    if last_write_idx is None:
        print(f"{name}: 没有找到任何 Write！")
        continue
    content = evs[last_write_idx][1]
    applied = 0
    failed = []
    for k, payload in evs[last_write_idx + 1:]:
        if k == "edit":
            old, new = payload
            if old is None or old == "":
                failed.append(("empty-old", old))
                continue
            if content.count(old) != 1:
                failed.append(("count=%d" % content.count(old), old[:80]))
                continue
            content = content.replace(old, new, 1)
            applied += 1
    out = os.path.join(ROOT, "tests", "hub", name)
    with io.open(out, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    print(f"{name}: write@{last_write_idx} + {applied} edits；failed={failed if failed else '无'}")
    print(f"  行数={content.count(chr(10))} 大小={len(content)}")
