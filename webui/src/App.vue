<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import LoginView from "./components/LoginView.vue";
import PlaylistSidebar from "./components/PlaylistSidebar.vue";
import TrackPanel from "./components/TrackPanel.vue";
import StatusBar from "./components/StatusBar.vue";
import AccountCenter from "./components/AccountCenter.vue";
import { fetchStateRaw, fmtClock, getMe } from "./lib/api";
import { sourceName, sourceClass } from "./lib/constants";

const REFRESH_MS = 5000;

const token = ref(sessionStorage.getItem("hub_basic") || "");
const user = ref(sessionStorage.getItem("hub_user") || "");
const state = ref(null);
const active = ref(null);
const updating = ref(false);
/* 顶栏时钟（P2-17：声明提前，避免 TDZ 侥幸） */
const clock = ref(fmtClock());
/* 视图持久化：刷新后保持在个人中心/主界面，不跳回首页（sessionStorage 存 view） */
const view = ref(sessionStorage.getItem("hub_view") || "main");   // "main" | "account"
function setView(v) {
  view.value = v;
  if (v === "main") sessionStorage.removeItem("hub_view");
  else sessionStorage.setItem("hub_view", v);
}

/* v3 身份：GET /api/me（全局角色 + 当前空间角色 + 我加入的空间 + 策略） */
const me = ref(null);
const myRole = computed(() => me.value?.my_role || "");          // 当前空间角色 owner/editor/viewer
const meIsAdmin = computed(() => !!me.value?.is_admin);          // 全局管理员
const canWrite = computed(() => myRole.value !== "viewer");      // viewer 只读
const spaces = computed(() => me.value?.spaces || []);
const policy = computed(() => me.value?.policy || {});

/* 顶栏「空间」点击 → 进入个人中心的「我的空间」页签（空间管理统一在个人中心） */
const acFocus = ref(0);
/* 退出登录确认 */
const logoutDialog = ref(false);
/* 第三轮 §1：改密/改名自己成功后 → 站内"请重新登录"弹窗（提示用新凭据，绝不触发浏览器原生认证框） */
const reloginDialog = ref(false);
const reloginMsg = ref("");
/* reloginPending：弹窗接管期间旧 token 已失效，任何 401 只静默、不强制 logout，
   避免"改名 → 被自动刷新踢到登录页"与弹窗叠加的混乱 */
const reloginPending = ref(false);
function onRelogin(msg) {
  reloginMsg.value = msg || "凭据已变更，请重新登录";
  reloginDialog.value = true;
  reloginPending.value = true;
  stopAuto();   // 旧 token 已失效：停掉自动刷新与时钟，等待用户主动重登
}
function confirmRelogin() {
  reloginDialog.value = false;
  reloginPending.value = false;
  logout();
}
const mySpace = computed(() => spaces.value.find((s) => s.space === state.value?.space) || null);
const mySpaceMembers = computed(() => (mySpace.value?.members || []).map((m) => m.name));

async function refreshMe() {
  if (!token.value) return;
  try {
    me.value = await getMe(token.value);
  } catch (e) {
    if ((e?.status === 401 || e.message === "auth") && !reloginPending.value) logout();
  }
}

/* 顶栏「空间」chip 点击 → 进入个人中心的「我的空间」页签（切换空间统一在个人中心里做） */
function openSpaceDialog() {
  setView("account");
  acFocus.value += 1;
}

const authed = computed(() => !!token.value);
const playlists = computed(() => {
  if (!state.value) return [];
  return state.value.playlists;
});

/* 底部平台占比统计：全部歌单按 source 汇总 */
const platformStats = computed(() => {
  if (!state.value) return [];
  const m = new Map();
  for (const pl of state.value.playlists) {
    for (const t of pl.tracks) {
      const s = t.source || "other";
      m.set(s, (m.get(s) || 0) + 1);
    }
  }
  return [...m.entries()]
    .map(([source, count]) => ({
      source, count,
      name: sourceName(source),
      cls: sourceClass(source),
    }))
    .sort((a, b) => b.count - a.count);
});

