/* 平台 code → 显示名（canonical source 字段） */
export const SOURCE_NAMES = {
  tx: "QQ音乐",
  tencent: "QQ音乐",
  qq: "QQ音乐",
  wy: "网易云音乐",
  netease: "网易云音乐",
  "163": "网易云音乐",
  neteasecloudmusic: "网易云音乐",
  kuwo: "酷我音乐",
  kw: "酷我音乐",
  kugou: "酷狗音乐",
  kg: "酷狗音乐",
  migu: "咪咕音乐",
  bili: "哔哩哔哩",
  bilibili: "哔哩哔哩",
  yt: "YouTube",
  youtube: "YouTube",
  apple: "Apple Music",
  itunes: "Apple Music",
  spotify: "Spotify",
};

export function sourceName(code) {
  const c = String(code || "").toLowerCase();
  return SOURCE_NAMES[c] || code || "–";
}

export function sourceClass(code) {
  const c = String(code || "").toLowerCase();
  if (["tx", "tencent", "qq"].includes(c)) return "tx";
  if (["wy", "netease", "163", "neteasecloudmusic"].includes(c)) return "netease";
  if (["kuwo", "kw"].includes(c)) return "kuwo";
  if (["kugou", "kg"].includes(c)) return "kugou";
  if (["migu"].includes(c)) return "migu";
  return "other";
}

/* 无损档位 */
export const LOSSLESS = new Set([
  "master", "atmos_plus", "atmos", "hires", "flac24bit", "flac",
]);
