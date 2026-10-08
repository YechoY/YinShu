<script setup>
import { ref, computed, watch, nextTick } from "vue";
import { fmtTime } from "../lib/api";
import { sourceName, sourceClass } from "../lib/constants";

const props = defineProps({
  playlistCount: { type: Number, default: 0 },
  trackCount: { type: Number, default: 0 },
  platformStats: { type: Array, default: () => [] },   // [{ source, name, cls, count }]
  journal: { type: Array, default: () => [] },
});

/* 日志自动滚动到底部（最新在底）；immediate：重新挂载（如从个人中心返回）也立即定位到最新 */
const scrollBox = ref(null);
watch(
  () => props.journal.length,
  async () => {
    await nextTick();
    const el = scrollBox.value;
    if (el) el.scrollTop = el.scrollHeight;
  },
  { immediate: true },
);

/* 客户端/设备名映射 */
const DEVICE_NAMES = {
  "ceru-plugin": "澜音插件",
  "cyshine-v1": "栖弦",
  "lx-x": "洛雪",
};

function clientParts(c) {
  const s = String(c || "");
  if (s === "user-confirm") return { user: "管理端", device: "确认通道" };
  if (s.startsWith("ui:")) return { user: s.slice(3), device: "网页管理" };
  const idx = s.indexOf(":");
  if (idx === -1) return { user: s, device: "" };
  return { user: s.slice(0, idx), device: DEVICE_NAMES[s.slice(idx + 1)] || s.slice(idx + 1) };
}

const ACTION_NAMES = {
  deliver: "同步拉取",
  reorder: "重排歌单",
  playlist_delete: "删除歌单",
  reorder_tracks: "重排歌曲",
  track_delete: "删除歌曲",
  confirm_restore: "确认恢复",
};

function actionLabel(j) {
  const a = j.action;
  if (!a) return "合并提交";
  return ACTION_NAMES[a] || a;
}

function escHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* 详情：新增/删除的歌单、曲目（含歌名列表，最多显示 2 首） */
function detailHtml(j) {
  const parts = [];
  for (const pl of (j.added_playlists || [])) {
    parts.push('<span class="plus">+歌单 ' + escHtml(pl) + '</span>');
  }
  for (const pl of (j.removed_playlists || [])) {
    parts.push('<span class="minus">-歌单 ' + escHtml(pl) + '</span>');
  }
  const trackPart = (obj, cls, sign) => {
    for (const [pl, v] of Object.entries(obj || {})) {
      const n = Array.isArray(v) ? v.length : v;
      const names = Array.isArray(v) ? v.filter(Boolean) : [];
      const brief = names.length
        ? '（' + escHtml(names.slice(0, 2).join('、')) + (names.length > 2 ? ' 等' : '') + '）'
        : '';
      parts.push('<span class="' + cls + '">' + sign + n + ' 曲 → ' + escHtml(pl) + brief + '</span>');
    }
  };
  trackPart(j.added_tracks, "plus", "+");
  trackPart(j.removed_tracks, "minus", "-");
  if (j.action === "playlist_delete") {
    return parts.length ? parts.join(" ") : "";
  }
  if (parts.length) return parts.join(" ");
  if (j.action === "deliver") {
    return '<span style="color:var(--ink-3)">已拉取最新视图</span>';
  }
  if (["reorder", "reorder_tracks"].includes(j.action)) {
    return j.sort_tag != null
      ? '<span style="color:var(--ink-3)">顺序已更新（tag ' + escHtml(j.sort_tag) + '，版本不变）</span>'
      : "";
  }
  return j.mutated ? "歌单内容有变更" : '<span style="color:var(--ink-3)">无变更</span>';
}

/* ---- 平台占比环形图（纯 SVG，无外部依赖） ---- */
const PLATFORM_COLORS = {
  tx: "#5f9d8f",       // QQ音乐 · 绿
  netease: "#c0656f",  // 网易云 · 红
  kuwo: "#d4a373",     // 酷我 · 橙
  kugou: "#4a6fa5",    // 酷狗 · 蓝
  migu: "#c38d94",     // 咪咕 · 玫粉
  other: "#9a958d",    // 其他 · 灰
};
const R = 44, C = 2 * Math.PI * R;

