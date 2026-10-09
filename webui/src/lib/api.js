/* hub REST API 封装 */
/* 统一响应处理：401（凭据失效/未认证）抛 ApiError(status=401, message="登录已失效，请重新登录")，
   前端据此显示可读文案/清 token/弹站内重登录，绝不触发浏览器原生 Basic 认证框；
   其余错误抛服务端 error 文案或 HTTP 状态码。 */
export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function parseOrThrow(r) {
  const data = await r.json().catch(() => ({}));
  if (r.status === 401) throw new ApiError("登录已失效，请重新登录", 401);
  if (!r.ok) throw new ApiError(data.error || "HTTP " + r.status, r.status);
  return data;
}

export async function fetchStateRaw(token) {
  const r = await fetch("/api/state", {
    headers: { Authorization: "Basic " + token },
  });
  return parseOrThrow(r);
}

/* 已配置账号列表（不含密码） */
export async function fetchUsersRaw(token) {
  const r = await fetch("/api/users", {
    headers: { Authorization: "Basic " + token },
  });
  return parseOrThrow(r);
}

/* 账号管理（管理员） */
export async function createUser(token, name, password, space, role) {
  const r = await fetch("/api/users", {
    method: "POST",
    headers: { Authorization: "Basic " + token, "Content-Type": "application/json" },
    body: JSON.stringify({ name, password, space: space || undefined, role: role || "user" }),
  });
  return parseOrThrow(r);
}

export async function deleteUser(token, name, purge) {
  const qs = purge === true ? "?purge=true" : "";
  const r = await fetch("/api/users/" + encodeURIComponent(name) + qs, {
    method: "DELETE",
    headers: { Authorization: "Basic " + token },
  });
  return parseOrThrow(r);
}

/* 空间管理（多账户共用歌单）：admin 拿全部空间，普通账号只拿自己那一个 */
export async function getSpaces(token) {
  const r = await fetch("/api/spaces", {
    headers: { Authorization: "Basic " + token },
  });
  return parseOrThrow(r);
}

/* 切换账号空间：目标空间必须已存在（404 = 不存在；403 = 非管理员改他人；400 = 空间为空） */
export async function updateUserSpace(token, name, space) {
  const r = await fetch("/api/users/" + encodeURIComponent(name) + "/space", {
    method: "PUT",
    headers: { Authorization: "Basic " + token, "Content-Type": "application/json" },
    body: JSON.stringify({ space }),
  });
  return parseOrThrow(r);
}

/* 设置账号建空间配额（仅管理员）：spaceQuota 为数字或 null(=跟随全局) */
export async function setUserQuota(token, name, spaceQuota) {
  const r = await fetch("/api/users/" + encodeURIComponent(name) + "/quota", {
    method: "PUT",
    headers: { Authorization: "Basic " + token, "Content-Type": "application/json" },
    body: JSON.stringify({ space_quota: spaceQuota }),
  });
  return parseOrThrow(r);
}

/* 删除孤儿空间（仅管理员；有账号指向的空间后端会 400，请先删账号） */
export async function deleteSpace(token, name) {
  const r = await fetch("/api/spaces/" + encodeURIComponent(name), {
    method: "DELETE",
    headers: { Authorization: "Basic " + token },
  });
  return parseOrThrow(r);
}

/* 新建空间（登录即可，受配额；创建者即 owner，current=true 建完切过去；409=重名/超配额） */
export async function createSpace(token, name, current = true) {
  const r = await fetch("/api/spaces", {
    method: "POST",
    headers: { Authorization: "Basic " + token, "Content-Type": "application/json" },
    body: JSON.stringify({ space: name, current }),
  });
  return parseOrThrow(r);
}

/* ================= v3 空间与成员体系 ================= */

/* 当前登录账号身份 + 空间清单 + 策略（前端启动用） */
export async function getMe(token) {
  const r = await fetch("/api/me", { headers: { Authorization: "Basic " + token } });
  return parseOrThrow(r);
}

/* 切换我的当前空间（必须已是成员；403=非成员） */
export async function switchSpace(token, space) {
  const r = await fetch("/api/me/space", {
    method: "POST",
    headers: { Authorization: "Basic " + token, "Content-Type": "application/json" },
    body: JSON.stringify({ space }),
  });
  return parseOrThrow(r);
}

