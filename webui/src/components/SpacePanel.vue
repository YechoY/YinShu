<script setup>
/* v3 空间与成员面板（放进个人中心，按 tab 分块）：
   tab="spaces"  我的空间：卡片（首字色块/成员头像/角色徽标）+ 新建/邀请码加入 + 切换/退出/删除
                 下方内嵌「空间管理」：选空间 → 成员表 + 邀请码（与卡片同页，不跳页签）
   tab="orphan"  孤儿空间（仅全局 admin）：删除
   所有增删改一律自定义 dialog；报错固定在 dialog 内部；不用浏览器原生 confirm/prompt。 */
import { ref, computed, onMounted, watch } from "vue";
import {
  getMe, getSpaces, createSpace, deleteSpace, switchSpace, leaveSpace,
  getMembers, setMemberRole, removeMember,
  createInvite, getInvites, revokeInvite, joinByCode,
  updateUserSpace, fetchUsersRaw, fmtTime, renameSpace, updatePolicy,
  getSpaceBackups, restoreSpaceBackup,
} from "../lib/api";

const props = defineProps({
  token: { type: String, required: true },
  me: { type: String, default: "" },
  tab: { type: String, default: "spaces" },   // "spaces" | "orphan"
  manageSpace: { type: String, default: "" }, // 空间管理当前选中的空间 id（由个人中心控制）
});
const emit = defineEmits(["changed", "go-manage"]);

const meData = ref(null);
const allSpaces = ref([]);
const users = ref([]);        // 全部账号（admin 添加成员用）
const members = ref([]);
const invites = ref([]);
/* 只展示有效的邀请码：已撤销/已过期/已失效的一律隐藏，列表保持干净 */
const activeInvites = computed(() =>
  invites.value.filter(i => !i.disabled && !i.invalid_reason));
const err = ref("");
const okMsg = ref("");
const busy = ref(false);
const dialog = ref("");       // "" | "create" | "join" | "role" | "remove-member"
                              //    | "revoke" | "add-member" | "leave" | "del-space"
                              //    | "restore"
/* 表单 */
const newName = ref("");
const joinCode = ref("");
const invRole = ref("editor");
const invHours = ref(24);
const invUses = ref(1);
const invNote = ref("");
const lastCreatedCode = ref("");
const roleTarget = ref(null);
const memberTarget = ref(null);
const leaveTarget = ref(null);
const spaceTarget = ref(null);
const revokeTarget = ref(null);
const addTarget = ref("");
const addRole = ref("editor");
/* 备份与恢复：槽位列表 + 待恢复目标 */
const backups = ref([]);
const restoreTarget = ref(null);   // {slot, ...摘要}

const ROLE_LABEL = { owner: "管理员", editor: "可编辑", viewer: "只读" };
const roleLabel = (r) => ROLE_LABEL[r] || r || "";

const isAdmin = computed(() => !!meData.value?.is_admin);
const mySpaces = computed(() => meData.value?.spaces || []);
const policy = computed(() => meData.value?.policy || {});
/* 第三轮 §3.4：成员管理需 owner/admin；生成邀请码则 owner/admin 或（editor + policy.member_invite） */
const canManage = (s) => !!s && (s.my_role === "owner" || isAdmin.value);
const canInvite = (s) => !!s && (canManage(s) || (s.my_role === "editor" && policy.value.member_invite));
/* 空间管理块可见性：我所在的全部空间（含只读成员——只读也能看成员表，只是不能邀请/管理） */
const manageBlockSpaces = computed(() => mySpaces.value);
const orphanSpaces = computed(() => allSpaces.value.filter((s) => s.orphan));
const manageInfo = computed(
  () => mySpaces.value.find((s) => s.space === props.manageSpace) || null);
/* 空间显示名：展示 name，回退 id（第三轮 §2） */
const spLabel = (s) => (s && s.name) ? s.name : (typeof s === "string" ? s : (s && s.space) || "");
const spaceLabelOf = (id) => {
  const s = allSpaces.value.find((x) => x.space === id);
  return spLabel(s) || id;
};
/* 能否退出：我是该空间唯一 owner（唯一管理员）不能退；其余都可退（含个人空间的非 owner 成员） */
function canLeave(s) {
  if (!s) return false;
  if (s.my_role === "owner") {
    const owners = (s.members || []).filter((m) => m.role === "owner").length;
    if (owners <= 1) return false;
  }
  return true;
}
/* 可添加的已有账号：启用中、且尚不是该空间成员 */
const addableUsers = computed(() => users.value.filter(
  (u) => u.enabled && !members.value.some((m) => m.name === u.name)));

/* 空间首字色块：按空间名取稳定渐变 */
const AVATAR_PALETTES = [
  ["#4a6fa5", "#6b8cae"], ["#85cdca", "#5f9d8f"], ["#e8a87c", "#d4a373"],
  ["#c38d94", "#c0656f"], ["#d4a373", "#85cdca"], ["#7ea3c4", "#4a6fa5"],
  ["#b07a4a", "#e8a87c"], ["#8a7fb0", "#c38d94"],
];
function avatarStyle(name) {
  let h = 0;
  for (const c of String(name || "")) h = (h * 31 + (c.codePointAt(0) || 0)) >>> 0;
  const [a, b] = AVATAR_PALETTES[h % AVATAR_PALETTES.length];
  return { background: `linear-gradient(135deg, ${a}, ${b})` };
}

async function load() {
  try {
    const [m, sp] = await Promise.all([getMe(props.token), getSpaces(props.token)]);
    meData.value = m;
    allSpaces.value = sp.spaces || [];
  } catch (e) {
    err.value = "加载空间信息失败：" + e.message;
  }
}

/* 第四轮 P0-7：loadManage 请求序号守卫——连点空间 chip 时丢弃过期响应，避免数据串空间 */
let manageSeq = 0;
async function loadManage() {
  const seq = ++manageSeq;
  if (!props.manageSpace) { members.value = []; invites.value = []; return; }
  try {
    /* 成员表对空间内所有成员可见（owner/editor/viewer 都能看有哪些成员）；
       改角色/移除等管理操作仍仅 owner/admin（canManage 控制操作按钮显隐，后端也会 403） */
    const [mm, ii] = await Promise.all([
      getMembers(props.token, props.manageSpace),
      getInvites(props.token, props.manageSpace).catch(() => ({ invites: [] })),
    ]);
    if (seq !== manageSeq) return;
    members.value = mm.members || [];
    invites.value = ii.invites || [];
    /* 备份槽位仅 owner/admin 可见；非 owner 403 时静默忽略 */
    getSpaceBackups(props.token, props.manageSpace)
      .then((r) => { if (seq === manageSeq) backups.value = r.slots || []; })
      .catch(() => { if (seq === manageSeq) backups.value = []; });
  } catch (e) {
    if (seq !== manageSeq) return;
    err.value = e.message;
  }
}

