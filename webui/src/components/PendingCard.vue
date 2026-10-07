<script setup>
/* P1-1 确认通道卡片：安全阀挂起删除 + 墓碑压制恢复（docs/05 §5.3.5）。
   每次操作走 POST 确认/拒绝，成功后由父组件触发 /api/state 刷新。 */
import { ref } from "vue";
import {
  confirmPendingDelete, rejectPendingDelete,
  confirmPendingRestore, rejectPendingRestore,
  fmtTime,
} from "../lib/api";

const props = defineProps({
  token: String,
  deletions: { type: Array, default: () => [] },
  restores: { type: Array, default: () => [] },
  me: { type: String, default: "" },           // 当前登录账号（卡片归属判断）
  meIsAdmin: { type: Boolean, default: false }, // 全局 admin 可处理任何人的卡
  myRole: { type: String, default: "" },        // 当前空间角色 owner/editor/viewer
});
const emit = defineEmits(["refresh"]);

const busy = ref(null);
const err = ref("");
const showErr = ref(false);

/* 谁能处理这张卡：空间 owner / 全局 admin 可处理任何卡；editor 仅自己提交的卡；
   viewer 只读，一律不显示按钮（后端 write 闸门也会 403）。 */
function canHandle(account) {
  if (props.myRole === "viewer") return false;
  return props.myRole === "owner" || props.meIsAdmin || !account || account === props.me;
}

async function act(kind, id, fn) {
  if (busy.value) return;
  busy.value = kind + ":" + id;
  try {
    await fn(props.token, id);
    emit("refresh");
  } catch (e) {
    err.value = e.message || "操作失败";
    showErr.value = true;
  } finally {
    busy.value = null;
  }
}
</script>

<template>
  <div v-if="deletions.length || restores.length" class="pending-zone">
    <div class="pending-title">
      <span class="pico">⚠</span> 待确认操作（同步安全机制挂起，需人工裁决）
    </div>

    <div v-for="d in deletions" :key="d.id" class="glass pending-card danger">
      <div class="p-head">
        <strong>{{ d.reason === "never_owned" ? "疑似删除（未拥有）" : "批量删除" }}</strong>
        <span class="p-src">{{ d.client }} · 账号 {{ d.account || "未知" }} · {{ fmtTime(d.created_at) }}</span>
      </div>
      <div class="p-body">
        <div v-if="d.playlists.length" class="p-line">
          <em>歌单</em>
          <span class="p-keys">{{ d.playlists.join("、") }}</span>
        </div>
        <div v-if="Object.keys(d.tracks).length" class="p-line">
          <em>曲目</em>
          <span class="p-keys">
            {{ Object.entries(d.tracks).map(([pl, n]) => `${pl} × ${n}首`).join("、") }}
          </span>
        </div>
        <div class="p-line hint">
          <template v-if="d.reason === 'never_owned'">
            此客户端提交的视图中缺少这些条目，但它从未提交过它们（来自另一端/旧副本），按删除保护规则（R1）不自动删除。确认后写入墓碑并同步给全部客户端；拒绝则保留现状。
          </template>
          <template v-else>
            来源客户端提交了这批删除，超出安全阈值被挂起。确认后写入墓碑并同步给全部客户端；拒绝则保留现状。
          </template>
        </div>
      </div>
      <!-- 空间 owner/全局 admin 可处理任何卡；editor 仅本人提交的卡；viewer 无按钮 -->
      <div v-if="canHandle(d.account)" class="p-ops">
        <button class="btn-danger" :disabled="busy" @click="act('del', d.id, confirmPendingDelete)">
          确认删除
        </button>
        <button class="btn-ghost" :disabled="busy" @click="act('del', d.id, rejectPendingDelete)">
          拒绝（保留）
        </button>
      </div>
      <div v-else class="p-line hint">由账号 {{ d.account }} 提交，需本人或本空间管理员处理</div>
    </div>

    <div v-for="r in restores" :key="r.key" class="glass pending-card restore">
      <div class="p-head">
        <strong>待确认恢复</strong>
        <span class="p-src">{{ r.origin || r.client || "" }} · 账号 {{ r.account || "未知" }} · {{ fmtTime(r.created_at) }}</span>
      </div>
      <div class="p-body">
        <div class="p-line">
          <em>{{ r.element === "playlist" ? "歌单" : "曲目" }}</em>
          <span class="p-keys">{{ r.name || r.key }}</span>
        </div>
        <div v-if="r.tracks && r.tracks.length" class="p-line">
          <em>含曲目</em>
          <span class="p-keys">{{ r.tracks.length }} 首</span>
        </div>
        <div class="p-line hint">该条目此前已被删除（墓碑压制），本次来自 {{ r.origin || "客户端" }} 的提交试图复活它。确认恢复则重新入列并清墓碑；拒绝则保持删除。</div>
      </div>
      <div v-if="canHandle(r.account)" class="p-ops">
        <button class="btn-primary" :disabled="busy" @click="act('res', r.key, confirmPendingRestore)">
          确认恢复
        </button>
        <button class="btn-ghost" :disabled="busy" @click="act('res', r.key, rejectPendingRestore)">
          保持删除
        </button>
      </div>
      <div v-else class="p-line hint">由账号 {{ r.account }} 提交，需本人或本空间管理员处理</div>
    </div>
  </div>

  <!-- 操作失败提示（Teleport 到 body 全局居中） -->
  <Teleport to="body">
    <div v-if="showErr" class="modal-mask" @click.self="showErr = false">
      <div class="modal glass">
        <div class="modal-head">
          <strong>操作失败</strong>
          <button class="modal-x" title="关闭" @click="showErr = false">×</button>
        </div>
        <div class="modal-msg err">{{ err }}</div>
        <div class="form-ops">
          <button class="btn-primary form-btn" @click="showErr = false">知道了</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