/* 退出空间（唯一 owner 不能退；退当前空间自动切回个人空间） */
export async function leaveSpace(token, space) {
  const r = await fetch("/api/me/leave", {
    method: "POST",
    headers: { Authorization: "Basic " + token, "Content-Type": "application/json" },
    body: JSON.stringify({ space }),
  });
  return parseOrThrow(r);
}

/* 某空间成员列表（成员可读） */
export async function getMembers(token, space) {
  const r = await fetch("/api/spaces/" + encodeURIComponent(space) + "/members", {
    headers: { Authorization: "Basic " + token },
  });
  return parseOrThrow(r);
}

/* 修改成员角色（owner；role=editor|viewer；不能降最后 owner） */
export async function setMemberRole(token, space, account, role) {
  const r = await fetch(
    "/api/spaces/" + encodeURIComponent(space) + "/members/" + encodeURIComponent(account),
    {
      method: "PUT",
      headers: { Authorization: "Basic " + token, "Content-Type": "application/json" },
      body: JSON.stringify({ role }),
    },
  );
  return parseOrThrow(r);
}

/* 移除成员（owner；被移除者自动切回个人空间） */
export async function removeMember(token, space, account) {
  const r = await fetch(
    "/api/spaces/" + encodeURIComponent(space) + "/members/" + encodeURIComponent(account),
    { method: "DELETE", headers: { Authorization: "Basic " + token } },
  );
  return parseOrThrow(r);
}

/* 生成邀请码（owner/admin；policy 开时 editor 也可）
 * payload: {space?, role:"editor"|"viewer", expires_in_hours, max_uses, note} */
