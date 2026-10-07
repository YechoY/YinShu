import sys, base64, json, urllib.request, urllib.error
sys.path.insert(0, "src"); sys.path.insert(0, ".")
import threading, time, tempfile
import uvicorn
from hub import api
from hub.store import Hub
from tests.hub.helpers import cyshine_payload, ceru_payload

store = tempfile.mkdtemp(prefix="dbg06c_", dir="tmp")
hub = Hub(store)
api.configure(hub, {
    "alice": {"password": api._hash_pw("pw1"), "space": "main",
              "role": "admin", "enabled": True},
}, config_path=None)
port = 18997
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

a = C("alice", "pw1")
print("1 put:", req("PUT", "/CyShineMusic/sync-v1.json",
                    cyshine_payload([{"id": "2", "name": "Mixed",
                                      "tracks": [("tx", "A", "歌A"), ("wy", "B", "歌B")]}]), a)[0], flush=True)
print("2 get:", req("GET", "/CyShineMusic/sync-v1.json", auth=a)[0], flush=True)
print("3 ceru put:", req("PUT", "/ceru/sync-v1.json",
                         ceru_payload([{"id": "2", "name": "Mixed",
                                        "tracks": [("tx", "A", "歌A"), ("wy", "B", "歌B")]}]), a)[0], flush=True)
print("4 ceru get:", req("GET", "/ceru/sync-v1.json", auth=a)[0], flush=True)
st, b = req("PUT", "/CyShineMusic/sync-v1.json",
            cyshine_payload([{"id": "2", "name": "Mixed", "tracks": [("tx", "A", "歌A")]}]), a)
print("5 put del B:", st, flush=True)
print("6 get:", req("GET", "/CyShineMusic/sync-v1.json", auth=a)[0], flush=True)
st, b = req("GET", "/ceru/sync-v1.json", auth=a)
print("7 ceru get:", st, [(p["id"], p["name"], [t["songId"] for t in p["tracks"]]) for p in b.get("playlists", [])], flush=True)
server.should_exit = True
