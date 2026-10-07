<script setup>
import { ref } from "vue";
import { reorderPlaylists, deletePlaylist } from "../lib/api";

const props = defineProps({
  playlists: { type: Array, default: () => [] },
  active: { type: String, default: null },
  token: { type: String, default: "" },
  canWrite: { type: Boolean, default: true },
});
const emit = defineEmits(["select", "refresh"]);

const busy = ref(false);
const dialog = ref("");        // "" | del | err
const delTarget = ref(null);
const err = ref("");
const NOTE_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="19" height="19"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>`;

function closeDialog() { dialog.value = ""; err.value = ""; }

function coverFail(e) {
  const el = e.target;
  el.outerHTML = `<div class="pl-cover ph">${NOTE_SVG}</div>`;
}

/* 排序：把 idx 位置的歌单移动到 target 位置 */
async function move(idx, target) {
  if (busy.value) return;
  const order = props.playlists.map((p) => p.pl_id);
  if (target < 0 || target >= order.length) return;
  const [id] = order.splice(idx, 1);
  order.splice(target, 0, id);
  busy.value = true;
  try {
    await reorderPlaylists(props.token, order);
    emit("refresh");
  } catch (e) {
    err.value = "排序失败：" + e.message;
    dialog.value = "err";
  } finally {
    busy.value = false;
  }
}

/* 删除歌单：先弹自定义确认框（不用浏览器原生 confirm） */
function askRemove(p) {
  if (busy.value) return;
  delTarget.value = p;
  err.value = "";
  dialog.value = "del";
}

async function confirmDelete() {
  const p = delTarget.value;
  if (!p || busy.value) return;
  busy.value = true;
  try {
    await deletePlaylist(props.token, p.pl_id);
    closeDialog();
    delTarget.value = null;
    emit("refresh");
  } catch (e) {
    err.value = "删除失败：" + e.message;
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <aside class="glass sidebar">
    <div class="side-head">
      <span>歌单</span>
      <span class="side-count">{{ playlists.length }}</span>
    </div>
    <div class="playlist-list">
      <div
        v-for="(p, i) in playlists"
        :key="p.pl_id"
        class="pl-item"
        :class="{ active: p.pl_id === active }"
        @click="emit('select', p.pl_id)"
      >
        <img v-if="p.cover" class="pl-cover" :src="p.cover" loading="lazy" @error="coverFail">
        <div v-else class="pl-cover ph" v-html="NOTE_SVG"></div>
        <div class="pl-info">
          <div class="pl-name">{{ p.name }}</div>
          <div class="pl-count">{{ p.track_count }} 首</div>
        </div>
        <div v-if="canWrite" class="pl-ops" @click.stop>
          <button class="op-btn" title="上移" :disabled="busy || i === 0" @click="move(i, i - 1)">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="18 15 12 9 6 15"/></svg>
          </button>
          <button class="op-btn" title="下移" :disabled="busy || i === playlists.length - 1" @click="move(i, i + 1)">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
          </button>
          <button class="op-btn danger" title="删除歌单" :disabled="busy" @click="askRemove(p)">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
          </button>
        </div>
      </div>
      <div v-if="!playlists.length" class="pl-name" style="padding: 10px; color: var(--ink-3)">暂无歌单</div>
    </div>
  </aside>

  <!-- 删除歌单确认（自定义 dialog，Teleport 到 body 保证全局居中） -->
  <Teleport to="body">
    <div v-if="dialog === 'del'" class="modal-mask" @click.self="closeDialog">
      <div class="modal glass">
        <div class="modal-head">
          <strong>删除歌单</strong>
          <button class="modal-x" title="关闭" @click="closeDialog">×</button>
        </div>
        <div v-if="err" class="modal-msg err">{{ err }}</div>
        <div class="modal-body">
          确定删除歌单「<strong>{{ delTarget?.name }}</strong>」？
          删除会同步到所有客户端（澜音 / 栖弦），<strong>不可恢复</strong>。
        </div>
        <div class="form-ops">
          <button class="btn-primary danger-solid" :disabled="busy" @click="confirmDelete">{{ busy ? "删除中…" : "确定" }}</button>
          <button class="btn-ghost form-btn" :disabled="busy" @click="closeDialog">取消</button>
        </div>
      </div>
    </div>

    <!-- 操作失败提示 -->
    <div v-if="dialog === 'err'" class="modal-mask" @click.self="closeDialog">
      <div class="modal glass">
        <div class="modal-head">
          <strong>操作失败</strong>
          <button class="modal-x" title="关闭" @click="closeDialog">×</button>
        </div>
        <div class="modal-msg err">{{ err }}</div>
        <div class="form-ops">
          <button class="btn-primary form-btn" @click="closeDialog">知道了</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
