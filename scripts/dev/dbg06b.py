import sys, base64, json, urllib.request, urllib.error
sys.path.insert(0, "src")
import threading, time, tempfile
import uvicorn
from hub import api
from hub.store import Hub

store = tempfile.mkdtemp(prefix="dbg06b_", dir="tmp")
hub = Hub(store)
api.configure(hub, {
    "alice": {"password": api._hash_pw("pw1"), "space": "main",
              "role": "admin", "enabled": True},
}, config_path=None)
port = 18998
base = f"http://127.0.0.1:{port}"
server = uvicorn.Server(uvicorn.Config(api.app, host="127.0.0.1", port=port, log_level="warning"))
t = threading.Thread(target=server.run, daemon=True)
t.start()
for _ in range(100):
    try:
        urllib.request.urlopen(base + "/api/state", timeout=1)
        break
    except urllib.error.HTTPError:
        break
    except Exception:
        time.sleep(0.1)

def C(user, pw):
    tok = base64.b64encode(f"{user}:{pw}".encode()).decode()
    return {"Authorization": f"Basic {tok}", "Content-Type": "application/json; charset=utf-8"}

def req(method, path, payload=None, auth=None):
    h = dict(auth or {})
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(base + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            b = resp.read()
            return resp.status, (json.loads(b.decode()) if b else None)
    except urllib.error.HTTPError as e:
        b = e.read()
        return e.code, (json.loads(b.decode()) if b else None)

def cyshine(pls):
    return {"sections": {"playlists": {"data": pls, "modifiedAt": "2030-01-01T00:00:00.000Z"}}}

def cyshine_pl(pid, name, tracks):
    return {"id": pid, "name": name, "tracks": [
        {"musicId": f"{s}_{i}", "musicInfo": {"songId": i, "meta": {"source": s}}} for s, i in tracks]}

def ceru(pls):
    return {"playlists": pls, "modifiedAt": "2030-01-01T00:00:00.000Z"}

def ceru_pl(pid, name, tracks):
    return {"id": pid, "name": name, "tracks": [
        {"songId": i, "source": s} for s, i in tracks]}

a = C("alice", "pw1")
p1 = cyshine_pl("2", "Mixed", [("tx", "A"), ("wy", "B")])
st, b = req("PUT", "/CyShineMusic/sync-v1.json", cyshine([p1]), a)
print("1 put:", st, flush=True)
print("2 get:", req("GET", "/CyShineMusic/sync-v1.json", auth=a)[0], flush=True)
p2 = ceru_pl("2", "Mixed", [("tx", "A"), ("wy", "B")])
print("3 ceru put:", req("PUT", "/ceru/sync-v1.json", ceru([p2]), a)[0], flush=True)
st, b = req("GET", "/ceru/sync-v1.json", auth=a)
print("4 ceru get:", st, flush=True)
p3 = cyshine_pl("2", "Mixed", [("tx", "A")])
st, b = req("PUT", "/CyShineMusic/sync-v1.json", cyshine([p3]), a)
print("5 cyshine put del B:", st, json.dumps(b, ensure_ascii=False)[:400] if b else None, flush=True)
print("6 cyshine get:", req("GET", "/CyShineMusic/sync-v1.json", auth=a)[0], flush=True)
st, b = req("GET", "/ceru/sync-v1.json", auth=a)
print("7 ceru get:", st, [(p["id"], p["name"], [t["songId"] for t in p["tracks"]]) for p in b.get("playlists", [])], flush=True)
# /api/state
st, b = req("GET", "/api/state", auth=a)
print("8 state:", json.dumps(b, ensure_ascii=False)[:600] if b else None, flush=True)
server.should_exit = True
