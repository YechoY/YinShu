<script setup>
import { ref, computed } from "vue";
import { fmtDur, qualityClass, reorderTracks, deleteTrack } from "../lib/api";
import { sourceName, sourceClass, LOSSLESS } from "../lib/constants";

const props = defineProps({
  playlist: { type: Object, default: null },
  token: { type: String, default: "" },
  canWrite: { type: Boolean, default: true },
});
const emit = defineEmits(["refresh"]);

const search = ref("");
const busy = ref(false);
const dialog = ref("");        // "" | err（删除不再二次确认：用户 2026-10-08「不要再弹确认了」）
const err = ref("");
const NOTE_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="28" height="28"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>`;
const NOTE_SVG_SM = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="15" height="15"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>`;

function closeDialog() { dialog.value = ""; err.value = ""; }

const filteredTracks = computed(() => {
  const p = props.playlist;
  if (!p) return [];
  const kw = search.value.toLowerCase();
  if (!kw) return p.tracks;
  return p.tracks.filter((t) =>
    [t.title, t.singer, t.album].some((v) => String(v || "").toLowerCase().includes(kw)));
});

const losslessCount = computed(() => {
  const p = props.playlist;
  if (!p) return 0;
  return p.tracks.filter((t) => LOSSLESS.has(String(t.quality || "").toLowerCase())).length;
});

function coverFail(e) {
  const el = e.target;
  const small = el.classList.contains("t-cover");
  el.outerHTML = `<div class="${small ? "t-cover" : "pl-cover"} ph">${small ? NOTE_SVG_SM : NOTE_SVG}</div>`;
}

/* 歌曲排序：fIdx 为当前过滤列表中的索引，delta=-1 上移 / +1 下移；按全量真实位置提交 */
async function moveTrack(fIdx, delta) {
  if (busy.value || !props.playlist) return;
  const full = props.playlist.tracks.map((t) => t.key);
  const key = filteredTracks.value[fIdx].key;
  const idx = full.indexOf(key);
  const target = idx + delta;
  if (idx === -1 || target < 0 || target >= full.length) return;
  const [k] = full.splice(idx, 1);
  full.splice(target, 0, k);
  busy.value = true;
  try {
    await reorderTracks(props.token, props.playlist.pl_id, full);
    emit("refresh");
  } catch (e) {
    err.value = "排序失败：" + e.message;
    dialog.value = "err";
  } finally {
    busy.value = false;
  }
}

/* 删除歌曲：直接生效。枢纽的删除是"下次交付即消失"，且曲目池仍保留元数据，
   再导入/别处还在时会自动回到视图 —— 不需要二次确认。 */
async function askRemove(t) {
  const p = props.playlist;
  if (busy.value || !p) return;
  busy.value = true;
  try {
    await deleteTrack(props.token, p.pl_id, t.key);
    emit("refresh");
  } catch (e) {
    err.value = "删除失败：" + e.message;
    dialog.value = "err";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <section class="glass content">
    <div class="pl-hero">
      <div
        class="pl-hero-cover"
        :style="playlist && playlist.cover ? { backgroundImage: 'url(' + playlist.cover + ')' } : {}"
      >
        <span v-if="!playlist || !playlist.cover" v-html="NOTE_SVG"></span>
      </div>
      <div class="pl-hero-info">
        <h2>{{ playlist?.name || "–" }}</h2>
        <div class="pl-hero-meta">
          <span class="chip">{{ playlist?.track_count || 0 }} 首</span>
          <span v-if="losslessCount" class="chip">无损 {{ losslessCount }} 首</span>
        </div>
      </div>
      <div class="search-box">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" width="15" height="15"><circle cx="11" cy="11" r="7"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
        <input v-model.trim="search" placeholder="搜索标题 / 歌手 / 专辑">
      </div>
    </div>

    <div class="track-wrap">
      <table>
        <thead><tr>
          <th class="col-idx">#</th>
          <th class="col-title">标题</th>
          <th class="col-singer">歌手</th>
          <th class="col-album">专辑</th>
          <th class="col-source">平台</th>
          <th class="col-quality">品质</th>
          <th class="col-dur">时长</th>
          <th v-if="canWrite" class="col-op"></th>
        </tr></thead>
        <tbody>
          <tr v-for="(t, i) in filteredTracks" :key="t.key">
            <td class="col-idx">{{ i + 1 }}</td>
            <td>
              <div class="t-title">
                <img v-if="t.picUrl" class="t-cover" :src="t.picUrl" loading="lazy" @error="coverFail">
                <div v-else class="t-cover ph" v-html="NOTE_SVG_SM"></div>
                <span class="t-name">{{ t.title || t.key }}</span>
              </div>
            </td>
            <td><span class="t-singer">{{ t.singer || "–" }}</span></td>
            <td><span class="t-album">{{ t.album || "–" }}</span></td>
            <td>
              <span v-if="t.source" class="t-source" :class="sourceClass(t.source)">{{ sourceName(t.source) }}</span>
              <span v-else class="t-source other">–</span>
            </td>
            <td>
              <span class="q-badge" :class="qualityClass(t.quality)">{{ t.quality ? t.quality.toUpperCase() : "–" }}</span>
            </td>
            <td class="col-dur"><span class="t-dur">{{ fmtDur(t.durationMs) }}</span></td>
            <td v-if="canWrite" class="col-op">
              <div class="t-ops">
                <button class="op-btn" title="上移" :disabled="busy || i === 0" @click="moveTrack(i, -1)">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="18 15 12 9 6 15"/></svg>
                </button>
                <button class="op-btn" title="下移" :disabled="busy || i === filteredTracks.length - 1" @click="moveTrack(i, +1)">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
                </button>
                <button class="op-btn danger" title="删除歌曲" :disabled="busy" @click="askRemove(t)">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
      <div v-if="!filteredTracks.length" class="empty-tip">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" width="46" height="46"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>
        <p>{{ search ? "没有匹配「" + search + "」的歌曲" : "歌单为空" }}</p>
      </div>
    </div>

    <!-- 删除不再二次确认（用户 2026-10-08「不要再弹确认了」） -->

    <!-- 操作失败提示 -->
    <Teleport to="body">
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
  </section>
</template>
