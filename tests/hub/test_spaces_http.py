"""v3 空间与成员体系 HTTP 验收（docs/12，方案 §7 的 14 条）。

覆盖：v2→v3 迁移不破坏同步 · 自助建空间/配额/名校验 · viewer 只读 ·
邀请码加入与互通 · 邀请码四种失效 · 邀请注册 · 切换/退出 · 唯一 owner 保护 ·
移除成员重置空间 · 删空间需无其他成员 · 审计日志。

起线程内 uvicorn（端口段 18831，与 test_api_http 18731 / test_hub 18751 /
test_devices_http 18931 错开，避免同进程撞端口）。**一次只跑一个 pytest 进程**。
"""
from __future__ import annotations

import base64
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

import uvicorn

from hub import api
from hub.store import Hub

from tests.hub.helpers import cyshine_payload, rmtree_force  # noqa: E402


def _do(req):
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            return r.status, r.headers, (json.loads(body.decode("utf-8")) if body else None)
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, e.headers, json.loads(body.decode("utf-8")) if body else None
        except Exception:
            return e.code, e.headers, body


class C:
    def __init__(self, base, user, pw):
        self.base = base.rstrip("/")
        tok = base64.b64encode(f"{user}:{pw}".encode()).decode()
        self.auth = {"Authorization": f"Basic {tok}",
                     "Content-Type": "application/json; charset=utf-8"}

    def _req(self, method, path, payload=None, headers=None):
        h = dict(headers or self.auth)
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        return _do(req)

    def get(self, path):
        return self._req("GET", path)

    def post(self, path, payload=None):
        return self._req("POST", path, payload if payload is not None else {})

    def put(self, path, payload=None):
        return self._req("PUT", path, payload if payload is not None else {})

    def delete(self, path):
        return self._req("DELETE", path)

    def anon_post(self, path, payload):
        h = {"Content-Type": "application/json; charset=utf-8"}
        return self._req("POST", path, payload, headers=h)


def fresh_configure(store, policy=None):
    """标准双账号夹具：alice=admin/main，bob=user/other（各自空间的 owner）。"""
    hub = Hub(store)
    pol = policy or {}
    api.configure(hub, {
        "alice": {"password": api._hash_pw("pw1"), "space": "main",
                  "role": "admin", "enabled": True},
        "bob": {"password": api._hash_pw("pw2"), "space": "other",
                "role": "user", "enabled": True},
    }, config_path=None, policy=pol)
    return hub


class SpacesHttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        cls.port = _free_port()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.server = uvicorn.Server(uvicorn.Config(api.app, host="127.0.0.1",
                                                  port=cls.port, log_level="warning"))
        cls.t = threading.Thread(target=cls.server.run, daemon=True)
        cls.t.start()
        for _ in range(100):
            try:
                urllib.request.urlopen(f"{cls.base}/api/state", timeout=1)
                break
            except urllib.error.HTTPError:
                break
            except Exception:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.t.join(timeout=10)

    def setUp(self):
        self.store = tempfile.mkdtemp(prefix="spaces_http_", dir=os.path.join(self._root, "tmp"))
        fresh_configure(self.store)
        self.a = C(self.base, "alice", "pw1")
        self.b = C(self.base, "bob", "pw2")

    def tearDown(self):
        rmtree_force(self.store)

    # ---- 1：v2→v3 迁移不破坏同步，同名/首个账号是 owner ----
    def test_01_v2_config_migrates_and_sync_works(self):
        st, _, me = self.b.get("/api/me")
        self.assertEqual(st, 200)
        self.assertEqual(me["space"], "other")
        self.assertEqual(me["my_role"], "owner")
        # 迁移后同步行为正常：PUT 建基线 → GET 回环
        self.assertEqual(
            self.b.put("/CyShineMusic/sync-v1.json",
                       cyshine_payload([{"id": "p1", "name": "单", "tracks": []}]))[0],
            200)
        st2, _, body = self.b.get("/CyShineMusic/sync-v1.json")
        self.assertEqual(st2, 200)
        self.assertIn("p1", body["sections"]["playlists"]["data"][0]["id"])

    # ---- 2：普通账号建空间成为 owner，current=true 顺手切过去 ----
    def test_02_user_create_space_becomes_owner(self):
        st, _, body = self.b.post("/api/spaces", {"space": "fam"})
        self.assertEqual(st, 200, body)
        self.assertEqual(body["current"], True)
        st2, _, me = self.b.get("/api/me")
        self.assertEqual(me["space"], "fam")
        self.assertEqual(me["my_role"], "owner")
        st3, _, spaces = self.b.get("/api/spaces")
        names = {s["space"] for s in spaces["spaces"]}
        self.assertIn("fam", names)

    # ---- 3：配额 ----
    def test_03_create_space_quota(self):
        # bob 已是 other owner（1）；再建两个到上限 3
        self.assertEqual(self.b.post("/api/spaces", {"space": "b1"})[0], 200)
        self.assertEqual(self.b.post("/api/spaces", {"space": "b2", "current": False})[0], 200)
        st, _, body = self.b.post("/api/spaces", {"space": "b3", "current": False})
        self.assertEqual(st, 409)
        self.assertIn("上限", body["error"])
        # admin 不受配额限制
        self.assertEqual(self.a.post("/api/spaces", {"space": "a1", "current": False})[0], 200)
        self.assertEqual(self.a.post("/api/spaces", {"space": "a2", "current": False})[0], 200)

    # ---- 4：空间名校验 ----
    def test_04_create_space_name_validation(self):
        self.assertEqual(self.b.post("/api/spaces", {"space": "  "})[0], 400)
        self.assertEqual(self.b.post("/api/spaces", {"space": "../x"})[0], 400)
        self.assertEqual(self.b.post("/api/spaces", {"space": "space__x"})[0], 400)
        self.assertEqual(self.b.post("/api/spaces", {"space": "other"})[0], 409)  # 重名

    # ---- 5：viewer 只读（GET 放行，PUT/删歌单/确认卡 403） ----
    def test_05_viewer_cannot_write_but_can_read(self):
        self.a.post("/api/spaces", {"space": "home"})   # alice 切到 home，owner
        self.a.put("/CyShineMusic/sync-v1.json",
                   cyshine_payload([{"id": "p1", "name": "家", "tracks": [("tx", "1", "歌A")]}]))
        st, _, inv = self.a.post("/api/invites", {"space": "home", "role": "viewer"})
        self.assertEqual(st, 200, inv)
        st2, _, joined = self.b.post("/api/join", {"code": inv["code"]})
        self.assertEqual(st2, 200, joined)
        self.assertEqual(joined["role"], "viewer")
        # 拉取放行
        self.assertEqual(self.b.get("/CyShineMusic/sync-v1.json")[0], 200)
        # 写入全拦
        self.assertEqual(self.b.put("/CyShineMusic/sync-v1.json",
                                    cyshine_payload([]))[0], 403)
        self.assertEqual(self.b.delete("/api/playlists/p1")[0], 403)

    # ---- 6：邀请码加入后歌单互通 ----
    def test_06_invite_join_flow(self):
        self.a.post("/api/spaces", {"space": "home"})
        _, _, inv = self.a.post("/api/invites", {"space": "home", "role": "editor"})
        st, _, joined = self.b.post("/api/join", {"code": inv["code"]})
        self.assertEqual(st, 200, joined)
        self.assertEqual(joined["space"], "home")
        self.a.put("/CyShineMusic/sync-v1.json",
                   cyshine_payload([{"id": "p1", "name": "家", "tracks": [("tx", "1", "歌A")]}]))
        # editor 是可写客户端：首次先 PUT 本地视图建基线（栖弦真实流程；R1 保证首次空提交
        # 不会清掉云端已有、自己从未提交过的 p1），再 GET 才能 deliver
        st0, _, m0 = self.b.put("/CyShineMusic/sync-v1.json", cyshine_payload([]))
        self.assertIn(st0, (200, 201), m0)
        _, _, body = self.b.get("/CyShineMusic/sync-v1.json")
        ids = [pl["id"] for pl in body["sections"]["playlists"]["data"]]
        self.assertIn("p1", ids)

    # ---- 7：邀请码四种失效，文案可区分 ----
    def test_07_invite_failures(self):
        self.a.post("/api/spaces", {"space": "home"})
        # 不存在
        st, _, b0 = self.b.post("/api/join", {"code": "ZZZZZZZZ"})
        self.assertEqual(st, 400)
        self.assertIn("不存在", b0["error"])
        # 撤销
        _, _, inv = self.a.post("/api/invites", {"space": "home"})
        self.assertEqual(self.a.delete(f"/api/invites/{inv['code']}")[0], 200)
        st2, _, b1 = self.b.post("/api/join", {"code": inv["code"]})
        self.assertEqual(st2, 400)
        self.assertIn("撤销", b1["error"])
        # 过期（直接改内存）
        _, _, inv2 = self.a.post("/api/invites", {"space": "home"})
        api._invites[inv2["code"]]["expires_at"] = "2000-01-01T00:00:00+08:00"
        st3, _, b2 = self.b.post("/api/join", {"code": inv2["code"]})
        self.assertEqual(st3, 400)
        self.assertIn("过期", b2["error"])
        # 用尽（max_uses=1，先 join 一次）
        _, _, inv3 = self.a.post("/api/invites", {"space": "home", "max_uses": 1})
        self.assertEqual(self.b.post("/api/join", {"code": inv3["code"]})[0], 200)
        st4, _, b3 = self.b.post("/api/join", {"code": inv3["code"]})
        self.assertEqual(st4, 400)
        self.assertIn("用完", b3["error"])

    # ---- 8：viewer 邀请码加入即只读 ----
    def test_08_invite_role_viewer(self):
        self.a.post("/api/spaces", {"space": "home"})
        _, _, inv = self.a.post("/api/invites", {"space": "home", "role": "viewer"})
        self.assertEqual(self.b.post("/api/join", {"code": inv["code"]})[0], 200)
        self.assertEqual(self.b.put("/CyShineMusic/sync-v1.json",
                                    cyshine_payload([]))[0], 403)

    # ---- 9：凭邀请码自助注册 ----
    def test_09_register_with_invite_code(self):
        self.a.post("/api/spaces", {"space": "home"})
        _, _, inv = self.a.post("/api/invites", {"space": "home", "role": "editor"})
        # 新名字注册成功
        st, _, reg = self.b.anon_post("/api/register",
                                      {"name": "carol", "password": "password1", "code": inv["code"]})
        self.assertEqual(st, 200, reg)
        self.assertEqual(reg["space"], "home")
        # carol 能登录且在 home 可写
        c = C(self.base, "carol", "password1")
        self.assertEqual(c.put("/CyShineMusic/sync-v1.json",
                               cyshine_payload([]))[0], 200)
        # 密码不足 6 位 → 400
        st0, _, b0 = self.b.anon_post("/api/register",
                                      {"name": "carol", "password": "x", "code": inv["code"]})
        self.assertEqual(st0, 400)
        # 重名 → 409
        st2, _, b2 = self.b.anon_post("/api/register",
                                      {"name": "carol", "password": "password2", "code": inv["code"]})
        self.assertEqual(st2, 409)
        # 坏码 → 400
        st3, _, b3 = self.b.anon_post("/api/register",
                                      {"name": "dave", "password": "password3", "code": "BADCODE0"})
        self.assertEqual(st3, 400)
        # 关闭自助注册 → 403
        api._policy["allow_self_register"] = False
        st4, _, b4 = self.b.anon_post("/api/register",
                                      {"name": "erin", "password": "password4", "code": inv["code"]})
        self.assertEqual(st4, 403)

    # ---- 10：退出回到个人空间；切到非成员空间 403 ----
    def test_10_leave_and_switch(self):
        self.a.post("/api/spaces", {"space": "home"})
        _, _, inv = self.a.post("/api/invites", {"space": "home"})
        self.b.post("/api/join", {"code": inv["code"]})   # bob 当前 home
        _, _, me0 = self.b.get("/api/me")
        self.assertEqual(me0["space"], "home")
        # 切到非成员空间 → 403
        st, _, b1 = self.b.post("/api/me/space", {"space": "main"})
        self.assertEqual(st, 403)
        # 退出 home → 自动切回个人空间 bob
        st2, _, lv = self.b.post("/api/me/leave", {"space": "home"})
        self.assertEqual(st2, 200, lv)
        self.assertEqual(lv["current"], "bob")
        _, _, me = self.b.get("/api/me")
        self.assertEqual(me["space"], "bob")
        self.assertEqual(me["my_role"], "owner")

    # ---- 11：唯一 owner 不能退出/被降级 ----
    def test_11_last_owner_cannot_leave_or_be_demoted(self):
        # bob 是 other 唯一 owner
        st, _, b1 = self.b.post("/api/me/leave", {"space": "other"})
        self.assertEqual(st, 400)
        self.assertIn("唯一", b1["error"])
        # alice（全局 admin）把 other 的 bob 降为 viewer → 400
        st2, _, b2 = self.a.put("/api/spaces/other/members/bob", {"role": "viewer"})
        self.assertEqual(st2, 400)
        self.assertIn("唯一", b2["error"])

    # ---- 12：移除成员后其当前空间重置、对原空间写入 403 ----
    def test_12_remove_member_resets_their_space(self):
        self.a.post("/api/spaces", {"space": "home"})
        _, _, inv = self.a.post("/api/invites", {"space": "home"})
        self.b.post("/api/join", {"code": inv["code"]})   # bob 当前 home
        st, _, rm = self.a.delete("/api/spaces/home/members/bob")
        self.assertEqual(st, 200, rm)
        self.assertEqual(rm["current"], "bob")           # 切回个人空间
        # bob 不在 home 了：切不回去、写 home（当前已 bob）通过其他方式验证非成员
        st2, _, b2 = self.b.post("/api/me/space", {"space": "home"})
        self.assertEqual(st2, 403)

    # ---- 13：删空间需无其他成员；移除后 owner 可删 ----
    def test_13_delete_space_requires_no_other_members(self):
        self.a.post("/api/spaces", {"space": "home"})
        _, _, inv = self.a.post("/api/invites", {"space": "home"})
        self.b.post("/api/join", {"code": inv["code"]})   # home 有 alice+bob
        st, _, b1 = self.a.delete("/api/spaces/home")
        self.assertEqual(st, 400)
        self.assertIn("bob", b1["error"])
        # 移除 bob 后 alice 可删（自己是 owner、当前在 home → 删后切回个人空间 alice）
        self.assertEqual(self.a.delete("/api/spaces/home/members/bob")[0], 200)
        st2, _, b2 = self.a.delete("/api/spaces/home")
        self.assertEqual(st2, 200, b2)
        self.assertEqual(b2["deleted"], "home")

    # ---- 14：审计日志记录成员体系变更，/api/state 返回 ----
    def test_14_audit_log_records_membership_changes(self):
        self.a.post("/api/spaces", {"space": "home"})
        _, _, inv = self.a.post("/api/invites", {"space": "home"})
        self.b.post("/api/join", {"code": inv["code"]})
        self.a.delete("/api/spaces/home/members/bob")
        _, _, state = self.a.get("/api/state")
        actions = [r["action"] for r in state.get("audit", [])]
        for expect in ("space.create", "invite.create", "invite.join", "member.remove"):
            self.assertIn(expect, actions)
        # 审计文件落盘
        path = os.path.join(self.store, "_audit.jsonl")
        self.assertTrue(os.path.exists(path))

    # ---- 15（P0-1）：/api/state 必须有空间读鉴权 ----
    # 账号 space 被改坏成非成员空间 → GET /api/state 必须 403（不变量①之外的兜底门）
    def test_15_api_state_requires_membership_read(self):
        self.a.post("/api/spaces", {"space": "home"})
        # 直接改内存模拟"账号指向非成员空间"的异常数据
        api._users["bob"]["space"] = "home"
        st, _, b = self.b.get("/api/state")
        self.assertEqual(st, 403)
        self.assertIn("成员", b["error"])

    # ---- 16（P0-2a）：已在空间的成员重复 join 不消耗邀请码 ----
    def test_16_member_rejoin_does_not_consume_invite(self):
        self.a.post("/api/spaces", {"space": "home"})
        _, _, inv = self.a.post("/api/invites", {"space": "home", "max_uses": 2})
        self.assertEqual(self.b.post("/api/join", {"code": inv["code"]})[0], 200)
        st2, _, j2 = self.b.post("/api/join", {"code": inv["code"]})
        self.assertEqual(st2, 200, j2)
        self.assertTrue(j2.get("already_member"))
        _, _, invs = self.a.get("/api/invites")
        row = next((x for x in invs["invites"] if x["code"] == inv["code"]), None)
        self.assertIsNotNone(row)
        self.assertEqual(row["uses"], 1)      # 重复 join 未再消耗
        self.assertEqual(row["remaining"], 1) # 留给外面的人

    # ---- 17（P0-2b）：只读成员不能列出邀请码 ----
    def test_17_viewer_cannot_list_invites(self):
        self.a.post("/api/spaces", {"space": "home"})
        _, _, inv = self.a.post("/api/invites", {"space": "home", "role": "viewer"})
        self.assertEqual(self.b.post("/api/join", {"code": inv["code"]})[0], 200)
        st, _, b = self.b.get("/api/invites")
        self.assertEqual(st, 403)

    # ---- 18（P1-3）：建号指向不存在的共享空间 → 404（不静默新建） ----
    def test_18_admin_create_user_rejects_unknown_space(self):
        st, _, b = self.a.post("/api/users", {"name": "x1", "password": "password1",
                                              "space": "nowhere"})
        self.assertEqual(st, 404)
        self.assertIn("不存在", b["error"])
        # 不传 space → 默认同名个人空间，隐含允许
        st2, _, ok = self.a.post("/api/users", {"name": "x2", "password": "password1"})
        self.assertEqual(st2, 200, ok)
        self.assertEqual(ok["user"]["space"], "x2")

    # ---- 19（P1-5）：改名同步成员表 invited_by ----
    def test_19_rename_syncs_member_invited_by(self):
        self.a.post("/api/spaces", {"space": "home"})
        _, _, inv = self.a.post("/api/invites", {"space": "home", "role": "editor"})
        self.assertEqual(self.b.post("/api/join", {"code": inv["code"]})[0], 200)
        # alice 改名 alice2（邀请人/创建人同步迁移）
        st, _, b = self.a.put("/api/users/alice/rename", {"new_name": "alice2"})
        self.assertEqual(st, 200, b)
        c2 = C(self.base, "alice2", "pw1")
        _, _, state = c2.get("/api/state")
        bob_row = next((m for m in state.get("members", []) if m["name"] == "bob"), None)
        self.assertIsNotNone(bob_row)
        self.assertEqual(bob_row["invited_by"], "alice2")
        self.assertEqual(bob_row["role"], "editor")

    # ---- 20（P1-4）：审计先按空间过滤再取尾部 30 ----
    def test_20_audit_filtered_before_tail30(self):
        self.a.post("/api/spaces", {"space": "home"})
        # 35 条本空间记录 + 5 条其他空间记录（若先 tail30 再过滤，本空间只剩 25 条）
        for i in range(35):
            api._audit("x.home", "alice", "home", f"h{i}")
        for i in range(5):
            api._audit("x.other", "bob", "other", f"o{i}")
        _, _, state = self.a.get("/api/state")
        home_rows = [r for r in state.get("audit", []) if r.get("action", "").startswith("x.home")]
        other_rows = [r for r in state.get("audit", []) if r.get("action", "").startswith("x.other")]
        self.assertEqual(len(home_rows), 30)
        self.assertEqual(len(other_rows), 0)

    # ---- 21（P1-6 / 第三轮 §3.2）：幂等补齐异常成员关系时写中性审计标签 ----
    # 第三轮将 action 由 invariant.repair 改为 member.autofill：_persist_accounts→_ensure_v3 对
    # 正常建号也会触发补齐，写"不变量修复"会误导；自动填充是常态维护，故标签中性化。
    def test_21_invariant_repair_audited(self):
        hub2 = Hub(self.store)
        api.configure(hub2, {
            "dave": {"password": api._hash_pw("pw"), "space": "nowhere",
                     "role": "user", "enabled": True},
        }, config_path=None, policy={})
        # 补齐后 dave 是 nowhere 的 owner（行为不变），同时落一条 member.autofill
        self.assertEqual(api._role_in("dave", "nowhere"), "owner")
        path = os.path.join(self.store, "_audit.jsonl")
        self.assertTrue(os.path.exists(path))
        with open(path, "r", encoding="utf-8") as f:
            self.assertIn("member.autofill", f.read())

    # ---- 22（第三轮 §2.1）：owner 重命名：name 变、id 与 pkl 文件名不变 ----
    def test_22_owner_can_rename_space(self):
        self.a.post("/api/spaces", {"space": "home"})
        st, _, b = self.a.put("/api/spaces/home/rename", {"new_name": "family"})
        self.assertEqual(st, 200, b)
        self.assertEqual(b["space"], "home")
        self.assertEqual(b["name"], "family")
        _, _, spaces = self.a.get("/api/spaces")
        row = next(s for s in spaces["spaces"] if s["space"] == "home")
        self.assertEqual(row["name"], "family")
        self.assertEqual(row["space"], "home")
        # pkl 文件名（=id）不变
        self.assertTrue(os.path.exists(os.path.join(self.store, "space__home.pkl")))
        self.assertFalse(os.path.exists(os.path.join(self.store, "space__family.pkl")))

    # ---- 23（§2.2）：改名后同步照常、歌单数据不丢（客户端地址零影响） ----
    def test_23_rename_keeps_sync_working(self):
        self.a.post("/api/spaces", {"space": "home"})
        _, _, inv = self.a.post("/api/invites", {"space": "home"})
        self.b.post("/api/join", {"code": inv["code"]})
        self.a.put("/CyShineMusic/sync-v1.json",
                   cyshine_payload([{"id": "p1", "name": "家",
                                     "tracks": [("tx", "1", "歌A")]}]))
        self.assertEqual(self.a.put("/api/spaces/home/rename",
                                    {"new_name": "family"})[0], 200)
        # bob 首次先 PUT 本地视图建基线（栖弦真实流程），再 GET 验证歌单还在
        self.assertIn(self.b.put("/CyShineMusic/sync-v1.json",
                                 cyshine_payload([]))[0], (200, 201))
        st, _, body = self.b.get("/CyShineMusic/sync-v1.json")
        self.assertEqual(st, 200)
        ids = [pl["id"] for pl in body["sections"]["playlists"]["data"]]
        self.assertIn("p1", ids)
        # alice 写入照常
        self.assertEqual(self.a.put("/CyShineMusic/sync-v1.json",
                                    cyshine_payload([{"id": "p1", "name": "家",
                                                      "tracks": []}]))[0], 200)

    # ---- 24（§2.3）：重命名拒绝重名/非法 ----
    def test_24_rename_rejects_duplicate_or_invalid(self):
        self.a.post("/api/spaces", {"space": "home"})
        # 与既有 id 冲突（bob 的 other）
        st, _, b = self.a.put("/api/spaces/home/rename", {"new_name": "other"})
        self.assertEqual(st, 409)
        self.assertIn("占用", b["error"])
        # 与既有展示名冲突
        self.assertEqual(self.a.post("/api/spaces", {"space": "fam", "current": False})[0], 200)
        st2, _, b2 = self.a.put("/api/spaces/home/rename", {"new_name": "fam"})
        self.assertEqual(st2, 409)
        # 非法名
        self.assertEqual(self.a.put("/api/spaces/home/rename", {"new_name": "../x"})[0], 400)
        self.assertEqual(self.a.put("/api/spaces/home/rename", {"new_name": "  "})[0], 400)
        self.assertEqual(self.a.put("/api/spaces/home/rename", {"new_name": "space__x"})[0], 400)

    # ---- 25（§2.4）：重命名需 owner ----
    def test_25_rename_requires_owner(self):
        self.a.post("/api/spaces", {"space": "home"})
        # bob 非 home 成员 → 403
        st, _, b = self.b.put("/api/spaces/home/rename", {"new_name": "x"})
        self.assertEqual(st, 403)
        # 空间不存在 → 404
        st2, _, b2 = self.a.put("/api/spaces/nosuch/rename", {"new_name": "x"})
        self.assertEqual(st2, 404)

    # ---- 26（§2.5）：路由接受 id 或展示名，结果一致 ----
    def test_26_routes_accept_id_or_name(self):
        self.a.post("/api/spaces", {"space": "home"})
        _, _, inv = self.a.post("/api/invites", {"space": "home"})
        self.b.post("/api/join", {"code": inv["code"]})
        self.assertEqual(self.a.put("/api/spaces/home/rename",
                                    {"new_name": "family"})[0], 200)
        st1, _, m1 = self.a.get("/api/spaces/family/members")
        self.assertEqual(st1, 200)
        st2, _, m2 = self.a.get("/api/spaces/home/members")
        self.assertEqual(st2, 200)
        self.assertEqual(m1["members"], m2["members"])
        self.assertEqual(m1["space_name"], "family")
        self.assertEqual(m2["space_name"], "family")

    # ---- 27（§1.3 C2）：新建空间拒绝展示名冲突（防止劫持 _find_space）----
    def test_post_spaces_rejects_display_name_conflict(self):
        self.a.post("/api/spaces", {"space": "home"})
        self.assertEqual(self.a.put("/api/spaces/home/rename",
                                    {"new_name": "family"})[0], 200)
        # alice 自己新建 family → 409（展示名已被占用）
        st, _, body = self.a.post("/api/spaces", {"space": "family", "current": False})
        self.assertEqual(st, 409)
        # bob 新建 family → 同样 409（任何人）
        st2, _, body2 = self.b.post("/api/spaces", {"space": "family", "current": False})
        self.assertEqual(st2, 409)
        # 新建真实不冲突的名字照常成功
        self.assertEqual(self.a.post("/api/spaces", {"space": "fresh", "current": False})[0], 200)

    # ---- 28（§1.3 C3）：/api/me/space、/api/me/leave 接受展示名 ----
    def test_me_space_accepts_display_name(self):
        self.a.post("/api/spaces", {"space": "home"})
        _, _, inv = self.a.post("/api/invites", {"space": "home"})
        self.b.post("/api/join", {"code": inv["code"]})
        self.assertEqual(self.a.put("/api/spaces/home/rename",
                                    {"new_name": "family"})[0], 200)
        # 切换：传展示名 family（bob 已是成员）→ 200 且切到稳定 id
        st, _, body = self.b.post("/api/me/space", {"space": "family"})
        self.assertEqual(st, 200, body)
        self.assertEqual(body["space"], "home")
        # 退出：传展示名 → 200，当前切回个人空间（账号同名）
        st2, _, body2 = self.b.post("/api/me/leave", {"space": "family"})
        self.assertEqual(st2, 200, body2)
        self.assertEqual(body2["current"], "bob")

    # ---- 29（§4.1 端到端）：改名+删除/恢复后 /api/state 无待确认卡 ----
    def test_state_endpoint_after_rename_and_restore_no_pending_cards(self):
        self.a.post("/api/spaces", {"space": "home"})
        self.assertEqual(self.a.put("/api/spaces/home/rename",
                                    {"new_name": "family"})[0], 200)
        # alice 写一首歌（栖弦），bob 加入并同步
        self.assertEqual(self.a.put("/CyShineMusic/sync-v1.json",
                                    cyshine_payload([{"id": "p1", "name": "家",
                                                      "tracks": [("tx", "1", "歌A")]}]))[0], 200)
        _, _, inv = self.a.post("/api/invites", {"space": "home"})
        self.b.post("/api/join", {"code": inv["code"]})
        self.assertEqual(self.b.put("/CyShineMusic/sync-v1.json",
                                    cyshine_payload([{"id": "p1", "name": "家",
                                                      "tracks": [("tx", "1", "歌A")]}]))[0], 200)
        # bob 删除歌 → 直删（互信；D33：无 pending 通道）
        self.b.get("/CyShineMusic/sync-v1.json")
        self.assertEqual(self.b.put("/CyShineMusic/sync-v1.json",
                                    cyshine_payload([]))[0], 200)
        # bob 看过删除视图后重加 → D34：加回来就加回来（无冷静期压制）
        self.b.get("/CyShineMusic/sync-v1.json")
        self.assertEqual(self.b.put("/CyShineMusic/sync-v1.json",
                                    cyshine_payload([{"id": "p1", "name": "家",
                                                      "tracks": [("tx", "1", "歌A")]}]))[0], 200)
        _, _, body = self.b.get("/CyShineMusic/sync-v1.json")
        ids = [pl["id"] for pl in body["sections"]["playlists"]["data"]]
        self.assertIn("p1", ids)   # D34：加回来就恢复


if __name__ == "__main__":
    unittest.main(verbosity=2)


def _free_port() -> int:
    """向系统要一个空闲端口。

    第十二轮（测试基建）：原实现是 `18xxx + (os.getpid() % 100)` 的固定基址，
    而本机 18787 上常驻着 DSH 的亿级上下文代理（billion-context）。某个 shell 的
    PID%100 恰好撞上基址偏移时，uvicorn 线程起不来（winerror 10048），整卷跑就
    表现为"偶发失败/单跑却通过"的假失败。改成向系统要端口，彻底避免撞车。
    """
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