let timer = null;
let clockTimer = null;

async function applyState(s) {
  state.value = s;
  if (!active.value || !s.playlists.some((p) => p.pl_id === active.value)) {
    active.value = s.playlists.length ? s.playlists[0].pl_id : null;
  }
}

async function fetchState(manual = false) {
  if (!token.value) return;
  if (manual) updating.value = true;
  try {
    await applyState(await fetchStateRaw(token.value));
    refreshMe().catch(() => {});
  } catch (e) {
    if ((e?.status === 401 || e.message === "auth") && !reloginPending.value) logout();
  } finally {
    updating.value = false;
  }
}

function startAuto() {
  stopAuto();
  timer = setInterval(() => fetchState(false), REFRESH_MS);
  clockTimer = setInterval(() => { clock.value = fmtClock(); }, 1000);
}
function stopAuto() {
  if (timer) { clearInterval(timer); timer = null; }
  if (clockTimer) { clearInterval(clockTimer); clockTimer = null; }
}

function logout() {
  stopAuto();
  sessionStorage.removeItem("hub_basic");
  sessionStorage.removeItem("hub_user");
  sessionStorage.removeItem("hub_view");
  sessionStorage.removeItem("hub_actab");   // 第四轮 P0-6：退出清个人中心页签，普通账号不会落在管理员专属页
  token.value = ""; user.value = ""; state.value = null; active.value = null;
  me.value = null;
  view.value = "main";
}

function confirmLogout() {
  logoutDialog.value = false;
  logout();
}

function onAuthed(u, t) {
  user.value = u;
  token.value = t;
  startAuto();
  fetchState(false);
  refreshMe().catch(() => {});
}

onMounted(() => {
  if (token.value && user.value) {
    fetchState(false).catch(() => {});
    refreshMe().catch(() => {});
    startAuto();
  }
});
onBeforeUnmount(stopAuto);

const ROLE_LABEL = { owner: "管理员", editor: "可编辑", viewer: "只读" };
function roleLabel(r) { return ROLE_LABEL[r] || r || ""; }
</script>