const hovered = ref(null);   // hover 的 stats 项（null=显示总数）

/* 各平台按数量降序；保留 sourceClass 归一（other 显示“其他”） */
const segs = computed(() => {
  const total = props.platformStats.reduce((n, s) => n + s.count, 0) || 1;
  let acc = 0;
  return props.platformStats.map((s) => {
    const frac = s.count / total;
    const dash = Math.max(frac * C - 2, 0.001);
    const item = {
      ...s,
      displayName: s.cls === "other" ? "其他" : (s.name || "其他"),
      color: PLATFORM_COLORS[s.cls] || PLATFORM_COLORS.other,
      frac,
      dash,
      offset: -acc,
    };
    acc += frac * C;
    return item;
  });
});

const centerInfo = computed(() => {
  if (hovered.value) {
    const s = hovered.value;
    return { title: s.displayName, sub: s.count + " 首 · " + Math.round(s.frac * 100) + "%" };
  }
  return { title: "歌曲总数", sub: props.trackCount + " 首" };
});
</script>

<template>
  <footer class="glass statusbar">
    <div class="status-col kpi-col">
      <div class="status-title">同步状态</div>
      <div class="kpis">
        <div class="kpi"><div class="v">{{ playlistCount }}</div><div class="k">歌单</div></div>
        <div class="kpi"><div class="v">{{ trackCount }}</div><div class="k">歌曲</div></div>
      </div>
    </div>

    <div class="status-col pie-col">
      <div class="status-title">平台占比</div>
      <div class="pie-body">
        <div class="pie-wrap">
          <svg class="donut" viewBox="0 0 120 120" aria-label="歌曲平台占比">
            <circle class="donut-bg" cx="60" cy="60" :r="R" />
            <circle
              v-for="(s, i) in segs"
              :key="s.source"
              class="donut-seg"
              :class="{ active: hovered === s }"
              :cx="60" :cy="60" :r="R"
              :stroke="s.color"
              :stroke-dasharray="s.dash + ' ' + (C - s.dash)"
              :stroke-dashoffset="s.offset"
              @mouseenter="hovered = s"
              @mouseleave="hovered = null"
            />
          </svg>
          <div class="donut-center">
            <div class="dc-title">{{ centerInfo.title }}</div>
            <div class="dc-sub">{{ centerInfo.sub }}</div>
          </div>
        </div>
        <div v-if="segs.length" class="pie-legend">
          <span v-for="s in segs" :key="'lg' + s.source" class="lg-item" @mouseenter="hovered = s" @mouseleave="hovered = null">
            <i class="lg-dot" :style="{ background: s.color }"></i>{{ s.displayName }} {{ s.count }}
          </span>
        </div>
        <div v-else class="pie-empty">暂无歌曲</div>
      </div>
    </div>

    <div class="status-col log-col">
      <div class="status-title">同步日志（最近 30 条 · 本地时间 · 最新在底部）</div>
      <div ref="scrollBox" class="status-scroll">
        <div v-for="(j, idx) in journal" :key="idx" class="journal-item">
          <span class="jtime">{{ fmtTime(j.at) }}</span>
          <span class="rev">v{{ j.revision }}</span>
          <span class="jclient">
            <template v-if="clientParts(j.client).device">{{ clientParts(j.client).user }} · {{ clientParts(j.client).device }}</template>
            <template v-else>{{ clientParts(j.client).user }}</template>
          </span>
          <span class="jact" :class="{ mut: actionLabel(j) === '合并提交' }">{{ actionLabel(j) }}</span>
          <span class="jdetail" v-html="detailHtml(j)"></span>
        </div>
        <div v-if="!journal.length" class="journal-item"><span class="jdetail">暂无记录</span></div>
      </div>
    </div>
  </footer>
</template>
