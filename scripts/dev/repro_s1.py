# -*- coding: utf-8 -*-
"""复核 check_sync.py S1/S4/S6 失败根因：delete 是否真删磁盘、首次 GET 是否 404。"""
import base64
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

ROOT = r"C:\Users\Echo\Desktop\Workspace\Music\playlist-sync-hub"
sys.path.insert(0, os.path.join(ROOT, "src"))
os.chdir(ROOT)
STORE = os.path.join(ROOT, "tmp")

from hub.store import Hub
from hub import api
import uvicorn

# 先看 tmp 下现有 pkl / 索引
idx_path = os.path.join(STORE, "index.json")
if os.path.exists(idx_path):
    print("index.json 内容:", open(idx_path, encoding="utf-8").read()[:500])
for f in os.listdir(STORE):
    if f.endswith(".pkl"):
        print("  pkl:", f)

hub = Hub(STORE)
print("delete 前 known:", hub._known)
ok = hub.delete_space("chk_sync")
print("delete_space 返回:", ok)
p = os.path.join(STORE, "spaces", "chk_sync.pkl")
print("chk_sync.pkl 存在:", os.path.exists(p), "路径:", p)
hub = Hub(STORE)
print("delete 后 known:", hub._known)

api.configure(hub, {"alice": {"password": api._hash_pw("pw1"), "space": "chk_sync",
                              "role": "admin", "enabled": True}}, config_path=None)
PORT = 8799
srv = uvicorn.Server(uvicorn.Config(api.app, host="127.0.0.1", port=PORT, log_level="error"))
threading.Thread(target=srv.run, daemon=True).start()
BASE = f"http://127.0.0.1:{PORT}"
for _ in range(100):
    try:
        urllib.request.urlopen(BASE + "/api/state", timeout=1)
        break
    except Exception:
        time.sleep(0.15)
AUTH = {"Authorization": "Basic " + base64.b64encode(b"alice:pw1").decode()}

def get(path):
    r = urllib.request.Request(BASE + path, headers=AUTH)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, resp.read().decode()[:200]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]

st, body = get("/CyShineMusic/sync-v1.json")
print("栖弦首次 GET:", st, body[:120])
st2, body2 = get("/ceru/sync-v1.json")
print("澜音首次 GET:", st2, body2[:120])