export async function createInvite(token, payload) {
  const r = await fetch("/api/invites", {
    method: "POST",
    headers: { Authorization: "Basic " + token, "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  return parseOrThrow(r);
}

/* 邀请码列表（owner 看全部；editor 仅自己创建的） */
export async function getInvites(token, space) {
  const qs = space ? "?space=" + encodeURIComponent(space) : "";
  const r = await fetch("/api/invites" + qs, {
    headers: { Authorization: "Basic " + token },
  });
  return parseOrThrow(r);
}

/* 撤销邀请码（创建者/owner/admin） */
export async function revokeInvite(token, code) {
  const r = await fetch("/api/invites/" + encodeURIComponent(code), {
    method: "DELETE",
    headers: { Authorization: "Basic " + token },
  });
  return parseOrThrow(r);
}

/* 凭邀请码加入空间（登录账号；加入即切到该空间） */
export async function joinByCode(token, code) {
  const r = await fetch("/api/join", {
    method: "POST",
    headers: { Authorization: "Basic " + token, "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  });
  return parseOrThrow(r);
}

/* 自助注册（无需登录；邀请码即注册许可）。后端不校验 Authorization 头，故不带。 */
export async function register(name, password, code) {
  const r = await fetch("/api/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, password, code }),
  });
  return parseOrThrow(r);
}

/* 修改密码：管理员改任意账号（不验旧密）；普通账号改自己需传旧密码 */
export async function changePassword(token, name, password, oldPassword) {
  const r = await fetch("/api/users/" + encodeURIComponent(name) + "/password", {
    method: "PUT",
    headers: { Authorization: "Basic " + token, "Content-Type": "application/json" },
    body: JSON.stringify({ password, old_password: oldPassword || undefined }),
  });
  return parseOrThrow(r);
}

/* 启用/禁用账号（管理员） */
export async function setUserEnabled(token, name, enabled) {
  const r = await fetch("/api/users/" + encodeURIComponent(name) + "/enabled", {
    method: "PUT",
    headers: { Authorization: "Basic " + token, "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  return parseOrThrow(r);
}

/* 重命名账号（管理员）：空间与数据保留 */
export async function renameUser(token, name, newName) {
  const r = await fetch("/api/users/" + encodeURIComponent(name) + "/rename", {
    method: "PUT",
    headers: { Authorization: "Basic " + token, "Content-Type": "application/json" },
    body: JSON.stringify({ new_name: newName }),
  });
  return parseOrThrow(r);
}

/* 重命名空间（展示名；owner/管理员）：id 不变，客户端地址零影响 */
export async function renameSpace(token, name, newName) {
  const r = await fetch("/api/spaces/" + encodeURIComponent(name) + "/rename", {
    method: "PUT",
    headers: { Authorization: "Basic " + token, "Content-Type": "application/json" },
    body: JSON.stringify({ new_name: newName }),
  });
  return parseOrThrow(r);
}

/* 空间备份槽位列表（owner/管理员）：current/bak1/bak2 各带摘要 */
export async function getSpaceBackups(token, name) {
  const r = await fetch("/api/spaces/" + encodeURIComponent(name) + "/backups", {
    headers: { Authorization: "Basic " + token },
  });
  return parseOrThrow(r);
}

/* 从备份槽位恢复空间数据（owner/管理员）：文件互换可逆，恢复后各端下次同步只增不删 */
export async function restoreSpaceBackup(token, name, slot) {
  const r = await fetch("/api/spaces/" + encodeURIComponent(name) +
    "/backups/" + encodeURIComponent(slot) + "/restore", {
    method: "POST",
    headers: { Authorization: "Basic " + token },
  });
  return parseOrThrow(r);
}

/* 修改全局策略（仅管理员）：如 { member_invite: true } */
export async function updatePolicy(token, patch) {
  const r = await fetch("/api/policy", {
    method: "PUT",
    headers: { Authorization: "Basic " + token, "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  return parseOrThrow(r);
}

/* 调整角色（管理员）：admin | user */
export async function setUserRole(token, name, role) {
  const r = await fetch("/api/users/" + encodeURIComponent(name) + "/role", {
    method: "PUT",
    headers: { Authorization: "Basic " + token, "Content-Type": "application/json" },
    body: JSON.stringify({ role }),
  });
  return parseOrThrow(r);
}

/* 歌单管理（当前账号空间） */
export async function reorderPlaylists(token, plIds) {
  const r = await fetch("/api/playlists/reorder", {
    method: "POST",
    headers: { Authorization: "Basic " + token, "Content-Type": "application/json" },
    body: JSON.stringify({ pl_ids: plIds }),
  });
  return parseOrThrow(r);
}

export async function deletePlaylist(token, plId) {
  const r = await fetch("/api/playlists/" + encodeURIComponent(plId), {
    method: "DELETE",
    headers: { Authorization: "Basic " + token },
  });
  return parseOrThrow(r);
}

/* 歌单内歌曲排序/删除 */
export async function reorderTracks(token, plId, keys) {
  const r = await fetch("/api/playlists/" + encodeURIComponent(plId) + "/tracks/reorder", {
    method: "POST",
    headers: { Authorization: "Basic " + token, "Content-Type": "application/json" },
    body: JSON.stringify({ keys }),
  });
  return parseOrThrow(r);
}

export async function deleteTrack(token, plId, key) {
  const r = await fetch(
    "/api/playlists/" + encodeURIComponent(plId) + "/tracks/" + encodeURIComponent(key),
    { method: "DELETE", headers: { Authorization: "Basic " + token } },
  );
  return parseOrThrow(r);
}

/* ---- 格式化 ---- */
export function pad(n) { return String(n).padStart(2, "0"); }

export function fmtTime(iso) {
  if (!iso) return "–";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "–";
  return pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + " " +
    pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
}

export function fmtClock() {
  const d = new Date();
  return pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
}

export function fmtDur(ms) {
  if (!Number.isFinite(ms)) return "–";
  const s = Math.round(ms / 1000);
  return Math.floor(s / 60) + ":" + pad(s % 60);
}

export function qualityClass(q) {
  const qs = String(q || "").toLowerCase();
  if (qs === "master") return "q-master";
  if (["atmos_plus", "atmos"].includes(qs)) return "q-atmos";
  if (qs === "hires") return "q-hires";
  if (["flac24bit", "flac"].includes(qs)) return "q-flac";
  if (qs === "320k") return "q-320k";
  if (qs === "192k") return "q-192k";
  if (qs === "128k") return "q-128k";
  return "q-other";
}
