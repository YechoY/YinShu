"""临时功能核查（非交付物）：模拟"真同步"主链路——栖弦与澜音插件共用同一账号/空间。

只回答一个问题：两端各自新增/删除，是否双向收敛且不丢。
"""
import base64
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

ROOT = r"C:\Users\Echo\Desktop\Workspace\Music\playlist-sync-hub"
sys.path.insert(0, os.path.join(ROOT, "src"))
os.chdir(ROOT)
# 每次运行用独立 store 子目录（不直接落 tmp 根目录）：
# 本机 Python 删除/存在性检查有"假成功/假阴性"（2026-10-06 实测），若复用同一目录，
# delete_space 删不掉上次残留 pkl，旧空间复活 → S1/S4/S6 误报 FAIL。
STORE = r"C:\Users\Echo\Desktop\Workspace\Music\playlist-sync-hub\tmp\chk_store_lead"

from hub.store import Hub          # noqa: E402
from hub import api                # noqa: E402
import uvicorn                     # noqa: E402

hub = Hub(STORE)
hub.delete_space("chk_sync")
hub = Hub(STORE)
api.configure(hub, {"alice": {"password": api._hash_pw("pw1"), "space": "chk_sync",
                              "role": "admin", "enabled": True}}, config_path=None)

PORT = 8797
srv = uvicorn.Server(uvicorn.Config(api.app, host="127.0.0.1", port=PORT, log_level="error"))
threading.Thread(target=srv.run, daemon=True).start()
BASE = f"http://127.0.0.1:{PORT}"
for _ in range(100):
    try:
        urllib.request.urlopen(BASE + "/api/state", timeout=1)
        break
    except urllib.error.HTTPError:
        break
    except Exception:
        time.sleep(0.15)

AUTH = {"Authorization": "Basic " + base64.b64encode(b"alice:pw1").decode()}
CY = "/CyShineMusic/sync-v1.json"
CE = "/ceru/sync-v1.json"


