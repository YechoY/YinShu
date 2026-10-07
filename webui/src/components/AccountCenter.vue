<script setup>
import { ref, computed, watch, onMounted, nextTick } from "vue";
import {
  fetchUsersRaw, createUser, deleteUser, changePassword,
  setUserEnabled, setUserRole, renameUser, getSpaces, updateUserSpace,
  setUserQuota, getMe,
} from "../lib/api";
import SpacePanel from "./SpacePanel.vue";

const props = defineProps({
  token: { type: String, required: true },
  me: { type: String, default: "" },
  focusTick: { type: Number, default: 0 },   // 顶栏「空间」点击 → 聚焦"我的空间"页签
});
const emit = defineEmits(["back", "changed", "relogin"]);

/* 个人中心页签："spaces"(我的空间) | "account"(账号) | "orphan"(孤儿空间)
   持久化到 sessionStorage：刷新/切页后回来仍停留在上次页签 */
const tab = ref(sessionStorage.getItem("hub_actab") || "spaces");
function setTab(t) {
  tab.value = t;
  sessionStorage.setItem("hub_actab", t);
}
/* 空间管理（内嵌在我的空间页签）当前选中的空间 */
const manageSpace = ref("");
/* 顶栏跳转：从「我的空间」切入个人中心 */
watch(() => props.focusTick, (v) => {
  if (v > 0) setTab("spaces");
});
/* 从空间卡片「管理」进入 → 选中该空间（不切页签，管理面板就在我的空间页签下方） */
function goManage(sp) {
  manageSpace.value = sp;
  setTab("spaces");
  nextTick(() => {
    const el = document.querySelector(".manage-block");
    if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}
/* 进入个人中心时：默认选中「当前空间」（无论角色；若不在可管理列表则回退我拥有的第一个） */
function ensureManageSpace(list) {
  if (manageSpace.value) return;
  const cur = list.find((s) => s.space === meInfo.value?.space);
  const firstOwn = list.find((s) => s.my_role === "owner");
  manageSpace.value = (cur || firstOwn)?.space || "";
}

const users = ref([]);
const spaces = ref([]);   // 空间列表（admin 全部；普通账号自己加入的）
const globalQuota = ref(3);  // 全局 policy.max_owned_spaces（跟随全局时的默认配额）
const err = ref("");
const okMsg = ref("");
const busy = ref(false);

const meInfo = computed(() => users.value.find((x) => x.name === props.me) || null);
const meIsAdmin = computed(() => !!meInfo.value && meInfo.value.role === "admin");
const adminCount = computed(() => users.value.filter((u) => u.role === "admin").length);
/* 空间展示名（第三轮 §2）：id → 展示名（spaces 列表里带 name，回退 id） */
function spaceName(id) {
  const s = spaces.value.find((x) => x.space === id);
  return (s && s.name) ? s.name : id;
}
const mySpaceInfo = computed(
  () => spaces.value.find((s) => s.space === meInfo.value?.space) || null);
const existSpaces = computed(() => spaces.value.filter((s) => s.exists));
const delIsLastInSpace = computed(() => {
  const t = delTarget.value;
  if (!t) return false;
  return users.value.filter((u) => u.space === t.space).length <= 1;
});

/* 弹窗互斥：同一时间只显示一个 dialog */
const dialog = ref("");   // "" | "pwd" | "rename" | "add" | "del" | "role" | "space" | "quota"
const delTarget = ref(null);
const roleFor = ref(null);   // 正在改角色的账号
const spaceFor = ref(null);  // 正在切换空间的账号
const spaceTo = ref("");     // 切换目标空间
const purgeSpace = ref(false);   // 删号时是否同时清理空间数据
/* 配额 */
const quotaFor = ref(null);  // 正在改配额的账号
const quotaMode = ref("global");  // "global"(跟随全局) | "custom"(自定义数字)
const quotaNum = ref(3);

function openForm(name) {
  err.value = "";
  okMsg.value = "";
  // 新增弹窗每次打开都清空上次输入
  if (name === "add") {
    newName.value = ""; newPass.value = ""; newSpace.value = "";
    newRole.value = "user"; spaceTouched = false;
  }
  dialog.value = dialog.value === name ? "" : name;
}
function closeDialog() {
  dialog.value = "";
  delTarget.value = null;
  roleFor.value = null;
  spaceFor.value = null;
  spaceTo.value = "";
  purgeSpace.value = false;
  quotaFor.value = null;
  err.value = "";
  okMsg.value = "";
}

/* 改密（普通用户验证当前密码用） */
const myOld = ref("");
const pwdFor = ref("");
const pwdNew = ref("");
const showPwd2 = ref(false);   // 改密弹窗密码明文切换
/* WebKit/Blink 支持 -webkit-text-security（密文遮罩 + 无原生眼睛）；Firefox 不支持 → 用原生 password + Firefox 自带眼睛 */
const pwdReveal = typeof CSS !== "undefined" && !!CSS.supports && CSS.supports("-webkit-text-security", "disc");
/* 重命名 */
const renameFor = ref("");
const renameTo = ref("");
/* 新增 */
const newName = ref("");
const newPass = ref("");
const newSpace = ref("");
const newRole = ref("user");
const showPwd = ref(false);    // 新增弹窗密码明文切换
let spaceTouched = false;   // 用户手动改过空间后不再跟随账号名

/* 空间 ID 可留空=账号名：未手动改时自动跟随账号名 */
watch(newName, (v) => {
  if (!spaceTouched) newSpace.value = v.trim();
});
watch(newSpace, (v) => {
  if (v !== newName.value.trim()) spaceTouched = true;
});

async function load() {
  try {
    const [u, s, m] = await Promise.all([
      fetchUsersRaw(props.token), getSpaces(props.token),
      getMe(props.token).catch(() => null),
    ]);
    users.value = u.users || [];
    spaces.value = s.spaces || [];
    ensureManageSpace(spaces.value);
    if (m?.policy?.max_owned_spaces) globalQuota.value = m.policy.max_owned_spaces;
  } catch (e) {
    err.value = e.message === "auth" ? "登录已失效" : "加载账号失败：" + e.message;
  }
}

/* ---- 改密（管理员重置任意账号；普通用户改自己需验证当前密码） ---- */
function startPwd(u) {
  pwdFor.value = u.name;
  pwdNew.value = "";
  myOld.value = "";
  err.value = "";
  okMsg.value = "";
  dialog.value = "pwd";
}

async function submitPwd() {
  err.value = "";
  okMsg.value = "";
  if (!pwdNew.value) { err.value = "新密码不能为空"; return; }
  if (pwdNew.value.length < 6) { err.value = "新密码至少 6 位"; return; }
  if (busy.value) return;   // 第四轮 P0-4
  busy.value = true;
  try {
    if (meIsAdmin.value) {
      await changePassword(props.token, pwdFor.value, pwdNew.value, undefined);
      okMsg.value = "已重置账号 " + pwdFor.value + " 的密码";
      closeDialog();
      await load();
    } else {
      await changePassword(props.token, props.me, pwdNew.value, myOld.value || undefined);
      closeDialog();
      // 第三轮 §1：改自己密码成功后走站内"请重新登录"（不用浏览器原生框）
      emit("relogin", "密码已修改，请使用新密码重新登录");
    }
  } catch (e) {
    err.value = e.message;
  } finally {
    busy.value = false;
  }
}

/* ---- 重命名 ---- */
function startRename(u) {
  renameFor.value = u.name;
  renameTo.value = u.name;
  err.value = "";
  okMsg.value = "";
  dialog.value = "rename";
}

async function submitRename() {
  err.value = "";
  okMsg.value = "";
  const to = renameTo.value.trim();
  if (!to) { err.value = "新账号名不能为空"; return; }
  busy.value = true;
  try {
    await renameUser(props.token, renameFor.value, to);
    const self = renameFor.value === props.me;
    closeDialog();
    if (self) {
      /* 第三轮 §1：改名自己 → 旧 token 已失效，不再 load/changed（会 401 强制踢人），
         直接弹站内"请重新登录"，澄清密码未变 */
      emit("relogin", "当前账号已重命名为 " + to + "，密码未变，请用新账号名登录");
    } else {
      await load();
      emit("changed");
      okMsg.value = "已重命名 " + renameFor.value + " → " + to;
    }
  } catch (e) {
    err.value = e.message;
  } finally {
    busy.value = false;
  }
}

/* ---- 新增 ---- */
async function addUser() {
  err.value = "";
  okMsg.value = "";
  if (!newName.value.trim() || !newPass.value) { err.value = "账号和密码不能为空"; return; }
  if (newPass.value.length < 6) { err.value = "密码至少 6 位"; return; }
  if (busy.value) return;   // 第四轮 P0-4
  busy.value = true;
  try {
    await createUser(props.token, newName.value.trim(), newPass.value,
      newSpace.value.trim() || undefined, newRole.value);
    okMsg.value = "已新增账号 " + newName.value.trim();
    newName.value = ""; newPass.value = ""; newSpace.value = "";
    newRole.value = "user";
    spaceTouched = false;
    closeDialog();
    await load();
    emit("changed");
  } catch (e) {
    err.value = e.message;
  } finally {
    busy.value = false;
  }
}

/* ---- 启停用 / 角色 / 删除 ---- */
async function toggleEnabled(u) {
  err.value = ""; okMsg.value = "";
  busy.value = true;
  try {
    await setUserEnabled(props.token, u.name, !u.enabled);
    okMsg.value = "已" + (u.enabled ? "禁用" : "启用") + "账号 " + u.name;
    await load();
  } catch (e) { err.value = e.message; } finally { busy.value = false; }
}

/* 角色切换：点击角色 → dialog 选择（避免表格容器裁剪下拉） */
function askRole(u) {
  roleFor.value = u;
  err.value = "";
  okMsg.value = "";
  dialog.value = "role";
}

async function confirmRole(role) {
  const u = roleFor.value;
  if (!u || role === u.role) { closeDialog(); return; }
  /* 第四轮 P0-3：请求成功后再关闭弹窗，失败留在弹窗内显示 err（先关后请求看不见结果） */
  err.value = ""; okMsg.value = "";
  busy.value = true;
  try {
    await setUserRole(props.token, u.name, role);
    okMsg.value = "已将 " + u.name + " 设为" + (role === "admin" ? "管理员" : "普通账号");
    closeDialog();
    await load();
  } catch (e) { err.value = e.message; } finally { busy.value = false; }
}

/* 删除确认 dialog */
function askDel(u) {
  delTarget.value = u;
  purgeSpace.value = false;
  err.value = "";
  okMsg.value = "";
  dialog.value = "del";
}

async function confirmDel() {
  const u = delTarget.value;
  if (!u) return;
  if (busy.value) return;   // 第四轮 P0-4
  busy.value = true;
  try {
    await deleteUser(props.token, u.name, purgeSpace.value);
    okMsg.value = "已删除账号 " + u.name
      + (purgeSpace.value ? "（已清理空间数据）" : "（空间数据已保留）");
    closeDialog();
    await load();
    emit("changed");
  } catch (e) {
    err.value = e.message;
  } finally {
    busy.value = false;
  }
}

/* 切换空间（管理员改任何人；普通账号改自己）：目标空间必须已存在 */
function askSpace(u) {
  spaceFor.value = u;
  spaceTo.value = u.space;
  err.value = "";
  okMsg.value = "";
  dialog.value = "space";
}

/* SpacePanel 切换/成员变化后：刷新账号表并通知主界面 */
async function onSpaceChanged() {
  await load();
  emit("changed");
}

/* 设置账号建空间配额（仅管理员）：数字 = 可拥有几个空间；跟随全局 = 用默认值 */
function askQuota(u) {
  quotaFor.value = u;
  quotaMode.value = u.space_quota == null ? "global" : "custom";
  quotaNum.value = u.space_quota ?? globalQuota.value;
  err.value = "";
  okMsg.value = "";
  dialog.value = "quota";
}

async function confirmQuota() {
  const u = quotaFor.value;
  if (!u) return;
  const mode = quotaMode.value;
  if (mode === "custom" && (!Number.isFinite(quotaNum.value) || quotaNum.value < 0)) {
    err.value = "请输入 0 以上的整数";
    return;
  }
  if (busy.value) return;   // 第四轮 P0-4
  busy.value = true;
  try {
    await setUserQuota(props.token, u.name, mode === "custom" ? Math.floor(quotaNum.value) : null);
    okMsg.value = "已设置 " + u.name + " 的空间配额";
    closeDialog();
    await load();
  } catch (e) {
    err.value = e.message;
  } finally {
    busy.value = false;
  }
}

async function confirmSpace() {
  const u = spaceFor.value;
  const to = (spaceTo.value || "").trim();
  if (!u) { closeDialog(); return; }
  if (!to) { err.value = "空间不能为空"; return; }
  if (to === u.space) { closeDialog(); return; }
  if (busy.value) return;   // 第四轮 P0-4
  busy.value = true;
  try {
    await updateUserSpace(props.token, u.name, to);
    okMsg.value = "已将 " + u.name + " 切换到空间 " + to;
    closeDialog();
    await load();
    emit("changed");
  } catch (e) {
    err.value = e.message;   // 后端错误文案原样显示（404=空间不存在等）
  } finally {
    busy.value = false;
  }
}

/* 新增账号：点选已有空间写入 newSpace（不再跟随账号名） */
function pickExistingSpace(sp) {
  newSpace.value = sp;
  spaceTouched = true;
}

onMounted(load);
</script>

<template>
  <div class="account-center">
    <header class="account-head">
      <button class="btn-ghost back-btn" @click="emit('back')">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" width="16" height="16"><polyline points="15 18 9 12 15 6"/></svg>
        返回歌单
      </button>
      <h2>个人中心</h2>
    </header>

    <!-- 当前账号信息 -->
    <section class="account-card glass">
      <div class="avatar">{{ (meInfo?.name || "?").slice(0, 1).toUpperCase() }}</div>
      <div class="account-card-info">
        <div class="account-name">
          {{ meInfo?.name || me }}
          <span class="me-tag">当前</span>
          <span class="role-badge" :class="{ admin: meInfo?.role === 'admin' }">{{ meInfo?.role === "admin" ? "管理员" : "普通" }}</span>
          <span class="state-badge" :class="{ on: meInfo?.enabled }">{{ meInfo?.enabled ? "启用" : "禁用" }}</span>
        </div>
        <div class="account-meta">
          <span>空间：{{ spaceName(meInfo?.space) || "–" }}（成员：{{ (mySpaceInfo?.members || []).map((m) => m.name).join("、") || "–" }}）</span>
          <span v-if="meInfo?.created_at">创建：{{ meInfo.created_at.slice(0, 10) }}</span>
        </div>
      </div>
    </section>

    <!-- 页签：我的空间 / 空间管理 / 账号 / 孤儿空间（按角色显隐） -->
    <nav class="ac-tabs glass">
      <button type="button" class="ac-tab" :class="{ on: tab === 'spaces' }" @click="setTab('spaces')">我的空间</button>
      <button type="button" class="ac-tab" :class="{ on: tab === 'account' }" @click="setTab('account')">{{ meIsAdmin ? "账号管理" : "我的账号" }}</button>
      <button v-if="meIsAdmin" type="button" class="ac-tab" :class="{ on: tab === 'orphan' }" @click="setTab('orphan')">孤儿空间</button>
    </nav>

    <!-- 账号管理（管理员可管理全部；普通账号管理自己） -->
    <template v-if="tab === 'account'">
    <section class="account-mgmt glass">
      <div class="mgmt-head">
        <div class="form-title">{{ meIsAdmin ? "账号管理" : "我的账号" }}</div>
        <button v-if="meIsAdmin" class="btn-ghost text-btn" @click="openForm('add')">
          {{ dialog === "add" ? "收起" : "+ 新增账号" }}
        </button>
      </div>

      <!-- 第四轮 P0-3：表格内操作（启停/配额/分配空间等）的成功/失败提示（弹窗外的操作也可见） -->
      <div v-if="err" class="modal-msg err">{{ err }}</div>
      <div v-if="okMsg" class="modal-msg ok">{{ okMsg }}</div>

      <div class="user-table-wrap">
        <table class="user-table">
          <thead><tr>
            <th class="c-name">账号</th>
            <th class="c-space">空间</th>
            <th class="c-role">角色</th>
            <th class="c-quota">空间配额</th>
            <th class="c-state">状态</th>
            <th class="c-at">创建时间</th>
            <th class="c-op">操作</th>
          </tr></thead>
          <tbody>
            <tr v-for="u in users" :key="u.name" :class="{ disabled: !u.enabled }">
              <td class="c-name">
                <strong>{{ u.name }}</strong>
                <span v-if="u.name === me" class="me-tag">当前</span>
              </td>
              <td class="c-space">
                <!-- 空间归属仅管理员可分配（普通账号不能自行加入他人空间） -->
                <button
                  v-if="meIsAdmin"
                  class="role-btn"
                  :disabled="busy"
                  title="点击分配空间"
                  @click="askSpace(u)"
                >
                  <span class="role-btn-label">{{ spaceName(u.space) }}</span>
                </button>
                <span v-else>{{ spaceName(u.space) }}</span>
              </td>
              <td class="c-role">
                <!-- 管理员可改他人角色：点击弹出角色选择 dialog；其余只看徽标 -->
                <button
                  v-if="meIsAdmin && u.name !== me"
                  class="role-btn"
                  :disabled="busy"
                  title="点击修改角色"
                  @click="askRole(u)"
                >
                  <span class="role-btn-label" :class="{ admin: u.role === 'admin' }">{{ u.role === "admin" ? "管理员" : "普通账号" }}</span>
                </button>
                <span v-else class="role-badge" :class="{ admin: u.role === 'admin' }">{{ u.role === "admin" ? "管理员" : "普通账号" }}</span>
              </td>
              <td class="c-quota">
                <!-- 空间配额：直接显示数字（未单独设置时=全局默认值）；仅管理员可改他人 -->
                <button
                  v-if="meIsAdmin && u.name !== me && u.role !== 'admin'"
                  class="quota-btn"
                  :disabled="busy"
                  :title="u.space_quota == null ? '跟随全局（默认 ' + globalQuota + ' 个）· 点击修改' : '可拥有 ' + u.space_quota + ' 个空间 · 点击修改'"
                  @click="askQuota(u)"
                >
                  {{ u.space_quota == null ? globalQuota : u.space_quota }}
                </button>
                <span v-else-if="u.role === 'admin'" class="quota-plain">不限</span>
                <span v-else class="quota-plain">{{ u.space_quota == null ? globalQuota : u.space_quota }}</span>
              </td>
              <td class="c-state">
                <!-- 管理员可切换他人启停用：状态列开关 -->
                <button
                  v-if="meIsAdmin && u.name !== me"
                  class="switch"
                  :class="{ on: u.enabled }"
                  :disabled="busy"
                  :title="u.enabled ? '点击禁用' : '点击启用'"
                  @click="toggleEnabled(u)"
                >
                  <span class="switch-knob"></span>
                  <em>{{ u.enabled ? "启用" : "禁用" }}</em>
                </button>
                <span v-else class="state-badge" :class="{ on: u.enabled }">{{ u.enabled ? "启用" : "禁用" }}</span>
              </td>
              <td class="c-at">{{ u.created_at ? u.created_at.slice(0, 10) : "–" }}</td>
              <td class="c-op">
                <button
                  v-if="meIsAdmin || u.name === me"
                  class="op-ico"
                  :disabled="busy"
                  title="改密"
                  @click="startPwd(u)"
                >
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="15" height="15"><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/></svg>
                </button>
                <button
                  v-if="meIsAdmin || u.name === me"
                  class="op-ico"
                  :disabled="busy"
                  title="重命名"
                  @click="startRename(u)"
                >
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="15" height="15"><path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg>
                </button>
                <button
                  v-if="meIsAdmin && u.name !== me"
                  class="op-ico danger"
                  :disabled="busy || (u.role === 'admin' && adminCount <= 1) || users.length <= 1"
                  :title="(u.role === 'admin' && adminCount <= 1) ? '至少保留一个管理员' : '删除账号'"
                  @click="askDel(u)"
                >
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="15" height="15"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- 改密 dialog（管理员重置任意账号；普通用户改自己需验证当前密码） -->
    <div v-if="dialog === 'pwd'" class="modal-mask" @click.self="closeDialog">
      <div class="modal glass">
        <div class="modal-head">
          <strong>{{ meIsAdmin ? "重置账号 " + pwdFor + " 的密码" : "修改我的密码" }}</strong>
          <button class="modal-x" title="关闭" @click="closeDialog">×</button>
        </div>
        <div v-if="err" class="modal-msg err">{{ err }}</div>
        <div v-if="okMsg" class="modal-msg ok">{{ okMsg }}</div>
        <label v-if="!meIsAdmin" class="field">
          <span>当前密码</span>
          <span class="pwd-box">
            <input v-model="myOld" :type="pwdReveal ? 'text' : (showPwd2 ? 'text' : 'password')"
              class="pwd-input" :class="{ plain: pwdReveal && showPwd2 }" placeholder="••••••" autocomplete="current-password">
            <button v-if="pwdReveal" type="button" class="pwd-eye" :title="showPwd2 ? '隐藏密码' : '显示密码'" @click="showPwd2 = !showPwd2">
              <svg v-if="showPwd2" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="15" height="15"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>
              <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="15" height="15"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
            </button>
          </span>
        </label>
        <label class="field">
          <span>新密码</span>
          <span class="pwd-box">
            <input v-model="pwdNew" :type="pwdReveal ? 'text' : (showPwd2 ? 'text' : 'password')"
              class="pwd-input" :class="{ plain: pwdReveal && showPwd2 }" placeholder="••••••" autocomplete="new-password">
            <button v-if="pwdReveal" type="button" class="pwd-eye" :title="showPwd2 ? '隐藏密码' : '显示密码'" @click="showPwd2 = !showPwd2">
              <svg v-if="showPwd2" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="15" height="15"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>
              <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="15" height="15"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
            </button>
          </span>
        </label>
        <div class="form-ops">
          <button class="btn-primary" :disabled="busy" @click="submitPwd">{{ busy ? "提交中…" : "保存密码" }}</button>
          <button class="btn-ghost form-btn" @click="closeDialog">取消</button>
        </div>
      </div>
    </div>

    <!-- 重命名 dialog -->
    <div v-if="dialog === 'rename'" class="modal-mask" @click.self="closeDialog">
      <div class="modal glass">
        <div class="modal-head">
          <strong>重命名账号 {{ renameFor }}</strong>
          <button class="modal-x" title="关闭" @click="closeDialog">×</button>
        </div>
        <div v-if="err" class="modal-msg err">{{ err }}</div>
        <div v-if="okMsg" class="modal-msg ok">{{ okMsg }}</div>
        <label class="field">
          <span>新账号名</span>
          <input v-model.trim="renameTo" placeholder="新账号名" autocomplete="off">
        </label>
        <div class="modal-hint">重命名后空间与歌单数据保留、密码不变；客户端需用新账号名登录。若重命名的是当前账号，请重新登录。</div>
        <div class="form-ops">
          <button class="btn-primary" :disabled="busy" @click="submitRename">{{ busy ? "提交中…" : "保存" }}</button>
          <button class="btn-ghost form-btn" @click="closeDialog">取消</button>
        </div>
      </div>
    </div>

    <!-- 新增账号 dialog（管理员，卡片化字段） -->
    <div v-if="dialog === 'add'" class="modal-mask" @click.self="closeDialog">
      <div class="modal glass">
        <div class="modal-head">
          <strong>新增账号</strong>
          <button class="modal-x" title="关闭" @click="closeDialog">×</button>
        </div>
        <div v-if="err" class="modal-msg err">{{ err }}</div>
        <div v-if="okMsg" class="modal-msg ok">{{ okMsg }}</div>
        <div class="add-fields">
          <label class="field">
            <span class="f-label">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="14" height="14"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
              账号名
            </span>
            <input v-model.trim="newName" placeholder="如 wife" autocomplete="off">
          </label>
          <label class="field">
            <span class="f-label">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="14" height="14"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
              密码
            </span>
            <span class="pwd-box">
              <input v-model="newPass" :type="pwdReveal ? 'text' : (showPwd ? 'text' : 'password')"
                class="pwd-input" :class="{ plain: pwdReveal && showPwd }" placeholder="••••••" autocomplete="new-password">
              <button v-if="pwdReveal" type="button" class="pwd-eye" :title="showPwd ? '隐藏密码' : '显示密码'" @click="showPwd = !showPwd">
                <svg v-if="showPwd" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="15" height="15"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>
                <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="15" height="15"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
              </button>
            </span>
          </label>
          <label class="field">
            <span class="f-label">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="14" height="14"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>
              空间 ID <em>（可留空=账号名）</em>
            </span>
            <input v-model.trim="newSpace" placeholder="自动跟随账号名">
            <span v-if="existSpaces.length" class="space-chips">
              <span class="f-label small">已有空间：</span>
              <button v-for="s in existSpaces" :key="s.space" type="button"
                class="chip" :class="{ on: newSpace === s.space }"
                :title="'成员：' + ((s.members || []).map((m) => m.name).join('、') || '无')"
                @click="pickExistingSpace(s.space)">{{ s.space }}</button>
            </span>
          </label>
          <div class="field">
            <span class="f-label">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="14" height="14"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
              角色
            </span>
            <div class="seg">
              <button class="seg-btn" :class="{ on: newRole === 'user' }" @click="newRole = 'user'">普通账号</button>
              <button class="seg-btn" :class="{ on: newRole === 'admin' }" @click="newRole = 'admin'">管理员</button>
            </div>
          </div>
        </div>
        <div class="form-ops">
          <button class="btn-primary" :disabled="busy" @click="addUser">{{ busy ? "提交中…" : "创建账号" }}</button>
          <button class="btn-ghost form-btn" @click="closeDialog">取消</button>
        </div>
      </div>
    </div>

    <!-- 修改角色 dialog（管理员） -->
    <div v-if="dialog === 'role'" class="modal-mask" @click.self="closeDialog">
      <div class="modal glass">
        <div class="modal-head">
          <strong>修改角色：{{ roleFor?.name }}</strong>
          <button class="modal-x" title="关闭" @click="closeDialog">×</button>
        </div>
        <div v-if="err" class="modal-msg err">{{ err }}</div>
        <div v-if="okMsg" class="modal-msg ok">{{ okMsg }}</div>
        <div class="role-pick">
          <button class="role-pick-item" :class="{ on: roleFor?.role === 'user' }" :disabled="busy" @click="confirmRole('user')">
            <span class="rp-name">普通账号</span>
            <span class="rp-desc">只能改自己的密码、重命名自己</span>
          </button>
          <button class="role-pick-item" :class="{ on: roleFor?.role === 'admin' }" :disabled="busy" @click="confirmRole('admin')">
            <span class="rp-name">管理员</span>
            <span class="rp-desc">可管理全部账号、改任意密码</span>
          </button>
        </div>
        <div class="form-ops single">
          <button class="btn-ghost form-btn" @click="closeDialog">取消</button>
        </div>
      </div>
    </div>

    <!-- 切换空间 dialog（管理员改任何人；普通账号改自己） -->
    <div v-if="dialog === 'space'" class="modal-mask" @click.self="closeDialog">
      <div class="modal glass">
        <div class="modal-head">
          <strong>切换空间：{{ spaceFor?.name }}</strong>
          <button class="modal-x" title="关闭" @click="closeDialog">×</button>
        </div>
        <div v-if="err" class="modal-msg err">{{ err }}</div>
        <div v-if="okMsg" class="modal-msg ok">{{ okMsg }}</div>
        <div class="modal-hint">把账号 <strong>{{ spaceFor?.name }}</strong> 分配到已存在的空间（加入后即与该空间成员共用同一份歌单）；要新建空间请用「+ 新增空间」。</div>
        <div v-if="existSpaces.length" class="role-pick">
          <button v-for="s in existSpaces" :key="s.space" class="role-pick-item"
            :class="{ on: spaceTo === s.space }" :disabled="busy"
            @click="spaceTo = s.space">
            <span class="rp-name">{{ spaceName(s.space) }}</span>
            <span class="rp-desc">成员：{{ (s.members || []).map((m) => m.name).join("、") || "无" }} · 歌单 {{ s.playlists }}</span>
          </button>
        </div>
        <label class="field">
          <span>或手输空间名</span>
          <input v-model.trim="spaceTo" placeholder="仅可切换到已存在空间">
        </label>
        <div class="form-ops">
          <button class="btn-primary" :disabled="busy" @click="confirmSpace">{{ busy ? "提交中…" : "保存" }}</button>
          <button class="btn-ghost form-btn" @click="closeDialog">取消</button>
        </div>
      </div>
    </div>

    <!-- 设置空间配额 dialog（仅管理员） -->
    <div v-if="dialog === 'quota'" class="modal-mask" @click.self="closeDialog">
      <div class="modal glass">
        <div class="modal-head">
          <strong>空间配额：{{ quotaFor?.name }}</strong>
          <button class="modal-x" title="关闭" @click="closeDialog">×</button>
        </div>
        <div v-if="err" class="modal-msg err">{{ err }}</div>
        <div v-if="okMsg" class="modal-msg ok">{{ okMsg }}</div>
        <div class="modal-hint">该账号最多可<strong>拥有（管理员）</strong>几个空间。设为 0 表示不能再新建空间；「跟随全局」用默认配额 {{ globalQuota }}。已有空间不受影响。</div>
        <div class="field">
          <span>配额</span>
          <div class="seg quota-seg">
            <button type="button" class="seg-btn" :class="{ on: quotaMode === 'global' }" @click="quotaMode = 'global'">跟随全局（{{ globalQuota }}）</button>
            <button type="button" class="seg-btn" :class="{ on: quotaMode === 'custom' }" @click="quotaMode = 'custom'">自定义</button>
          </div>
        </div>
        <label v-if="quotaMode === 'custom'" class="field">
          <span>可拥有空间数（0-1000）</span>
          <input v-model.number="quotaNum" type="number" min="0" max="1000" placeholder="如 5" @keyup.enter="confirmQuota">
        </label>
        <div class="form-ops">
          <button class="btn-primary" :disabled="busy" @click="confirmQuota">{{ busy ? "保存中…" : "保存配额" }}</button>
          <button class="btn-ghost form-btn" @click="closeDialog">取消</button>
        </div>
      </div>
    </div>

    <!-- 删除确认 dialog -->
    <div v-if="dialog === 'del'" class="modal-mask" @click.self="closeDialog">
      <div class="modal glass">
        <div class="modal-head">
          <strong>删除账号 {{ delTarget?.name }}</strong>
          <button class="modal-x" title="关闭" @click="closeDialog">×</button>
        </div>
        <div v-if="err" class="modal-msg err">{{ err }}</div>
        <div v-if="okMsg" class="modal-msg ok">{{ okMsg }}</div>
        <div class="modal-hint">
          确定删除账号 <strong>{{ delTarget?.name }}</strong>？删除后该账号无法再登录与同步；
          若没有其他账号使用空间 <strong>{{ spaceName(delTarget?.space) }}</strong>，其歌单数据会<strong>保留</strong>在 data/spaces/（不会自动删除）。
        </div>
        <label v-if="delIsLastInSpace" class="auto-toggle">
          <input type="checkbox" v-model="purgeSpace">
          <span>同时清理该空间数据（不可恢复）</span>
        </label>
        <div class="form-ops">
          <button class="btn-primary danger-solid" :disabled="busy" @click="confirmDel">{{ busy ? "删除中…" : "确认删除" }}</button>
          <button class="btn-ghost form-btn" @click="closeDialog">取消</button>
        </div>
      </div>
    </div>
    </template>

    <!-- v3 空间与成员：我的空间 / 空间管理 / 孤儿空间（按页签渲染） -->
    <SpacePanel
      v-if="tab !== 'account'"
      :token="token"
      :me="me"
      :tab="tab"
      :manage-space="manageSpace"
      @go-manage="goManage"
      @changed="onSpaceChanged"
    />

    <div class="ac-foot">
      <span>管理员可增删账号、重命名、改密码、调角色与启停用；普通账号只能改自己的密码。禁用账号无法登录与同步；密码一律哈希存储。</span>
      <span>多个账号可共用同一「空间」的歌单，主题、音源与本地音乐互不影响。删除账号后歌单数据默认保留，可在「空间管理」中删除孤儿空间。</span>
    </div>
  </div>
</template>

<style scoped>
.space-chips { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-top: 6px; }
.space-chips .f-label.small { font-size: 11.5px; color: var(--ink-3); }
.space-chips .chip { cursor: pointer; transition: .12s; }
.space-chips .chip:hover { border-color: var(--accent); color: var(--accent); }
.space-chips .chip.on { border-color: var(--accent); color: var(--accent); background: rgba(74, 111, 165, .1); }
/* 底部说明：两行短句，完整显示不截断 */
.ac-foot { margin-top: 24px; display: flex; flex-direction: column; gap: 6px;
  font-size: 12px; line-height: 1.7; color: var(--ink-2);
  background: rgba(74, 111, 165, .1); padding: 12px 14px; border-radius: 10px; }
.auto-toggle { margin-top: 10px; }
/* 个人中心页签 */
.ac-tabs { display: flex; gap: 4px; padding: 6px; border-radius: 14px; margin-bottom: 16px;
  width: fit-content; max-width: 100%; flex-wrap: wrap; }
.ac-tab { border: 0; background: transparent; padding: 8px 16px; font-size: 13px; font-weight: 600;
  color: var(--ink-2); border-radius: 10px; cursor: pointer; transition: .12s; white-space: nowrap; }
.ac-tab:hover { color: var(--accent); background: var(--accent-soft); }
.ac-tab.on { background: linear-gradient(135deg, #4a6fa5, #6b8cae); color: #fff;
  box-shadow: 0 4px 12px rgba(74, 111, 165, .3); }

/* 空间配额列 */
.c-quota { width: 13%; }
/* 账号表加了配额列后整体列宽再平衡（仅本组件内覆盖，不动 main.css）——账号名/空间列加宽，杜绝省略号截断 */
.account-mgmt .c-name { width: 15%; }
.account-mgmt .c-space { width: 12%; }
.account-mgmt .c-role { width: 14%; }
.account-mgmt .c-state { width: 11%; }
.account-mgmt .c-at { width: 12%; }
.account-mgmt .c-op { width: 23%; }
.account-mgmt .c-name strong { font-size: 13px; }
.account-mgmt thead th { letter-spacing: .04em; }
.quota-btn {
  display: inline-flex; align-items: baseline; gap: 4px; padding: 3px 10px;
  border-radius: 999px; border: 0;
  background: rgba(74, 111, 165, .14); font-size: 12px; color: var(--ink-2);
  cursor: pointer; transition: .12s; white-space: nowrap;
}
.quota-btn:hover:not(:disabled) { background: rgba(74, 111, 165, .14); color: var(--accent); }
.quota-btn:disabled { opacity: .5; cursor: not-allowed; }
.quota-sub { font-style: normal; font-size: 10.5px; color: var(--ink-3); }
.quota-plain { font-size: 12px; color: var(--ink-2); }
.quota-seg { margin-left: 0; margin-top: 8px; }
.quota-seg .seg-btn { padding: 5px 12px; font-size: 12px; }
tr.disabled td { opacity: .72; }
</style>