/* ---- 备份与恢复 ---- */
function fmtSize(n) {
  if (n == null) return "–";
  if (n < 1024) return n + " B";
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
  return (n / 1024 / 1024).toFixed(2) + " MB";
}
const slotLabel = { current: "当前数据", bak1: "备份 1（上一次）", bak2: "备份 2（更早）" };
function askRestore(slotInfo) {
  if (!slotInfo || !slotInfo.exists || slotInfo.slot === "current") return;
  if (slotInfo.error) return;
  restoreTarget.value = slotInfo;
  err.value = ""; okMsg.value = "";
  dialog.value = "restore";
}
async function confirmRestore() {
  const t = restoreTarget.value;
  if (!t) return;
  if (busy.value) return;
  busy.value = true;
  try {
    await restoreSpaceBackup(props.token, props.manageSpace, t.slot);
    closeDialog();
    flash(t.slot === "bak1"
      ? "已恢复到上一次状态（恢复前的数据现在在备份 1，再恢复一次即可撤销）"
      : "已恢复到更早的备份（恢复前的数据保存在备份 1）");
    /* 刷新备份列表与成员/邀请码（恢复后基线已重置，revision 等会变） */
    const r = await getSpaceBackups(props.token, props.manageSpace).catch(() => null);
    if (r) backups.value = r.slots || [];
    loadManage();
    emit("changed");
  } catch (e) { err.value = e.message; } finally { busy.value = false; }
}

function openDialog(name) {
  err.value = ""; okMsg.value = "";
  if (name === "create") newName.value = "";
  if (name === "join") joinCode.value = "";
  dialog.value = name;
}
function closeDialog() {
  dialog.value = "";
  roleTarget.value = null; memberTarget.value = null;
  leaveTarget.value = null; spaceTarget.value = null;
  revokeTarget.value = null; addTarget.value = "";
  err.value = ""; okMsg.value = "";
}
function flash(msg) { okMsg.value = msg; setTimeout(() => { if (okMsg.value === msg) okMsg.value = ""; }, 2600); }

/* ---- 空间管理（内嵌在我的空间页签：成员 + 邀请码） ---- */
function openManage(s) {
  emit("go-manage", s.space);   // 交给个人中心：记录选中空间（不切页签，管理面板就在本页下方）
}
/* 唯一管理员点了「退出」→ 给明确提示（而不是按钮无反应） */
function openLeaveHint(s) {
  leaveTarget.value = s; err.value = ""; okMsg.value = "";
  dialog.value = "leave-hint";
}
/* 空间管理页签内切空间 */
function pickManage(sp) {
  if (sp === props.manageSpace) return;
  members.value = []; invites.value = [];
  lastCreatedCode.value = "";
  err.value = ""; okMsg.value = "";
  emit("go-manage", sp);
}
/* 选中空间变化 → 加载成员与邀请码 */
watch(
  () => props.manageSpace,
  (nv) => {
    if (!nv) { members.value = []; invites.value = []; backups.value = []; return; }
    members.value = []; invites.value = []; backups.value = [];
    lastCreatedCode.value = "";
    err.value = ""; okMsg.value = "";
    loadManage();
  }
);

/* ---- 我的空间：切换 / 退出 / 删除 ---- */
async function doSwitch(s) {
  if (s.is_current) return;
  busy.value = true; err.value = "";
  try {
    await switchSpace(props.token, s.space);
    await load();
    emit("changed");
    flash("已切换到空间 " + spLabel(s));
  } catch (e) { err.value = e.message; } finally { busy.value = false; }
}
function askLeave(s) { leaveTarget.value = s; err.value = ""; okMsg.value = ""; dialog.value = "leave"; }
async function confirmLeave() {
  const s = leaveTarget.value;
  if (!s) return;
  if (busy.value) return;   // 第四轮 P0-4
  busy.value = true;
  try {
    const r = await leaveSpace(props.token, s.space);
    closeDialog();
    await load();
    emit("changed");
    flash("已退出 " + spLabel(s) + "，当前空间 " + (r.current || ""));
  } catch (e) { err.value = e.message; } finally { busy.value = false; }
}
function askDeleteSpace(s) { spaceTarget.value = s; err.value = ""; okMsg.value = ""; dialog.value = "del-space"; }
async function confirmDeleteSpace() {
  const s = spaceTarget.value;
  if (!s) return;
  if (busy.value) return;   // 第四轮 P0-4
  busy.value = true;
  try {
    await deleteSpace(props.token, s.space);
    closeDialog();
    await load();
    emit("changed");
    flash("已删除空间 " + spLabel(s));
  } catch (e) { err.value = e.message; } finally { busy.value = false; }
}

/* ---- 新建空间 ---- */
async function confirmCreate() {
  const name = (newName.value || "").trim();
  if (!name) { err.value = "空间名不能为空"; return; }
  if (busy.value) return;   // 第四轮 P0-4：防连按回车重复提交
  busy.value = true;
  try {
    await createSpace(props.token, name, true);
    closeDialog();
    await load();
    emit("changed");
    flash("已创建并切换到空间 " + name);
  } catch (e) { err.value = e.message; } finally { busy.value = false; }
}

/* ---- 邀请码：加入 / 生成（manage 弹窗内嵌）/ 复制 / 撤销 ---- */
async function doJoin() {
  const code = (joinCode.value || "").trim();
  if (!code) { err.value = "请输入邀请码"; return; }
  if (busy.value) return;   // 第四轮 P0-4
  busy.value = true;
  try {
    const r = await joinByCode(props.token, code);
    closeDialog();
    await load();
    emit("changed");
    flash("已加入空间 " + r.space + "（角色：" + roleLabel(r.role) + "）");
  } catch (e) { err.value = e.message; } finally { busy.value = false; }
}
async function confirmInvite() {
  if (!props.manageSpace) { err.value = "请选择空间"; return; }
  if (busy.value) return;   // 第四轮 P0-4
  busy.value = true;
  try {
    const r = await createInvite(props.token, {
      space: props.manageSpace, role: invRole.value,
      expires_in_hours: Number(invHours.value) || 24,
      max_uses: Number(invUses.value) || 1, note: invNote.value || "",
    });
    lastCreatedCode.value = r.code;
    await loadManage();
    flash("邀请码已生成");
  } catch (e) { err.value = e.message; } finally { busy.value = false; }
}
function copyCode(code) {
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(code).then(
      () => flash("邀请码已复制：" + code),
      () => { okMsg.value = "浏览器不允许自动复制，请手动复制：" + code; },
    );
  } else {
    okMsg.value = "请手动复制邀请码：" + code;
  }
}
/* 第三轮 §3.4：全局 admin 切换「允许成员（editor）生成邀请码」开关 */
async function toggleMemberInvite() {
  if (busy.value) return;   // 第四轮 P0-4
  busy.value = true; err.value = "";
  try {
    const next = !policy.value.member_invite;
    await updatePolicy(props.token, { member_invite: next });
    await load();
    emit("changed");
    flash(next ? "已开启：可编辑成员也能生成邀请码" : "已关闭：仅空间管理员可生成邀请码");
  } catch (e) { err.value = e.message; } finally { busy.value = false; }
}
/* 第三轮 §2：重命名当前管理的空间（展示名，id 不变） */
const renameTo = ref("");
const renaming = ref(false);
function startRenameSpace() {
  renameTo.value = spLabel(manageInfo.value) || props.manageSpace;
  err.value = ""; okMsg.value = "";
  dialog.value = "rename-space";
}
async function confirmRenameSpace() {
  const to = (renameTo.value || "").trim();
  if (!to) { err.value = "新空间名不能为空"; return; }
  if (busy.value) return;   // 第四轮 P0-4
  busy.value = true;
  try {
    await renameSpace(props.token, props.manageSpace, to);
    closeDialog();
    await load();
    emit("changed");
    flash("空间已重命名为 " + to);
  } catch (e) { err.value = e.message; } finally { busy.value = false; }
}
function askRevoke(inv) { revokeTarget.value = inv; err.value = ""; okMsg.value = ""; dialog.value = "revoke"; }
async function confirmRevoke() {
  const inv = revokeTarget.value;
  if (!inv) return;
  if (busy.value) return;   // 第四轮 P0-4
  busy.value = true;
  try {
    await revokeInvite(props.token, inv.code);
    closeDialog();
    await loadManage();
    flash("邀请码已撤销");
  } catch (e) { err.value = e.message; } finally { busy.value = false; }
}