<template>
  <div>
    <!-- 浅色毛玻璃背景光斑 -->
    <div class="bg-decor" aria-hidden="true">
      <span class="blob b1"></span>
      <span class="blob b2"></span>
      <span class="blob b3"></span>
    </div>

    <!-- 登录 -->
    <LoginView v-if="!authed" @authed="onAuthed" />

    <!-- 个人中心 -->
    <AccountCenter
      v-else-if="view === 'account'"
      :token="token"
      :me="user"
      :focus-tick="acFocus"
      @back="setView('main')"
      @changed="fetchState(true)"
      @relogin="onRelogin"
    />

    <!-- 主界面 -->
    <div v-else id="main">
      <header class="glass topbar">
        <div class="topbar-left">
          <div class="brand">
            <span class="brand-ico sm">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>
            </span>
            <strong>音枢 Yinshu</strong>
          </div>
          <div class="top-info">
            <div class="ti-group">
              <span class="ti-label">账号</span>
              <strong class="ti-val">{{ user }}</strong>
            </div>
            <span class="ti-sep" aria-hidden="true"></span>
            <button class="chip chip-space" type="button"
              :title="(mySpaceMembers.length ? '成员：' + mySpaceMembers.join('、') + ' · ' : '') + '当前角色：' + roleLabel(myRole) + '（点击进入个人中心管理/切换空间）'"
              @click="openSpaceDialog">
              <span class="ti-label">空间</span>
              <strong class="ti-val">{{ state?.name || state?.space }}</strong>
              <em class="chip-role" :class="'role-' + myRole">{{ roleLabel(myRole) }}</em>
              <span v-if="mySpaceMembers.length" class="ti-members" :title="mySpaceMembers.join('、')">
                <i>成员</i>{{ mySpaceMembers.join("、") }}
              </span>
              <svg class="chip-edit" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="11" height="11"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
            </button>
            <span v-if="!canWrite" class="chip chip-readonly" title="只读成员：可拉取歌单，不能增删改或确认操作">只读成员</span>
          </div>
        </div>
        <div class="topbar-center">
          <span class="big-clock">{{ clock }}</span>
        </div>
        <div class="topbar-actions">
          <button class="btn-ghost" title="立即刷新" @click="fetchState(true)" :class="{ spinning: updating }">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.64-6.36"/><path d="M21 3v6h-6"/></svg>
          </button>
          <button class="btn-ghost" title="个人中心（账号管理）" @click="setView('account')">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/></svg>
          </button>
          <button class="btn-ghost" title="退出登录" @click="logoutDialog = true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
          </button>
        </div>
      </header>

      <div class="shell">
        <PlaylistSidebar
          :playlists="playlists"
          :active="active"
          :token="token"
          :can-write="canWrite"
          @select="active = $event"
          @refresh="fetchState(true)"
        />
        <div class="main-col">
          <TrackPanel
            :playlist="state?.playlists.find((p) => p.pl_id === active) || null"
            :token="token"
            :can-write="canWrite"
            @refresh="fetchState(true)"
          />
        </div>
      </div>

      <StatusBar
        :playlist-count="playlists.length"
        :track-count="state ? state.playlists.reduce((n, p) => n + p.track_count, 0) : 0"
        :platform-stats="platformStats"
        :journal="state?.journal || []"
      />
    </div>
  </div>

  <!-- 全局弹窗（主界面与个人中心都渲染；改密/改名自己时个人中心页也要能立即弹"请重新登录"） -->
  <!-- 退出登录确认 -->
  <div v-if="logoutDialog" class="modal-mask" @click.self="logoutDialog = false">
    <div class="modal glass logout-modal">
      <div class="logout-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" width="26" height="26"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>
      </div>
      <div class="modal-head">
        <strong>退出登录</strong>
        <button class="modal-x" title="关闭" @click="logoutDialog = false">×</button>
      </div>
      <p class="logout-text">确定退出账号 <strong>{{ user }}</strong> 吗？<br>退出后需要重新输入密码才能登录。</p>
      <div class="form-ops">
        <button class="btn-danger" @click="confirmLogout">退出登录</button>
        <button class="btn-ghost form-btn" @click="logoutDialog = false">取消</button>
      </div>
    </div>
  </div>

  <!-- 第三轮 §1：改密/改名自己后请重新登录（站内弹窗，避免浏览器原生 Basic 框）。
       强制语义：不提供 × 关闭，只能"立即重新登录"或"稍后" -->
  <div v-if="reloginDialog" class="modal-mask">
    <div class="modal glass logout-modal">
      <div class="logout-icon relogin-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="28" height="28"><path d="M15 3h4a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-4"/><polyline points="10 17 15 12 10 7"/><line x1="15" y1="12" x2="3" y2="12"/></svg>
      </div>
      <div class="modal-head relogin-head">
        <strong>请重新登录</strong>
      </div>
      <p class="logout-text">{{ reloginMsg }}<br>点击后回到登录页，请按提示重新登录。</p>
      <div class="form-ops single">
        <button class="btn-primary" @click="confirmRelogin">立即重新登录</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 顶栏信息分组：账号 | 空间（角色徽标 · 成员）—— 层次分明 */