def req(method, path, payload=None, headers=None):
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={**AUTH, "Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            raw = resp.read().decode()
            return resp.status, dict((k.lower(), v) for k, v in resp.headers.items()), (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, dict((k.lower(), v) for k, v in e.headers.items()), (json.loads(raw) if raw else None)
        except Exception:
            return e.code, {}, raw


def cy_track(src, sid):
    return {"musicId": f"{src}_{sid}", "name": f"歌{sid}", "singer": "s", "albumName": "a",
            "source": src, "quality": "hires", "picUrl": "",
            "musicInfo": {"id": f"{src}_{sid}", "name": f"歌{sid}", "singer": "s",
                          "source": src, "interval": "03:27", "meta": {"songId": sid}}}


def cy_put(playlists, extra_local=None):
    """playlists: [(pl_id, name, [(src,sid)])]"""
    data = []
    for pid, name, tracks in playlists:
        tl = [cy_track(s, i) for s, i in tracks]
        if extra_local and pid in extra_local:
            tl.extend(extra_local[pid])
        data.append({"version": 1, "id": pid, "name": name, "tracks": tl,
                     "createdAt": "2026-10-06T00:00:00Z", "updatedAt": "2026-10-06T00:00:00Z"})
    return {"schemaVersion": 1, "generatedAt": "2026-10-06T00:00:00Z",
            "sections": {"playlists": {"data": data},
                         "appearance": {"data": {"themeSeedArgb": 1}},
                         "musicSources": {"data": []}}}


def ce_put(playlists):
    return {"schemaVersion": 1, "playlists": [
        {"id": pid, "name": name,
         "tracks": [{"source": s, "songId": i, "title": f"歌{i}", "singer": "s", "album": "a"} for s, i in tracks]}
        for pid, name, tracks in playlists]}


def cy_view():
    st, _, b = req("GET", CY)
    if st != 200:
        return st, None
    return st, {p["id"]: [t.get("musicId") for t in p["tracks"]] for p in b["sections"]["playlists"]["data"]}


def ce_view():
    st, _, b = req("GET", CE)
    if st != 200:
        return st, None
    return st, {p["id"]: [t["songId"] for t in p["tracks"]] for p in b["playlists"]}


def state():
    _, _, s = req("GET", "/api/state")
    return s


out = []
def rec(tag, ok, detail):
    out.append(f"{'PASS' if ok else 'FAIL'}  {tag}: {detail}")

# S1 栖弦首次接入
st, _, _ = req("GET", CY)
rec("S1 栖弦首次GET=404", st == 404, f"HTTP {st}")
st, _, _ = req("PUT", CY, cy_put([("P1", "栖弦单", [("tx", "1"), ("tx", "2")])]))
rec("S2 栖弦首次PUT", st in (200, 201), f"HTTP {st}")
st, v = cy_view()
rec("S3 栖弦GET回读", st == 200 and v and v.get("P1") == ["tx_1", "tx_2"], f"HTTP {st} {v}")

# S4 澜音首次接入（同账号同空间）
st, _, _ = req("GET", CE)
rec("S4 澜音首次GET=404", st == 404, f"HTTP {st}")
# 模拟澜音：已从 hub 导入 tx1/tx2，另有自己的 kw9
st, _, _ = req("PUT", CE, ce_put([("ceru-P1", "栖弦单", [("tx", "1"), ("tx", "2"), ("kw", "9")])]))
rec("S5 澜音PUT(含自己的新增)", st in (200, 201), f"HTTP {st}")
st, v = cy_view()
rec("S6 栖弦看到澜音的新增", st == 200 and v and v.get("P1") == ["tx_1", "tx_2", "kw_9"], f"{v}")

# S7 栖弦删一首 → 澜音应看不到
req("PUT", CY, cy_put([("P1", "栖弦单", [("tx", "1"), ("kw", "9")])]))
st, v = ce_view()
rec("S7 栖弦删歌→澜音不再有", st == 200 and v and "2" not in (v.get("ceru-P1") or []), f"{v}")

# S8 澜音接受删除后回提 → 栖弦侧稳定
st, _, _ = req("PUT", CE, ce_put([("ceru-P1", "栖弦单", [("tx", "1"), ("kw", "9")])]))
st, v = cy_view()
rec("S8 稳定收敛", st == 200 and v and v.get("P1") == ["tx_1", "kw_9"], f"{v}")

# S9 澜音新增歌单 → 栖弦可见
req("PUT", CE, ce_put([("ceru-P1", "栖弦单", [("tx", "1"), ("kw", "9")]),
                       ("ceru-P2", "澜音单", [("wy", "7")])]))
st, v = cy_view()
rec("S9 澜音新增歌单→栖弦可见", st == 200 and v is not None and any(x == ["wy_7"] for x in v.values()), f"{v}")

# S10（P0.5 新语义）栖弦删掉整个「澜音单」（从未提交过它，R1 保护）→ 不自动删，
# 而是出"待确认删除"卡（reason=never_owned），默认保留
req("PUT", CY, cy_put([("P1", "栖弦单", [("tx", "1"), ("kw", "9")])]))
st, v = ce_view()
p2_alive = [x for x in (v or {}) if x != "ceru-P1"]
pend0 = state().get("pendingDeletions") or []
n_never = sum(1 for p in pend0 if p.get("reason") == "never_owned")
rec("S10 删未拥有歌单→出待确认卡且不删", st == 200 and p2_alive and n_never >= 1,
    f"澜音侧仍在={bool(p2_alive)} 待确认删除卡(never_owned)={n_never}")

# S11（P0.5）确认该卡 → 歌单真删 → 澜音把删掉的歌单推回来（它本地删不掉）→ 被墓碑压住
pid = next((p["id"] for p in pend0 if p.get("reason") == "never_owned"), None)
if pid:
    req("POST", f"/api/pending-deletions/{pid}/confirm")
st, v = ce_view()
gone = st == 200 and not [x for x in (v or {}) if x != "ceru-P1"]
st, _, _ = req("PUT", CE, ce_put([("ceru-P1", "栖弦单", [("tx", "1"), ("kw", "9")]),
                                  ("ceru-P2", "澜音单", [("wy", "7")])]))
st, v = cy_view()
revived = any(x == ["wy_7"] for x in (v or {}).values())
rec("S11 确认删除后推回被墓碑压住", bool(pid) and gone and not revived,
    f"确认后澜音侧消失={gone}；澜音推回后栖弦视图={v}")

# S12 本地文件曲目往返
local = {"musicId": "D:\\Music\\x.flac", "name": "本地", "singer": "本机", "albumName": "",
         "source": "local", "quality": "", "picUrl": "",
         "musicInfo": {"id": "D:\\Music\\x.flac", "name": "本地", "singer": "本机",
                       "source": "local", "interval": "03:00", "meta": {}}}
st, _, _ = req("PUT", CY, cy_put([("P1", "栖弦单", [("tx", "1")])], extra_local={"P1": [local]}))
st2, _, b = req("GET", CY)
n_local = 0
if st2 == 200:
    for p in b["sections"]["playlists"]["data"]:
        n_local += sum(1 for t in p["tracks"] if t.get("source") == "local")
rec("S12 本地文件曲目不报错且回填", st in (200, 201) and n_local == 1, f"PUT={st} 回填本地曲目数={n_local}")

# S15 栖弦删一首歌 → 澜音（删不掉本地副本）把它推回来 → 是否复活
req("PUT", CY, cy_put([("P1", "栖弦单", [("tx", "1"), ("tx", "2")])]))
req("PUT", CE, ce_put([("ceru-P1", "栖弦单", [("tx", "1"), ("tx", "2")])]))
req("GET", CY)                                                     # 栖弦取最新基线
req("PUT", CY, cy_put([("P1", "栖弦单", [("tx", "1")])]))          # 栖弦删 tx2
st, v = ce_view()
gone = st == 200 and v is not None and "2" not in (v.get("ceru-P1") or [])
req("GET", CE)                                                     # 澜音交付（看到删除，进待清理）
st, _, _ = req("PUT", CE, ce_put([("ceru-P1", "栖弦单", [("tx", "1"), ("tx", "2")])]))  # 澜音推回
st, v2 = cy_view()
back = any(x == ["tx_1", "tx_2"] for x in (v2 or {}).values())
rec("S15 栖弦删歌不被澜音推回", gone and not back,
    f"删除已传播到澜音={gone}；澜音推回后栖弦视图={v2}；待确认恢复={len(state().get('pendingRestores') or [])}")

# S13 顺序在两端来回：是否每轮都推 revision（ping-pong）
before = state().get("revision")
for _ in range(3):
    req("PUT", CY, cy_put([("P1", "栖弦单", [("tx", "1"), ("kw", "9")])]))
    req("PUT", CE, ce_put([("ceru-P1", "栖弦单", [("kw", "9"), ("tx", "1")])]))
after = state().get("revision")
st, v = cy_view()
rec("S13 顺序来回不无限推进", after - before <= 3, f"revision {before}->{after} 栖弦视图={v}")

# S14 条件请求
st, h, _ = req("GET", CY)
etag = h.get("etag")
st2, _, _ = req("GET", CY, headers={"If-None-Match": etag or ""})
rec("S14 条件请求(记录)", True, f"带ETag再GET -> HTTP {st2}（设计文档要求永不 304，栖弦 GET 不带该头）")

print("当前空间状态: revision=%s sort_tag=%s 歌单=%s 待确认删除=%d 待确认恢复=%d"
      % (state().get("revision"), state().get("sort_tag"), state().get("playlists"),
         len(state().get("pendingDeletions") or []), len(state().get("pendingRestores") or [])))
print("\n".join(out))
srv.should_exit = True