/* ---- 成员：改角色 / 移除 ---- */
function askRole(m) { roleTarget.value = m; err.value = ""; okMsg.value = ""; dialog.value = "role"; }
async function confirmRole(role) {
  const m = roleTarget.value;
  if (!m) { closeDialog(); return; }
  if (busy.value) return;   // 第四轮 P0-4
  busy.value = true;
  try {
    await setMemberRole(props.token, props.manageSpace, m.name, role);
    closeDialog();
    await loadManage();
    flash(m.name + " 已设为" + roleLabel(role));
  } catch (e) { err.value = e.message; } finally { busy.value = false; }
}
function askRemove(m) { memberTarget.value = m; err.value = ""; okMsg.value = ""; dialog.value = "remove-member"; }
async function confirmRemove() {
  const m = memberTarget.value;
  if (!m) return;
  if (busy.value) return;   // 第四轮 P0-4
  busy.value = true;
  try {
    const r = await removeMember(props.token, props.manageSpace, m.name);
    closeDialog();
    await Promise.all([load(), loadManage()]);
    if (r.current) flash("已移除 " + m.name + "，其已切回个人空间");
    else flash("已移除成员 " + m.name);
    emit("changed");
  } catch (e) { err.value = e.message; } finally { busy.value = false; }
}

/* ---- 管理员：把已有账号直接加入空间（updateUserSpace 加入，再按需设 viewer） ---- */
function openAddMember() {
  addTarget.value = ""; addRole.value = "editor";
  err.value = ""; okMsg.value = "";
  users.value = [];
  fetchUsersRaw(props.token)
    .then((r) => { users.value = r.users || []; })
    .catch((e) => { err.value = "加载账号失败：" + e.message; });
  dialog.value = "add-member";
}
async function confirmAddMember() {
  const name = addTarget.value;
  if (!name) { err.value = "请选择账号"; return; }
  if (busy.value) return;   // 第四轮 P0-4
  busy.value = true;
  try {
    await updateUserSpace(props.token, name, props.manageSpace);
    if (addRole.value === "viewer") {
      await setMemberRole(props.token, props.manageSpace, name, "viewer").catch(() => {});
    }
    closeDialog();
    await Promise.all([loadManage(), load()]);
    emit("changed");
    flash("已把 " + name + " 加入空间 " + spaceLabelOf(props.manageSpace) + "（" + roleLabel(addRole.value) + "）");
  } catch (e) { err.value = e.message; } finally { busy.value = false; }
}

onMounted(() => { load(); loadManage(); });
</script>

