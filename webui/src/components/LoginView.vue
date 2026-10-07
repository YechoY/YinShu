<script setup>
import { ref, onMounted } from "vue";
import { fetchStateRaw, fetchUsersRaw, register } from "../lib/api";

const emit = defineEmits(["authed"]);

const mode = ref("login");   // login | register
const loginUser = ref("");
const loginPass = ref("");
const loginErr = ref("");
const loading = ref(false);
const userList = ref([]); // 已配置账号（需认证后获取；未登录时留空，可手动输入）
const showLoginPwd = ref(false);   // 登录密码明文切换
const showRegPwd = ref(false);     // 注册密码明文切换
/* WebKit/Blink 支持 -webkit-text-security（密文遮罩 + 无浏览器原生眼睛，用我们自己的眼睛按钮）；
   Firefox 不支持该属性 → 用 type=password 原生密文 + Firefox 自带眼睛（隐藏我们的按钮避免双眼睛） */
const pwdReveal = typeof CSS !== "undefined" && !!CSS.supports && CSS.supports("-webkit-text-security", "disc");

/* 注册表单 */
const regUser = ref("");
const regPass = ref("");
const regCode = ref("");

async function submit() {
  if (!loginUser.value || !loginPass.value) {
    loginErr.value = "请输入账号和密码";
    return;
  }
  loading.value = true;
  loginErr.value = "";
  try {
    /* UTF-8 btoa：中文/emoji 账号密码不抛 InvalidCharacterError（后端按 UTF-8 解 Basic） */
    const t = btoa(unescape(encodeURIComponent(loginUser.value + ":" + loginPass.value)));
    await fetchStateRaw(t); // 验证凭据
    /* sessionStorage：每个标签页独立保存登录态，多标签登录不同账号互不覆盖（App.vue 同口径） */
    sessionStorage.setItem("hub_basic", t);
    sessionStorage.setItem("hub_user", loginUser.value);
    emit("authed", loginUser.value, t);
  } catch (e) {
    loginErr.value = e.message === "auth" || e?.status === 401
      ? "账号或密码错误" : "无法连接服务：" + e.message;
  } finally {
    loading.value = false;
  }
}

/* 凭邀请码自助注册：成功后自动登录，直接进入邀请码对应空间（不建个人空间） */
async function doRegister() {
  loginErr.value = "";
  if (!regUser.value || !regPass.value) { loginErr.value = "请输入账号和密码"; return; }
  if (regPass.value.length < 6) { loginErr.value = "密码至少 6 位"; return; }
  if (!regCode.value) { loginErr.value = "请输入邀请码"; return; }
  loading.value = true;
  try {
    /* register(name, password, code) 是三个位置参数，不能传对象 */
    await register(regUser.value.trim(), regPass.value, regCode.value.trim());
    const t = btoa(unescape(encodeURIComponent(regUser.value.trim() + ":" + regPass.value)));
    sessionStorage.setItem("hub_basic", t);
    sessionStorage.setItem("hub_user", regUser.value.trim());
    emit("authed", regUser.value.trim(), t);
  } catch (e) {
    loginErr.value = e.message;
  } finally {
    loading.value = false;
  }
}

function switchMode(m) {
  mode.value = m;
  loginErr.value = "";
}

/* 尝试用上次记住的账号拉取账号列表（仅提示用，失败静默） */
onMounted(async () => {
  const t = sessionStorage.getItem("hub_basic");
  if (!t) return;
  try {
    const r = await fetchUsersRaw(t);
    userList.value = r.users || [];
  } catch {
    /* 未登录或网络异常，忽略 */
  }
});
</script>

