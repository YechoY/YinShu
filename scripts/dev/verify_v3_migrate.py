"""第1层验证：v2 config -> configure -> v3 内存结构 + 落盘，pkl 不动。"""
import json, os, sys, tempfile, shutil
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from hub import api
from hub.store import Hub

root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
store = tempfile.mkdtemp(prefix="v3migrate_", dir=os.path.join(root, "tmp"))
cfg_path = os.path.join(store, "config.json")

# 模拟真实 v2 config（admin/Yecho/wife）
v2 = {
    "version": 2,
    "accounts": {
        "admin": {"password": api._hash_pw("admin123"), "space": "main", "role": "admin", "enabled": True, "created_at": "", "updated_at": ""},
        "Yecho": {"password": api._hash_pw("yy"), "space": "Yecho", "role": "admin", "enabled": True, "created_at": "2026-10-06T20:03:25+08:00", "updated_at": ""},
        "wife": {"password": api._hash_pw("ww"), "space": "main", "role": "user", "enabled": True, "created_at": "2026-10-06T20:45:44+08:00", "updated_at": ""},
    },
}
with open(cfg_path, "w", encoding="utf-8") as f:
    json.dump(v2, f, ensure_ascii=False)

# 用 run_fastapi.load_config 读 + configure
from hub.run_fastapi import load_config
users, spaces, members, invites, policy = load_config(cfg_path)
hub = Hub(os.path.join(store, "spaces"))
api.configure(hub, users, config_path=cfg_path, spaces=spaces, members=members, invites=invites, policy=policy)

print("== spaces =="); print(json.dumps(api._spaces, ensure_ascii=False, indent=1))
print("== members =="); print(json.dumps(api._members, ensure_ascii=False, indent=1))
print("== policy ==", api._policy)

# 不变量校验
assert "main" in api._spaces and "Yecho" in api._spaces
assert api._spaces["main"]["kind"] == "shared"
assert api._spaces["Yecho"]["kind"] == "personal"
assert api._role_in("admin", "main") == "owner"      # 兜底：main 无同名 owner -> 首个 admin
assert api._role_in("wife", "main") == "editor"
assert api._role_in("Yecho", "Yecho") == "owner"
assert api._role_in("wife", "Yecho") is None         # 非成员
assert api._role_in("bob", "main") is None
# 落盘 v3
disk = json.load(open(cfg_path, encoding="utf-8"))
assert disk["version"] == 3, disk.get("version")
assert set(disk.keys()) >= {"version", "accounts", "spaces", "members", "invites", "policy"}
# 密码仍哈希
assert disk["accounts"]["wife"]["password"].startswith("pbkdf2$")
# 账号 space 指针未变（同步行为不变）
assert disk["accounts"]["wife"]["space"] == "main"
assert disk["accounts"]["Yecho"]["space"] == "Yecho"
# bak 滚动文件
print("== config dir ==", sorted(os.listdir(store)))
print("MIGRATE OK")
shutil.rmtree(store, ignore_errors=True)