<template>
  <!-- ============ tab: 我的空间 ============ -->
  <section v-show="tab === 'spaces'" class="account-mgmt glass space-panel">
    <div class="mgmt-head">
      <div class="form-title">我的空间</div>
      <span class="panel-sub">当前空间 <strong>{{ meData?.space_name || meData?.space }}</strong> · {{ roleLabel(meData?.my_role) }}</span>
    </div>

    <div class="entry-row">
      <button v-if="policy.can_create_space" class="btn-primary entry-btn" @click="openDialog('create')">+ 新建空间</button>
      <button class="btn-ghost entry-btn" @click="openDialog('join')">输入邀请码加入</button>
      <span v-if="policy.can_create_space && policy.my_quota != null" class="quota-hint">
        你拥有 {{ policy.owned_count }}/{{ policy.my_quota }} 个空间
      </span>
      <span v-else-if="policy.can_create_space" class="quota-hint">
        你拥有 {{ policy.owned_count }} 个空间（管理员不限）
      </span>
    </div>

    <div class="space-list">
      <div v-for="s in mySpaces" :key="s.space" class="space-row"
        :class="{ current: s.is_current, manageable: canManage(s) }"
        :title="canManage(s) ? '点击进入该空间的成员与邀请码管理' : ''"
        @click="canManage(s) && openManage(s)">
        <div class="sc-avatar" :style="avatarStyle(spLabel(s))">{{ (spLabel(s) || "?").slice(0, 1).toUpperCase() }}</div>
        <div class="sc-info">
          <div class="sc-top">
            <span class="sc-name">{{ spLabel(s) }}</span>
            <span class="sc-role" :class="'role-' + s.my_role">{{ roleLabel(s.my_role) }}</span>
            <span v-if="s.is_current" class="sc-cur">当前</span>
            <span v-if="s.kind === 'personal'" class="sc-kind">个人</span>
          </div>
          <div class="sc-sub">
            <span class="sc-members">
              <span v-if="!s.members || !s.members.length" class="mb-none">无成员</span>
              <template v-else>
                <span v-for="m in s.members.slice(0, 4)" :key="m.name"
                  class="mb-avatar" :class="'mb-' + m.role"
                  :title="m.name + '（' + roleLabel(m.role) + '）'">{{ m.name.slice(0, 1).toUpperCase() }}</span>
                <span v-if="s.members.length > 4" class="mb-more">+{{ s.members.length - 4 }}</span>
              </template>
            </span>
            <span class="sc-meta">歌单 {{ s.playlists }} 个</span>
          </div>
        </div>
        <div class="sc-ops">
          <button v-if="!s.is_current" class="btn-primary sc-btn" :disabled="busy" @click.stop="doSwitch(s)">切换</button>
          <button v-if="canManage(s)" class="btn-ghost sc-btn" :disabled="busy" @click.stop="openManage(s)">管理</button>
          <button
            v-if="s.my_role === 'owner' && (s.members || []).length <= 1"
            class="btn-ghost sc-btn danger-text" :disabled="busy" title="删除仅含你自己的空间"
            @click.stop="askDeleteSpace(s)">删除空间</button>
          <button
            class="btn-ghost sc-btn" :disabled="busy"
            :title="canLeave(s) ? '退出该空间' : '你是唯一管理员，不能退出；可删除空间或邀请他人成为管理员'"
            @click.stop="canLeave(s) ? askLeave(s) : openLeaveHint(s)">退出</button>
        </div>
      </div>
      <div v-if="!mySpaces.length" class="empty-line">暂未加入任何空间。</div>
    </div>

    <!-- 空间管理（内嵌在我的空间页签：选空间 → 成员 + 邀请码，无需再跳页签） -->
    <div v-if="manageBlockSpaces.length" class="manage-block">
      <div class="mgmt-head">
        <div class="form-title">空间管理</div>
        <span class="panel-sub">选择空间，管理它的成员与邀请码</span>
      </div>

      <div class="sp-pick">
        <button v-for="s in manageBlockSpaces" :key="s.space" type="button"
          class="sp-chip" :class="{ on: manageSpace === s.space }"
          @click="pickManage(s.space)">
          {{ spLabel(s) }}
          <em v-if="s.is_current" class="cur-tag">当前</em>
        </button>
      </div>

      <template v-if="manageInfo">
        <div v-if="err" class="modal-msg err">{{ err }}</div>
        <div v-if="okMsg" class="modal-msg ok">{{ okMsg }}</div>

        <div class="manage-head">
          <span class="sc-role" :class="'role-' + (manageInfo.my_role || 'owner')">
            {{ roleLabel(manageInfo.my_role || "owner") }}
          </span>
          <span class="manage-meta">歌单 {{ manageInfo.playlists ?? "–" }} 个 · 成员 {{ manageInfo.members?.length ?? members.length }} 人</span>
          <!-- 第三轮 §3.4：全局 admin 切换「允许成员生成邀请码」全局策略 -->
          <button v-if="isAdmin" class="btn-ghost text-btn inv-policy-btn" :disabled="busy"
            :title="policy.member_invite ? '当前：可编辑成员也能生成邀请码 · 点击关闭' : '当前：仅空间管理员可生成邀请码 · 点击开启'"
            @click="toggleMemberInvite">
            <span class="mini-switch" :class="{ on: policy.member_invite }"><i></i></span>
            成员可邀请{{ policy.member_invite ? "：开" : "：关" }}
          </button>
          <!-- 第三轮 §2：重命名当前空间（展示名，id 不变） -->
          <button v-if="canManage(manageInfo)" class="btn-ghost text-btn" :disabled="busy"
            title="重命名空间（展示名；数据与地址不变）" @click="startRenameSpace">重命名</button>
          <button v-if="isAdmin" class="btn-ghost text-btn" @click="openAddMember">+ 添加已有账号</button>
        </div>

        <!-- 成员表：空间内所有成员可看；改角色/移除等管理操作仅 owner/admin（后端也会 403） -->
        <div class="block-title">成员（{{ members.length }}）</div>
        <div class="user-table-wrap">
          <table class="user-table member-table">
            <thead><tr>
              <th class="c-name">成员</th>
              <th class="c-role">角色</th>
              <th class="c-at">加入时间</th>
              <th class="c-state">邀请人</th>
              <th class="c-op">操作</th>
            </tr></thead>
            <tbody>
              <tr v-if="!members.length" class="empty-row">
                <td colspan="5">暂无成员 · 生成下方邀请码发给家人/朋友，即可凭码加入</td>
              </tr>
              <tr v-for="m in members" :key="m.name">
                <td class="c-name">
                  <strong>{{ m.name }}</strong>
                  <span v-if="m.name === me" class="me-tag">当前</span>
                </td>
                <td class="c-role">
                  <button v-if="canManage(manageInfo) && m.role !== 'owner'" class="role-btn" :disabled="busy"
                    title="点击修改角色" @click="askRole(m)">
                    <span class="role-btn-label">{{ roleLabel(m.role) }}</span>
                  </button>
                  <span v-else class="sc-role" :class="'role-' + m.role"
                    :title="m.role === 'owner' ? '管理员身份由空间归属决定，不能通过指派产生' : ''">{{ roleLabel(m.role) }}</span>
                </td>
                <td class="c-at">{{ m.joined_at ? m.joined_at.slice(0, 10) : "–" }}</td>
                <td class="c-state">{{ m.invited_by || "创建者" }}</td>
                <td class="c-op">
                  <template v-if="canManage(manageInfo)">
                    <button v-if="m.name !== me" class="op-ico danger" :disabled="busy" title="移除成员" @click="askRemove(m)">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="15" height="15"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                    </button>
                    <span v-else>–</span>
                  </template>
                  <span v-else>–</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- 邀请码：owner/admin，或（editor + 管理员开启成员邀请） -->
        <template v-if="canInvite(manageInfo)">
        <div class="block-title">邀请码（{{ activeInvites.length }}）</div>
        <div class="invite-gen">
          <div class="invite-gen-fields">
            <div class="seg">
              <button type="button" class="seg-btn" :class="{ on: invRole === 'editor' }" title="受邀者可以编辑歌单" @click="invRole = 'editor'">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="13" height="13"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>
                <span>可编辑</span>
              </button>
              <button type="button" class="seg-btn" :class="{ on: invRole === 'viewer' }" title="受邀者只能查看歌单" @click="invRole = 'viewer'">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="13" height="13"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
                <span>只读</span>
              </button>
            </div>
            <input v-model.number="invHours" type="number" min="1" max="720" class="inv-mini" title="有效期（小时，1-720）" placeholder="24h">
            <input v-model.number="invUses" type="number" min="1" max="100" class="inv-mini" title="可用次数（1-100）" placeholder="1次">
            <input v-model.trim="invNote" maxlength="60" class="inv-note-input" placeholder="备注（选填）">
            <button class="btn-primary gen-btn" :disabled="busy" @click="confirmInvite">{{ busy ? "生成中…" : "生成" }}</button>
          </div>
          <div v-if="lastCreatedCode" class="code-result">
            新邀请码：<code>{{ lastCreatedCode }}</code>
            <button type="button" class="btn-ghost mini" @click="copyCode(lastCreatedCode)">复制</button>
          </div>
        </div>
        <div v-if="activeInvites.length" class="invite-list">
          <div v-for="inv in activeInvites" :key="inv.code" class="invite-row">
            <code class="inv-code">{{ inv.code }}</code>
            <span class="sc-role" :class="'role-' + inv.role">{{ roleLabel(inv.role) }}</span>
            <span class="inv-state st-ok">剩 {{ inv.remaining }}/{{ inv.max_uses }} 次</span>
            <span class="inv-expire">到期 {{ fmtTime(inv.expires_at) }}</span>
            <span v-if="inv.note" class="inv-note">{{ inv.note }}</span>
            <span class="inv-ops">
              <button class="btn-ghost mini" :disabled="busy" @click="copyCode(inv.code)">复制</button>
              <button class="btn-ghost mini danger-text" :disabled="busy" @click="askRevoke(inv)">撤销</button>
            </span>
          </div>
        </div>
        <div v-else class="invite-empty">暂无有效的邀请码。生成一个发给家人/朋友，即可凭码加入。</div>
        </template>

        <!-- 备份与恢复：owner/admin。每次同步落盘自动滚动两份备份；恢复 = 文件互换，可逆 -->
        <template v-if="canManage(manageInfo)">
        <div class="block-title backup-title">备份与恢复
          <span class="backup-sub">每次同步自动保留最近 2 份备份 · 恢复可撤销</span>
        </div>
        <div class="backup-list">
          <div v-for="b in backups" :key="b.slot" class="backup-row"
               :class="{ cur: b.slot === 'current' }">
            <div class="backup-main">
              <span class="backup-slot">{{ slotLabel[b.slot] || b.slot }}</span>
              <span v-if="b.exists" class="backup-meta">
                {{ b.mtime ? b.mtime.replace("T", " ") : "" }} · {{ fmtSize(b.size) }} ·
                歌单 {{ b.playlists ?? "–" }} 个 / 曲目 {{ b.tracks ?? "–" }} 首
              </span>
              <span v-else class="backup-meta">（空）</span>
            </div>
            <div v-if="b.exists && b.names && b.names.length" class="backup-names"
                 :title="b.names.join('、')">{{ b.names.join("、") }}</div>
            <div class="backup-ops">
              <button v-if="b.slot !== 'current'" class="btn-ghost mini" :disabled="busy || !b.exists || !!b.error"
                :title="b.error || ('把空间数据恢复到' + (slotLabel[b.slot] || b.slot))"
                @click="askRestore(b)">恢复到此</button>
              <span v-else class="backup-cur-tag">使用中</span>
            </div>
          </div>
        </div>
        </template>
      </template>
    </div>
    <div v-if="!manageBlockSpaces.length" class="empty-line">你还没有加入任何空间；在「我的空间」新建或凭邀请码加入一个后，即可在这里查看成员。</div>
  </section>
  <!-- ============ tab: 孤儿空间（仅 admin） ============ -->
  <section v-show="tab === 'orphan' && isAdmin" class="account-mgmt glass space-panel">
    <div class="mgmt-head">
      <div class="form-title">孤儿空间</div>
      <span class="panel-sub">已无任何成员的空间 · 删除即永久清除歌单数据</span>
    </div>
    <div v-if="orphanSpaces.length" class="user-table-wrap">
      <table class="user-table space-table">
        <thead><tr>
          <th class="c-name">空间</th>
          <th class="c-role">歌单</th>
          <th class="c-state">状态</th>
          <th class="c-op">操作</th>
        </tr></thead>
        <tbody>
          <tr v-for="s in orphanSpaces" :key="s.space">
            <td class="c-name"><strong>{{ s.space }}</strong></td>
            <td class="c-role">{{ s.playlists }}</td>
            <td class="c-state"><span class="state-badge orphan">孤儿</span></td>
            <td class="c-op">
              <button class="del-mini" :disabled="busy" @click="askDeleteSpace(s)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-else class="empty-line">没有孤儿空间。</div>
  </section>

  <!-- ==================== dialogs ==================== -->

  <!-- 新建空间 -->
  <div v-if="dialog === 'create'" class="modal-mask" @click.self="closeDialog">
    <div class="modal glass">
      <div class="modal-head">
        <strong>新建空间</strong>
        <button class="modal-x" @click="closeDialog">×</button>
      </div>
      <div v-if="err" class="modal-msg err">{{ err }}</div>
      <label class="field">
        <span>空间名</span>
        <input v-model.trim="newName" placeholder="1-32 位中英文/数字/_/-" @keyup.enter="confirmCreate">
      </label>
      <div class="modal-hint">创建后你是该空间管理员，并自动切换过去；可再用邀请码把家人加进来。配额 {{ policy.owned_count }}/{{ policy.my_quota != null ? policy.my_quota : "∞" }}。</div>
      <div class="form-ops">
        <button class="btn-primary" :disabled="busy" @click="confirmCreate">{{ busy ? "创建中…" : "创建并切换" }}</button>
        <button class="btn-ghost form-btn" @click="closeDialog">取消</button>
      </div>
    </div>
  </div>

  <!-- 输入邀请码加入 -->
  <div v-if="dialog === 'join'" class="modal-mask" @click.self="closeDialog">
    <div class="modal glass">
      <div class="modal-head">
        <strong>加入空间</strong>
        <button class="modal-x" @click="closeDialog">×</button>
      </div>
      <div v-if="err" class="modal-msg err">{{ err }}</div>
      <label class="field">
        <span>邀请码</span>
        <input v-model.trim="joinCode" placeholder="向空间管理员索取 8 位邀请码" @keyup.enter="doJoin">
      </label>
      <div class="modal-hint">加入后即与该空间成员共用一份歌单，并把当前空间切换过去；角色由邀请码决定（可编辑/只读）。</div>
      <div class="form-ops">
        <button class="btn-primary" :disabled="busy" @click="doJoin">{{ busy ? "加入中…" : "加入空间" }}</button>
        <button class="btn-ghost form-btn" @click="closeDialog">取消</button>
      </div>
    </div>
  </div>

  <!-- 添加已有账号（仅 admin） -->
  <div v-if="dialog === 'add-member'" class="modal-mask" @click.self="closeDialog">
    <div class="modal glass">
      <div class="modal-head">
        <strong>添加账号到 {{ spLabel(manageInfo) }}</strong>
        <button class="modal-x" @click="closeDialog">×</button>
      </div>
      <div v-if="err" class="modal-msg err">{{ err }}</div>
      <div v-if="!addableUsers.length" class="modal-hint">没有可添加的账号：所有启用账号都已是该空间成员。</div>
      <template v-else>
        <div class="role-pick">
          <button v-for="u in addableUsers" :key="u.name" type="button" class="role-pick-item"
            :class="{ on: addTarget === u.name }" :disabled="busy" @click="addTarget = u.name">
            <span class="rp-name">{{ u.name }}
              <em v-if="u.space && u.space !== u.name" class="rp-sub">当前空间 {{ spaceLabelOf(u.space) }}</em>
            </span>
            <span class="rp-desc">{{ u.role === "admin" ? "全局管理员" : "普通账号" }}</span>
          </button>
        </div>
        <div class="field">
          <span>加入后角色</span>
          <div class="seg">
            <button type="button" class="seg-btn" :class="{ on: addRole === 'editor' }" @click="addRole = 'editor'">可编辑</button>
            <button type="button" class="seg-btn" :class="{ on: addRole === 'viewer' }" @click="addRole = 'viewer'">只读</button>
          </div>
        </div>
      </template>
      <div class="form-ops">
        <button class="btn-primary" :disabled="busy || !addTarget" @click="confirmAddMember">{{ busy ? "添加中…" : "加入空间" }}</button>
        <button class="btn-ghost form-btn" @click="closeDialog">取消</button>
      </div>
    </div>
  </div>

  <!-- 修改成员角色 -->
  <div v-if="dialog === 'role'" class="modal-mask" @click.self="closeDialog">
    <div class="modal glass">
      <div class="modal-head">
        <strong>修改角色：{{ roleTarget?.name }}</strong>
        <button class="modal-x" @click="closeDialog">×</button>
      </div>
      <div v-if="err" class="modal-msg err">{{ err }}</div>
      <div class="role-pick">
        <button class="role-pick-item" :class="{ on: roleTarget?.role === 'editor' }" :disabled="busy" @click="confirmRole('editor')">
          <span class="rp-name">可编辑</span>
          <span class="rp-desc">可同步增删改、处理自己提交的待确认操作</span>
        </button>
        <button class="role-pick-item" :class="{ on: roleTarget?.role === 'viewer' }" :disabled="busy" @click="confirmRole('viewer')">
          <span class="rp-name">只读</span>
          <span class="rp-desc">只能拉取歌单，不能增删改或同步上传</span>
        </button>
      </div>
      <div class="modal-hint owner-hint">管理员（owner）身份由空间归属决定（如空间创建者），不能通过指派产生；本空间的管理员不受影响。</div>
      <div class="form-ops single">
        <button class="btn-ghost form-btn" @click="closeDialog">取消</button>
      </div>
    </div>
  </div>

  <!-- 移除成员 -->
  <div v-if="dialog === 'remove-member'" class="modal-mask" @click.self="closeDialog">
    <div class="modal glass">
      <div class="modal-head">
        <strong>移除成员：{{ memberTarget?.name }}</strong>
        <button class="modal-x" @click="closeDialog">×</button>
      </div>
      <div v-if="err" class="modal-msg err">{{ err }}</div>
      <div class="modal-hint">
        将 <strong>{{ memberTarget?.name }}</strong> 移出空间 <strong>{{ manageSpace }}</strong>？
        其当前空间会自动切回个人空间，不再能读写该空间；歌单数据不受影响。
      </div>
      <div class="form-ops">
        <button class="btn-primary danger-solid" :disabled="busy" @click="confirmRemove">{{ busy ? "移除中…" : "确认移除" }}</button>
        <button class="btn-ghost form-btn" @click="closeDialog">取消</button>
      </div>
    </div>
  </div>

  <!-- 撤销邀请码 -->
  <div v-if="dialog === 'revoke'" class="modal-mask" @click.self="closeDialog">
    <div class="modal glass">
      <div class="modal-head">
        <strong>撤销邀请码</strong>
        <button class="modal-x" @click="closeDialog">×</button>
      </div>
      <div v-if="err" class="modal-msg err">{{ err }}</div>
      <div class="modal-hint">
        确定撤销邀请码 <code>{{ revokeTarget?.code }}</code>？撤销后该码立即失效，还没用它加入的人将无法再加入。
      </div>
      <div class="form-ops">
        <button class="btn-primary danger-solid" :disabled="busy" @click="confirmRevoke">{{ busy ? "撤销中…" : "确认撤销" }}</button>
        <button class="btn-ghost form-btn" @click="closeDialog">取消</button>
      </div>
    </div>
  </div>

  <!-- 退出空间 -->
  <div v-if="dialog === 'leave'" class="modal-mask" @click.self="closeDialog">
    <div class="modal glass">
      <div class="modal-head">
        <strong>退出空间：{{ spLabel(leaveTarget) }}</strong>
        <button class="modal-x" @click="closeDialog">×</button>
      </div>
      <div v-if="err" class="modal-msg err">{{ err }}</div>
      <div class="modal-hint">退出后你将不再是 <strong>{{ spLabel(leaveTarget) }}</strong> 的成员，当前空间自动切回你的个人空间；该空间歌单数据保留。</div>
      <div class="form-ops">
        <button class="btn-primary danger-solid" :disabled="busy" @click="confirmLeave">{{ busy ? "退出中…" : "确认退出" }}</button>
        <button class="btn-ghost form-btn" @click="closeDialog">取消</button>
      </div>
    </div>
  </div>

  <!-- 唯一管理员不能退出：点击「退出」时的明确提示 -->
  <div v-if="dialog === 'leave-hint'" class="modal-mask" @click.self="closeDialog">
    <div class="modal glass">
      <div class="modal-head">
        <strong>不能退出该空间</strong>
        <button class="modal-x" @click="closeDialog">×</button>
      </div>
      <div class="modal-hint">你是空间 <strong>{{ spLabel(leaveTarget) }}</strong> 的唯一管理员，空间至少要留一名管理员，所以不能直接退出。<br><br>
        你可以：<br>
        ① 在下方「空间管理」里生成邀请码邀请他人加入，并把对方设为管理员后，再退出；<br>
        ② 若该空间只有你一人，可删除空间。</div>
      <div class="form-ops single">
        <button class="form-btn" @click="closeDialog">知道了</button>
      </div>
    </div>
  </div>

  <!-- 删除空间 -->
  <div v-if="dialog === 'del-space'" class="modal-mask" @click.self="closeDialog">
    <div class="modal glass">
      <div class="modal-head">
        <strong>删除空间：{{ spLabel(spaceTarget) }}</strong>
        <button class="modal-x" @click="closeDialog">×</button>
      </div>
      <div v-if="err" class="modal-msg err">{{ err }}</div>
      <div class="modal-hint">
        <template v-if="spaceTarget?.orphan">
          孤儿空间 <strong>{{ spLabel(spaceTarget) }}</strong> 已无任何成员，删除后其歌单数据
          （<code>space__{{ spaceTarget?.space }}.pkl</code>）将<strong>永久清除，不可恢复</strong>。
        </template>
        <template v-else>
          确定删除你自己的空间 <strong>{{ spLabel(spaceTarget) }}</strong>？你将被切回个人空间，该空间歌单数据<strong>永久清除，不可恢复</strong>。
        </template>
      </div>
      <div class="form-ops">
        <button class="btn-primary danger-solid" :disabled="busy" @click="confirmDeleteSpace">{{ busy ? "删除中…" : "确认删除" }}</button>
        <button class="btn-ghost form-btn" @click="closeDialog">取消</button>
      </div>
    </div>
  </div>

  <!-- 第三轮 §2：重命名空间（展示名，id 与数据不变） -->
  <div v-if="dialog === 'rename-space'" class="modal-mask" @click.self="closeDialog">
    <div class="modal glass">
      <div class="modal-head">
        <strong>重命名空间</strong>
        <button class="modal-x" @click="closeDialog">×</button>
      </div>
      <div v-if="err" class="modal-msg err">{{ err }}</div>
      <label class="field">
        <span>新的展示名</span>
        <input v-model.trim="renameTo" placeholder="1-32 位中英文/数字/_/-" autocomplete="off" @keyup.enter="confirmRenameSpace">
      </label>
      <div class="modal-hint">只修改对外展示名：空间 id、歌单数据、成员与客户端同步地址均保持不变。</div>
      <div class="form-ops">
        <button class="btn-primary" :disabled="busy" @click="confirmRenameSpace">{{ busy ? "保存中…" : "保存" }}</button>
        <button class="btn-ghost form-btn" @click="closeDialog">取消</button>
      </div>
    </div>
  </div>

  <!-- 恢复备份：文件互换可逆；恢复后各端下次同步只增不删 -->
  <div v-if="dialog === 'restore'" class="modal-mask" @click.self="closeDialog">
    <div class="modal glass">
      <div class="modal-head">
        <strong>恢复备份：{{ spLabel({ space: manageSpace, name: manageInfo?.name }) }}</strong>
        <button class="modal-x" @click="closeDialog">×</button>
      </div>
      <div v-if="err" class="modal-msg err">{{ err }}</div>
      <div class="modal-hint">
        将把空间数据恢复到 <strong>{{ slotLabel[restoreTarget?.slot] }}</strong>：
        <template v-if="restoreTarget">
          {{ restoreTarget.mtime?.replace("T", " ") }} · 歌单 {{ restoreTarget.playlists }} 个 / 曲目 {{ restoreTarget.tracks }} 首
          <template v-if="restoreTarget.names?.length">（{{ restoreTarget.names.join("、") }}）</template>
        </template>
        。<br>
        当前数据不会丢失——它会被换到备份 1 槽位，<strong>再恢复一次即可撤销</strong>。
        恢复后各设备下次同步只增不删，请各端拉取一次确认后再正常同步。
      </div>
      <div class="form-ops">
        <button class="btn-primary danger-solid" :disabled="busy" @click="confirmRestore">{{ busy ? "恢复中…" : "确认恢复" }}</button>
        <button class="btn-ghost form-btn" @click="closeDialog">取消</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.panel-sub { font-size: 12px; color: var(--ink-3); font-weight: 400; }