<template>
  <div id="login">
    <form class="glass login-card" @submit.prevent="mode === 'login' ? submit() : doRegister()">
      <div class="brand">
        <span class="brand-ico">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>
        </span>
        <div>
          <h1>playlist-sync-hub</h1>
          <p>跨平台歌单同步枢纽</p>
        </div>
      </div>

      <div class="seg login-seg">
        <button type="button" class="seg-btn" :class="{ on: mode === 'login' }" @click="switchMode('login')">登录</button>
        <button type="button" class="seg-btn" :class="{ on: mode === 'register' }" @click="switchMode('register')">邀请码注册</button>
      </div>

      <template v-if="mode === 'login'">
        <label class="field">
          <span>账号</span>
          <input
            v-model.trim="loginUser"
            list="hub-users"
            autocomplete="username"
            placeholder="输入账号名"
          >
          <datalist id="hub-users">
            <option v-for="u in userList" :key="u.name" :value="u.name">
              {{ u.name }}（空间 {{ u.space }}）
            </option>
          </datalist>
        </label>
        <p v-if="userList.length" class="field-tip">本机已配置：{{ userList.map((u) => u.name).join("、") }}</p>
        <label class="field">
          <span>密码</span>
          <span class="pwd-box">
            <input v-model="loginPass" :type="pwdReveal ? 'text' : (showLoginPwd ? 'text' : 'password')"
              class="pwd-input" :class="{ plain: pwdReveal && showLoginPwd }"
              autocomplete="current-password" placeholder="输入密码">
            <button v-if="pwdReveal" type="button" class="pwd-eye" :title="showLoginPwd ? '隐藏密码' : '显示密码'"
              @click="showLoginPwd = !showLoginPwd">
              <svg v-if="showLoginPwd" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="15" height="15"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>
              <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="15" height="15"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
            </button>
          </span>
        </label>
        <button class="btn-primary" type="submit" :disabled="loading">
          {{ loading ? "登录中…" : "登录" }}
        </button>
        <div class="login-hint">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="14" height="14"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>
          <span>每个账号有独立的歌单空间，家人可用管理员发的邀请码注册加入同一空间。</span>
        </div>
      </template>

      <template v-else>
        <p class="reg-tip">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="14" height="14"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
          <span>还没有账号？凭空间管理员发的 8 位邀请码自助注册，注册后自动进入对应共享空间。</span>
        </p>
        <label class="field">
          <span>新账号名</span>
          <input v-model.trim="regUser" autocomplete="username" placeholder="设置一个账号名">
        </label>
        <label class="field">
          <span>密码</span>
          <span class="pwd-box">
            <input v-model="regPass" :type="pwdReveal ? 'text' : (showRegPwd ? 'text' : 'password')"
              class="pwd-input" :class="{ plain: pwdReveal && showRegPwd }"
              autocomplete="new-password" placeholder="设置密码（至少 6 位）">
            <button v-if="pwdReveal" type="button" class="pwd-eye" :title="showRegPwd ? '隐藏密码' : '显示密码'"
              @click="showRegPwd = !showRegPwd">
              <svg v-if="showRegPwd" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="15" height="15"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>
              <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="15" height="15"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
            </button>
          </span>
        </label>
        <label class="field">
          <span>邀请码</span>
          <input v-model.trim="regCode" placeholder="输入空间管理员给的 8 位邀请码">
        </label>
        <button class="btn-primary" type="submit" :disabled="loading">
          {{ loading ? "注册中…" : "注册并进入共享空间" }}
        </button>
        <div class="login-hint">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="14" height="14"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>
          <span>注册后直接加入邀请码对应的共享空间，角色由邀请码决定（可编辑 / 只读）。</span>
        </div>
      </template>

      <div class="login-err">{{ loginErr }}</div>
    </form>
  </div>
</template>

<style scoped>
/* 登录页：品牌区居中，登录/注册 tab 全宽等分，卡片放大精致化 */
.login-card { border-radius: 26px; }
.login-card .brand { flex-direction: column; align-items: center; text-align: center; gap: 10px; margin-bottom: 8px; }
.login-card .brand-ico { width: 58px; height: 58px; border-radius: 20px; }
.login-card .brand-ico svg { width: 30px; height: 30px; }
.login-card .brand h1 { font-size: 22px; letter-spacing: .02em; }
.login-card .brand p { font-size: 13px; margin-top: 3px; }
.login-seg { display: flex; margin: 22px 0 8px; }
.login-seg .seg-btn { flex: 1; justify-content: center; padding: 10px 0; font-size: 13.5px; }
.login-card .field { margin-top: 22px; }
.login-card .field span { font-size: 13px; margin-bottom: 8px; }
.login-card .field input { height: 44px; padding: 0 16px; font-size: 14px; border-radius: 12px; }
/* 密码框（显示/隐藏按钮）：输入区留出眼睛按钮空间 */
.login-card .field .pwd-input { height: 44px; padding: 0 44px 0 16px; font-size: 14px; border-radius: 12px; }
.login-card .btn-primary { height: 44px; padding: 0; margin-top: 26px; font-size: 14.5px;
  border-radius: 12px; display: flex; align-items: center; justify-content: center; }
.login-card .login-hint {
  font-size: 12px; margin-top: 20px; color: var(--ink-2); line-height: 1.7;
  display: flex; align-items: flex-start; gap: 8px;
  background: linear-gradient(135deg, rgba(74, 111, 165, .1), rgba(133, 205, 202, .13));
  border: 1px solid rgba(74, 111, 165, .16); padding: 10px 12px; border-radius: 12px;
}
.login-card .login-hint svg { flex: none; margin-top: 3px; color: var(--accent); }
.field-tip { font-size: 11.5px; color: var(--ink-3); margin-top: 6px; }
.reg-tip {
  font-size: 12.5px; color: var(--ink-2); line-height: 1.7; margin: 8px 0 4px;
  display: flex; align-items: flex-start; gap: 8px;
  background: linear-gradient(135deg, rgba(74, 111, 165, .1), rgba(133, 205, 202, .13));
  border: 1px solid rgba(74, 111, 165, .16); padding: 10px 12px; border-radius: 12px;
}
.reg-tip svg { flex: none; margin-top: 3px; color: var(--accent); }
</style>