.ti-group { display: inline-flex; align-items: center; gap: 6px; min-width: 0; }
.ti-label { font-size: 11px; color: var(--ink-3); white-space: nowrap; }
.ti-val { font-size: 12.5px; font-weight: 700; color: var(--ink); }
.ti-sep { width: 1px; height: 18px; background: rgba(74, 111, 165, .32); flex-shrink: 0; }
/* 顶部空间标签：可点击切换空间（button 重置为 chip 外观） */
.chip-space {
  display: inline-flex; align-items: center; gap: 7px;
  font-family: inherit; font-size: 12px; line-height: 1;
  cursor: pointer; max-width: 360px;
}
.chip-space:hover { border-color: var(--accent); color: var(--accent); }
.chip-space .ti-label { transition: color .12s; }
.chip-space:hover .ti-label { color: var(--accent); }
.ti-members {
  font-style: normal; font-size: 11px; color: var(--ink-3);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  border-left: 1px solid rgba(74, 111, 165, .35); padding-left: 7px;
  display: inline-flex; align-items: center; gap: 5px; min-width: 0;
}
.ti-members i {
  font-style: normal; font-size: 10px; color: var(--ink-2);
  background: rgba(74, 111, 165, .14); border-radius: 5px; padding: 1px 6px;
}
.chip-space:hover .ti-members { color: var(--accent); }
.chip-edit { opacity: .5; flex-shrink: 0; }
.chip-space:hover .chip-edit { opacity: 1; }
/* 空间角色徽标 */
.chip-role { font-style: normal; font-size: 10px; line-height: 1.5; padding: 1px 7px;
  border-radius: 999px; white-space: nowrap; }
.role-owner { background: rgba(212, 163, 115, .15); color: #b07a4a; }
.role-editor { background: rgba(95, 157, 143, .15); color: #4f8879; }
.role-viewer { background: rgba(154, 149, 141, .18); color: #6e6a64; }
.chip-readonly { background: rgba(154, 149, 141, .18); color: #6e6a64; }
.cur-tag { font-style: normal; font-size: 10px; color: var(--accent);
  border: 1px solid var(--accent); border-radius: 999px; padding: 0 6px; margin-left: 5px; }
/* 退出登录确认弹窗：红色主按钮（全局 .btn-danger 仅在 .p-ops 内定义，这里补一个） */
.btn-danger { background: var(--red); color: #fff; border-color: var(--red); }
.btn-danger:hover:not(:disabled) { box-shadow: 0 8px 18px rgba(192, 101, 111, .35); transform: translateY(-1px); }
/* 退出/重登录弹窗：图标居中、文字加深、按钮居中，两个弹窗统一精致化 */
.logout-modal { text-align: center; width: 460px; }
.logout-icon { width: 60px; height: 60px; border-radius: 50%; margin: 2px auto 6px;
  background: linear-gradient(135deg, rgba(192, 101, 111, .16), rgba(232, 168, 124, .18));
  color: #c0656f; box-shadow: 0 8px 24px rgba(192, 101, 111, .18);
  display: flex; align-items: center; justify-content: center; }
.relogin-icon { background: linear-gradient(135deg, rgba(74, 111, 165, .16), rgba(133, 205, 202, .18));
  color: var(--accent); box-shadow: 0 8px 24px rgba(74, 111, 165, .2); }
.logout-modal .modal-head { justify-content: center; margin-bottom: 6px; }
.logout-modal .modal-head strong { font-size: 18px; font-weight: 600; color: var(--ink);
  font-family: var(--font-serif); letter-spacing: .02em; }
.logout-text { margin: 10px 0 2px; font-size: 14px; line-height: 1.8;
  color: var(--ink); text-align: center; }
.logout-text strong { font-weight: 700; color: var(--ink); }
.logout-modal .form-ops {
  display: flex; gap: 10px; justify-content: center; margin-top: 22px;
}
/* 退出/取消严格等宽（flex:1 均分，覆盖全局 .btn-ghost 36px 与宽度继承），大圆角水彩风格 */
.logout-modal .btn-danger,
.logout-modal .form-btn {
  flex: 1 1 0; min-width: 0; width: auto !important; height: 40px;
  margin: 0; padding: 0 10px; border-radius: 12px;
}
.logout-modal .btn-danger { border: 0; }
/* 顶栏 grid 三栏：左信息 / 中时钟 / 右操作，时钟天然居中且不受内容宽度影响 */
.topbar-left { display: flex; align-items: center; gap: 12px; min-width: 0; overflow: hidden; }
.topbar-center { display: flex; align-items: center; justify-content: center; min-width: 0; }
</style>