/* 空间管理内嵌区：顶部与空间列表之间留出空隙，各区块标题上方统一留白 */
.manage-block { margin-top: 24px; }
.block-title { font-size: 13.5px; font-weight: 600; color: var(--ink); margin: 24px 0 10px; }
/* 成员表空态：柔和占位，替代光秃秃的表头 */
.user-table-wrap .empty-row td {
  text-align: center; color: var(--ink-3); font-size: 12.5px;
  padding: 24px 0 26px; letter-spacing: .02em;
}
.manage-head { margin-bottom: 8px; }
.block-title { font-size: 13px; font-weight: 600; color: var(--ink-2); margin: 16px 0 8px; }
.empty-line { padding: 8px 2px; color: var(--ink-3); font-size: 13px; }

/* ---- 入口行：实底主操作 + 配额 ---- */
.entry-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 14px; }
.entry-btn { width: auto; height: auto; margin: 0; padding: 7px 16px; font-size: 13px; border-radius: 10px; }
.quota-hint { font-size: 11.5px; color: var(--ink-3); }

/* ---- 空间列表：一行一个空间（信息横排 + 行尾固定宽度按钮组） ---- */
.space-list { display: flex; flex-direction: column; gap: 8px; }
.space-row {
  display: flex; align-items: center; gap: 14px;
  border: 1px solid rgba(74, 111, 165, .28);
  border-radius: 14px; padding: 12px 16px; background: rgba(255, 255, 255, .5);
  transition: .15s;
}
.space-row.current { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent) inset;
  background: rgba(74, 111, 165, .08); }
.space-row.manageable { cursor: pointer; }
.space-row.manageable:hover { border-color: var(--accent);
  box-shadow: 0 6px 18px rgba(74, 111, 165, .12); }
.sc-avatar {
  width: 42px; height: 42px; border-radius: 12px; flex-shrink: 0;
  color: #fff; font-size: 17px; font-weight: 700;
  display: flex; align-items: center; justify-content: center;
  box-shadow: 0 4px 12px rgba(74, 111, 165, .16);
}
.sc-info { flex: 1; min-width: 0; }
.sc-top { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.sc-name { font-weight: 600; font-size: 14px; }
.sc-cur { font-size: 10px; color: var(--accent); border: 1px solid var(--accent);
  border-radius: 999px; padding: 0 6px; }
.sc-kind { font-size: 10px; color: var(--ink-3); border: 1px solid rgba(74,111,165,.5);
  border-radius: 999px; padding: 0 6px; }
.sc-role { font-size: 10px; line-height: 1.6; padding: 0 7px; border-radius: 999px; white-space: nowrap; }
.role-owner { background: rgba(212, 163, 115, .15); color: #b07a4a; }
.role-editor { background: rgba(95, 157, 143, .15); color: #4f8879; }
.role-viewer { background: rgba(154, 149, 141, .18); color: #6e6a64; }
/* 第二行：成员头像叠放 + 歌单数 */
.sc-sub { display: flex; align-items: center; gap: 12px; margin-top: 4px; flex-wrap: wrap; }
.sc-members { display: flex; align-items: center; min-height: 20px; }
.mb-avatar {
  width: 20px; height: 20px; border-radius: 50%; font-size: 9.5px; font-weight: 700; color: #fff;
  display: inline-flex; align-items: center; justify-content: center;
  border: 1.5px solid #fff; margin-right: -5px; box-shadow: 0 1px 3px rgba(74, 111, 165, .18);
}
.mb-owner { background: #b07a4a; }
.mb-editor { background: #4f8879; }
.mb-viewer { background: #6e6a64; }
.mb-more { font-size: 10.5px; color: var(--ink-3); margin-left: 7px; }
.mb-none { font-size: 12px; color: var(--ink-3); }
.sc-meta { font-size: 11.5px; color: var(--ink-3); }
/* 行尾操作按钮：固定宽度统一（不再随文字长短参差） */
.sc-ops { display: flex; gap: 6px; margin-left: auto; flex: none; }
/* 关键修复：全局 .btn-primary/.btn-ghost 带 width:100% / 36x36 固定尺寸，这里全部重置为紧凑 */
.sc-ops .sc-btn {
  width: 62px; height: 32px; margin: 0; flex: none;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 12px; padding: 0; border-radius: 9px; white-space: nowrap;
}
.sc-ops .btn-primary { box-shadow: none; }
.sc-ops .btn-primary:hover { box-shadow: 0 8px 22px rgba(74, 111, 165, .28); }
.danger-text { color: var(--red); }

/* ---- 窄屏：空间行允许换行，按钮略缩 ---- */
@media (max-width: 768px) {
  .space-row { flex-wrap: wrap; padding: 10px 12px; }
  .sc-ops { margin-left: auto; }
  .sc-ops .sc-btn { width: 56px; height: 30px; }
}

/* ---- 空间管理页签 ---- */
.sp-pick { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
.sp-chip {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 7px 14px; border-radius: 999px; border: 1px solid rgba(74, 111, 165, .3);
  background: rgba(255, 255, 255, .6); font-size: 13px; font-weight: 600; color: var(--ink-2);
  cursor: pointer; transition: .12s;
}
.sp-chip:hover { border-color: var(--accent); color: var(--accent); }
.sp-chip.on { background: linear-gradient(135deg, #4a6fa5, #6b8cae); color: #fff;
  border-color: transparent; box-shadow: 0 4px 12px rgba(74, 111, 165, .25); }
.sp-chip .cur-tag { color: var(--accent); border-color: var(--accent); }
.sp-chip.on .cur-tag { color: #fff; border-color: rgba(255,255,255,.7); }
.manage-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  padding: 9px 12px; border: 1px solid rgba(74, 111, 165, .22); border-radius: 10px;
  background: rgba(255, 255, 255, .5); margin-bottom: 4px; }
.manage-meta { font-size: 12px; color: var(--ink-2); }
.manage-head .text-btn { margin-left: auto; }
/* 空间管理页签成员表：独立列宽，成员名列宽足够不截断 */
.member-table .c-name { width: 26%; }
.member-table .c-role { width: 16%; }
.member-table .c-at { width: 22%; white-space: nowrap; }
.member-table .c-state { width: 18%; }
.member-table .c-op { width: 18%; }
.member-table .c-name strong { font-size: 13px; }
.member-table .me-tag { margin-left: 4px; }

/* ---- 邀请码：生成表单 + 列表 ---- */
.invite-gen { display: flex; flex-direction: column; gap: 10px; }
.invite-gen-fields { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.invite-gen-fields .seg { margin-left: 0; }
.inv-mini { width: 72px; height: 34px; padding: 0 8px; font-size: 12.5px;
  border: 1px solid rgba(74, 111, 165, .35); border-radius: 9px;
  background: rgba(255, 255, 255, .85); color: var(--ink); outline: none; transition: .12s; }
.inv-mini:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(74, 111, 165, .12); }
.inv-note-input { flex: 1; min-width: 140px; height: 34px; padding: 0 10px; font-size: 12.5px;
  border: 1px solid rgba(74, 111, 165, .35); border-radius: 9px;
  background: rgba(255, 255, 255, .85); color: var(--ink); outline: none; transition: .12s; }
.inv-note-input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(74, 111, 165, .12); }
.gen-btn { width: auto; height: auto; margin: 0 0 0 auto; padding: 7px 18px; font-size: 13px; border-radius: 9px; }
.code-result { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.code-result code { font-family: ui-monospace, Menlo, Consolas, monospace; font-weight: 700;
  letter-spacing: 1px; background: rgba(74, 111, 165, .1); padding: 2px 8px; border-radius: 6px; }
.invite-list { display: flex; flex-direction: column; gap: 6px; margin-top: 10px; }
.invite-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding: 7px 10px; border: 1px solid rgba(74, 111, 165, .25); border-radius: 9px;
  background: rgba(255, 255, 255, .45); font-size: 12.5px; }
.inv-code { font-family: ui-monospace, Menlo, Consolas, monospace; font-weight: 700;
  letter-spacing: 1px; background: rgba(74, 111, 165, .1); padding: 2px 8px; border-radius: 6px; }
.inv-state { font-size: 11.5px; }
.inv-state.st-ok { color: #4f8879; }
.inv-expire { color: var(--ink-3); font-size: 11.5px; }
.inv-note { color: var(--ink-2); font-size: 11.5px; }
.inv-ops { margin-left: auto; display: flex; gap: 4px; }
.invite-empty { font-size: 12.5px; color: var(--ink-3); margin-top: 6px;
  padding: 14px 16px; border: 1px dashed rgba(74, 111, 165, .28); border-radius: 10px;
  background: rgba(255, 255, 255, .35); letter-spacing: .01em; }
/* ---- 备份与恢复 ---- */
.backup-title { display: flex; align-items: baseline; gap: 8px; }
.backup-sub { font-size: 11px; font-weight: 400; color: var(--ink-3); }
.backup-list { display: flex; flex-direction: column; gap: 6px; }
.backup-row { padding: 8px 10px; border: 1px solid rgba(74, 111, 165, .25); border-radius: 9px;
  background: rgba(255, 255, 255, .45); font-size: 12.5px; }
.backup-row.cur { border-color: rgba(85, 157, 143, .45); background: rgba(133, 205, 202, .12); }
.backup-main { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.backup-slot { font-weight: 700; color: var(--ink); }
.backup-meta { color: var(--ink-3); font-size: 11.5px; }
.backup-names { margin-top: 3px; color: var(--ink-2); font-size: 11.5px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.backup-ops { margin-top: 5px; display: flex; justify-content: flex-end; }
.backup-cur-tag { font-size: 11.5px; color: #4f8879; }
.btn-ghost.mini { width: auto; height: auto; padding: 3px 10px; font-size: 11.5px; border-radius: 6px; }
.rp-sub { font-style: normal; font-size: 11px; font-weight: 400; color: var(--ink-3); margin-left: 6px; }
/* 第三轮 §3.4：可编辑成员只见邀请码区时的提示 / 全局策略开关 / 重命名按钮 */
.invite-hint { font-size: 12.5px; color: var(--ink-2); margin: 4px 0 12px;
  padding: 10px 12px; border: 1px dashed rgba(74, 111, 165, .28); border-radius: 10px;
  background: rgba(255, 255, 255, .4); }
.inv-policy-btn { display: inline-flex; align-items: center; gap: 6px;
  font-size: 12px; padding: 4px 10px; border-radius: 8px; }
.mini-switch { width: 30px; height: 16px; border-radius: 999px; background: rgba(74, 111, 165, .22);
  position: relative; transition: .18s; display: inline-block; flex-shrink: 0; }
.mini-switch i { position: absolute; top: 2px; left: 2px; width: 12px; height: 12px;
  border-radius: 50%; background: #fff; transition: .18s; box-shadow: 0 1px 3px rgba(0, 0, 0, .25); }
.mini-switch.on { background: var(--accent); }
.mini-switch.on i { left: 16px; }
.owner-hint { margin-top: 10px; }

/* ---- 孤儿空间表 ---- */
.space-table .c-name { width: 34%; }
.space-table .c-role { width: 16%; text-align: center; }
.space-table .c-state { width: 20%; }
.space-table .c-op { width: 30%; text-align: center; }
.del-mini { font-size: 12px; padding: 4px 14px; border-radius: 7px; border: 0;
  background: var(--red); color: #fff; cursor: pointer; transition: .12s; }
.del-mini:hover:not(:disabled) { filter: brightness(.94); }
.del-mini:disabled { opacity: .45; cursor: not-allowed; }
.state-badge.orphan { background: rgba(192, 101, 111, .12); color: var(--red); }
</style>
