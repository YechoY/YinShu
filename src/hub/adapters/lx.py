"""lx-x（洛雪音乐）方言适配器 —— 第六轮 §2。

- 提交/交付都是**整份文件**外形（`version:"2"` / `lastModified` epoch ms / `data`）。
- 只有 `data.userList`（用户自建歌单）参与合并；三张个人列表（defaultList/loveList/tempList）
  与顶层其余键（playHistory/downloadTasks/未知键）走 opaque 按客户端分桶原样往返（§2.4）。
- 身份（§2.2）：`platform_id` = 顶层 id 去 "<source>_" 前缀（否则原样；缺失才退 meta 键）；
  `source` = song.source || meta.source。无法定身份/非白名单音源 → opaque["tracks"] 原样（I6）。
- 有损点（§2.8）：meta.id==0 原样；杂键不回写（只回写 `_LX_META_KEYS` 九个键）；kg 老格式走"原样 id"。
- 音质元数据（§2.8.1 第十轮修订）：**不再只留最高档**。原生 `meta` 九个键整份存
  `lx_raw_meta` 原样回写，交付时保证 `qualitys`（数组）与 `_qualitys`（映射）同时非空非缺。
  理由是真机事故：洛雪客户端 `src/core/music/utils.ts:246-271 getPlayQuality()` 无守卫地读
  `musicInfo.meta._qualitys[...]`，而同步落库（`src/utils/listManage.ts:98-133 listDataOverwrite`）
  不做任何字段修复 ⇒ 从列表点歌时直接 TypeError，连播放 URL 都不会去取。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Tuple

from .base import KEY_SEP, ParseError, RenderContext, Dialect, identity_key, _duration_ms_to_interval

# 洛雪认识的白名单音源（其余按 I6 进 opaque，不参与合并）
_KNOWN_SOURCES = frozenset({"wy", "tx", "kw", "kg", "mg"})
# 歌单附加键（不参与合并，原样透传）
_PL_EXTRAS = ("origin", "source", "sourceListId", "locationUpdateTime")
# 曲目 meta 的完整键集：洛雪原生写出的就是这九个键（`tests/data/lx_playlists_small.json` 实测）。
# 整份原样往返，缺一个都可能让客户端点播链路的无守卫解引用炸掉。
_LX_META_KEYS = ("_qualitys", "albumId", "albumMid", "albumName", "id", "picUrl",
                 "qualitys", "songId", "strMediaMid")


def _interval_to_duration_ms(interval) -> int | None:
    """洛雪 interval（"mm:ss" / "h:mm:ss"）→ 毫秒；非法返回 None。
    照官方导入插件 plugin.js:2471-2479：split(":") 最多 3 段、每段数字。"""
    if not isinstance(interval, str):
        return None
    parts = [p for p in interval.split(":") if p != ""][:3]
    if not parts:
        return None
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    total_s = 0
    for n in nums:
        total_s = total_s * 60 + n
    return total_s * 1000


def _best_quality(meta: dict):
    """qualitys/_qualitys 里取最高一档（§2.8.1：只保留最高档，size 丢弃）。"""
    for key in ("qualitys", "_qualitys"):
        q = meta.get(key)
        if isinstance(q, list) and q:
            types = [x.get("type") for x in q if isinstance(x, dict) and x.get("type") is not None]
            if types:
                return max(types, key=lambda t: (not isinstance(t, (int, float)), t))
        elif isinstance(q, dict) and q:
            types = [v for v in q.values() if v is not None]
            if types:
                return max(types, key=lambda t: (not isinstance(t, (int, float)), t))
    return None


def _resolve_lx_identity(tr: dict) -> Tuple[str, str, dict]:
    """→ (source, platform_id, opaque_meta)。无法定身份 → source 空串（调用方走 opaque）。"""
    meta = tr.get("meta") if isinstance(tr.get("meta"), dict) else {}
    source = tr.get("source") or meta.get("source")
    raw_id = tr.get("id")
    if not isinstance(raw_id, str) or not raw_id:
        raw_id = (meta.get("strMediaMid") or meta.get("songId")
                  or meta.get("songmid") or meta.get("hash") or meta.get("copyrightId"))
    if not isinstance(source, str) or not source or not isinstance(raw_id, str) or not raw_id:
        return "", "", {}
    platform_id = raw_id
    if platform_id.startswith(f"{source}_"):
        platform_id = platform_id[len(source) + 1:]
    return source, platform_id, meta


def _ensure_lx_quality_meta(tr_meta: dict, fallback_quality=None) -> None:
    """保证交付条目的 `meta` 里 `qualitys`（数组）与 `_qualitys`（映射）同时存在且非空。

    第十轮真机事故（2026-10-08）：枢纽此前只回写 `qualitys:[{type:最高档}]`，不写 `_qualitys`；
    洛雪同步落库不做字段修复，于是 `getPlayQuality()`（`src/core/music/utils.ts:246-271`）
    读 `musicInfo.meta._qualitys[quality]` 抛 `TypeError` ⇒ 从列表点歌完全没反应。
    没有原生音质数据时**至少声明 128k**（各音源都能取到的下限），并把已知的最高档一并列出。
    """
    qs = tr_meta.get("qualitys")
    qm = tr_meta.get("_qualitys")
    types: List = []
    if isinstance(qs, list):
        types = [x.get("type") for x in qs if isinstance(x, dict) and x.get("type") is not None]
    if not types and isinstance(qm, dict) and qm:
        # 只有 `_qualitys` 映射：补出数组形态（size 一并带上，原生就是人类可读字符串）
        qs = []
        for t, v in qm.items():
            size = v.get("size") if isinstance(v, dict) else None
            qs.append({"type": t, "size": size} if size is not None else {"type": t})
        types = [x["type"] for x in qs]
    if not types:
        q = fallback_quality if fallback_quality is not None else "128k"
        qs = [{"type": q}]
        types = [q]
        if str(q) != "128k":
            qs.append({"type": "128k"})       # 已知最高档之外再垫一个下限，保证一定取得到
            types.append("128k")
    tr_meta["qualitys"] = qs
    if not (isinstance(qm, dict) and qm):
        qm = {}
        for item in qs:
            if not isinstance(item, dict) or item.get("type") is None:
                continue
            t = item["type"]
            size = item.get("size")
            qm[t] = {"size": size} if size is not None else {"size": ""}
        tr_meta["_qualitys"] = qm


def _lx_tracks(raw_list) -> Tuple[List[dict], Dict[str, dict], List[dict]]:
    """曲目数组 → (tracks 身份键, meta_delta, opaque 原文列表)。"""
    tracks: List[dict] = []
    meta_delta: Dict[str, dict] = {}
    opaque_tracks: List[dict] = []
    for tr in raw_list:
        if not isinstance(tr, dict):
            raise ParseError(f"lx 曲目必须是对象: {tr!r}")
        source, pid, meta = _resolve_lx_identity(tr)
        if not source or not pid or source not in _KNOWN_SOURCES:
            # 无法定身份 / 非白名单音源 → I6 原样进 opaque（不丢、不计数为合并内容）
            opaque_tracks.append(tr)
            continue
        key = identity_key(source, pid)
        tracks.append({"source": source, "songId": pid})
        album = meta.get("albumName")
        duration_ms = _interval_to_duration_ms(tr.get("interval"))
        quality = _best_quality(meta)
        entry = {
            "source": source,
            "title": tr.get("name") or tr.get("title"),
            "singer": tr.get("singer"),
            "album": album,
            "duration_ms": duration_ms,
            "quality": quality,
            "pic_url": meta.get("picUrl"),
        }
        # 洛雪专用回写字段（原样 id 优先；meta.id 为 0 时不写）
        lx_native = tr.get("id")
        if isinstance(lx_native, str) and lx_native:
            entry["lx_native_id"] = lx_native
        num_id = meta.get("id")
        if isinstance(num_id, (int, str)) and num_id not in (0, "0", ""):
            entry["lx_num_id"] = num_id
        if meta.get("strMediaMid") is not None:
            entry["lx_str_media_mid"] = meta["strMediaMid"]
        if meta.get("songId") is not None:
            entry["lx_song_id"] = meta["songId"]
        # 原生 meta 整份留档（音质明细 albumId/albumMid/hash 等都在里面），交付时原样回写
        raw_meta = {k: meta[k] for k in _LX_META_KEYS if meta.get(k) is not None}
        if raw_meta:
            entry["lx_raw_meta"] = raw_meta
        existing = meta_delta.setdefault(key, {})
        for kk, vv in entry.items():
            if vv is not None and kk not in existing:   # 缺失字段不覆盖已有值（照 cyshine）
                existing[kk] = vv
    return tracks, meta_delta, opaque_tracks


def parse_lx(payload: dict) -> Tuple[dict, Dict[str, dict], dict, List[str]]:
    """洛雪 playlists.json 线格式 → (submission, meta_delta, opaque, warnings)。"""
    warnings: List[str] = []
    if not isinstance(payload, dict):
        raise ParseError("payload 必须是对象")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ParseError("data 必须是对象（settings/user_apis 走旁路，不走此解析）")
    raw_user_list = data.get("userList")
    if not isinstance(raw_user_list, list):
        raise ParseError("data.userList 必须是数组")

    opaque: dict = {
        "lx_lists": {},
        "lx_extra": {},
        "lx_playlist_extras": {},
        "tracks": {},
    }
    # 三张个人列表原样收（缺键不写；空数组也算值，避免丢"已清空"状态）
    for _k in ("defaultList", "loveList", "tempList"):
        if _k in data:
            opaque["lx_lists"][_k] = data[_k]
    # 顶层其余键（playHistory/downloadTasks/未知键）原样收
    for _k, _v in payload.items():
        if _k in ("version", "lastModified", "data"):
            continue
        opaque["lx_extra"][_k] = _v

    playlists: List[dict] = []
    meta_delta: Dict[str, dict] = {}
    for item in raw_user_list:
        if not isinstance(item, dict):
            raise ParseError(f"userList 元素必须是对象: {item!r}")
        pl_id = item.get("id")
        name = item.get("name") or ""
        if not isinstance(pl_id, str) or not pl_id:
            raise ParseError(f"userList 歌单缺 id: {item!r}")
        raw_list = item.get("list") or []
        # 歌单附加键（origin/source/sourceListId/locationUpdateTime）原样透传
        extras = {k: item.get(k) for k in _PL_EXTRAS if item.get(k) is not None}
        if extras:
            opaque["lx_playlist_extras"][str(pl_id)] = extras
        tracks, track_meta, opaque_tracks = _lx_tracks(raw_list)
        playlists.append({"native_id": str(pl_id), "name": name, "tracks": tracks})
        if opaque_tracks:
            opaque.setdefault("tracks", {})[str(pl_id)] = opaque_tracks
        for key, m in track_meta.items():
            merged = meta_delta.setdefault(key, {})
            for kk, vv in m.items():
                if vv is not None and kk not in merged:
                    merged[kk] = vv

    submission = {"playlists": playlists, "opaque": None, "create_only": False}
    # P0-A：洛雪声明的时刻在顶层 `lastModified`（epoch 毫秒）。交给引擎后，枢纽交付的
    # `lastModified` 会严格大于它，洛雪才会 `hasRemoteUpdate=true` 采纳（含删除）视图；
    # 否则它会认为"远端没有更新"而丢弃删除并把自己那份回推。
    stamp = _epoch_ms_to_iso(payload.get("lastModified"))
    if stamp:
        submission["client_modified_at"] = stamp
    return submission, meta_delta, opaque, warnings


def render_lx(ctx: RenderContext) -> dict:
    """枢纽视图 + opaque → 洛雪 playlists.json 外形。只写洛雪认识的键。"""
    native_ids = ctx.native_ids
    playlists_out = []
    for pl_id, info in ctx.view.items():
        plid = native_ids.get(pl_id, pl_id)
        extras = (ctx.opaque.get("lx_playlist_extras") or {}).get(str(plid))
        pl = {"id": plid, "name": info["name"], "locationUpdateTime": None,
              "list": []}
        if isinstance(extras, dict):
            for k in _PL_EXTRAS:
                if k in extras:
                    pl[k] = extras[k]
            if "locationUpdateTime" in extras:
                pl["locationUpdateTime"] = extras["locationUpdateTime"]
        else:
            pl["origin"] = "created"          # 枢纽新建歌单（§2.5）
            pl["locationUpdateTime"] = None
        tracks_out = []
        for key in info["tracks"]:
            meta = ctx.meta_pool.get(key, {})
            source, pid = key.split(KEY_SEP, 1)
            native = meta.get("lx_native_id")
            tid = native if isinstance(native, str) and native else f"{source}_{pid}"
            duration_ms = meta.get("duration_ms")
            interval = _duration_ms_to_interval(duration_ms) if duration_ms else None
            if duration_ms and duration_ms >= 3600_000:
                h = duration_ms // 3600_000
                m = (duration_ms % 3600_000) // 60000
                s = (duration_ms % 60000) // 1000
                interval = f"{h}:{m:02d}:{s:02d}"
            song_id = meta.get("lx_song_id") or pid
            str_mid = meta.get("lx_str_media_mid") or pid
            quality = meta.get("quality")
            # §2.8.1（第十轮修订）：原生 meta 整份回写，再用枢纽已知的身份/展示字段覆盖，
            # 最后保证 qualitys/_qualitys 同时非空——客户端点播链路的无守卫解引用依赖它。
            tr_meta = {}
            raw_meta = meta.get("lx_raw_meta")
            if isinstance(raw_meta, dict):
                for _k in _LX_META_KEYS:
                    if raw_meta.get(_k) is not None:
                        tr_meta[_k] = raw_meta[_k]
            tr_meta["songId"] = song_id
            tr_meta["strMediaMid"] = str_mid
            tr_meta["albumName"] = meta.get("album") or tr_meta.get("albumName") or ""
            tr_meta["picUrl"] = meta.get("pic_url") or tr_meta.get("picUrl") or ""
            if meta.get("lx_num_id") is not None:
                tr_meta["id"] = meta["lx_num_id"]
            elif not isinstance(tr_meta.get("id"), (int, str)):
                tr_meta["id"] = 0          # §2.8.2：meta.id 为 0 回写仍是 0（洛雪原生语义）
            if meta.get("album_id") is not None:
                tr_meta["albumId"] = meta["album_id"]
            _ensure_lx_quality_meta(tr_meta, quality)
            tracks_out.append({
                "id": tid,
                "name": meta.get("title") or pid,
                "singer": meta.get("singer") or "",
                "source": source,
                "interval": interval or "0:00",
                "meta": tr_meta,
            })
        # I6：无法定身份/非白名单音源的曲目在 parse 时进了 `opaque["tracks"][歌单 id]`，
        # 必须原样回填——否则客户端拿到"少了几首"的视图，按"缺席即删除"会删掉用户本地曲目。
        if ctx.opaque_tracks:
            extra = ctx.opaque_tracks.get(str(plid))
            if extra:
                tracks_out.extend(extra)
        pl["list"] = tracks_out
        playlists_out.append(pl)

    payload = {
        "version": "2",
        "lastModified": _iso_to_epoch_ms(ctx.generated_at),
        "data": {
            "userList": playlists_out,
        },
    }
    lists = (ctx.opaque.get("lx_lists") or {})
    for _k in ("defaultList", "loveList", "tempList"):
        if _k in lists:
            payload["data"][_k] = lists[_k]
    for _k, _v in (ctx.opaque.get("lx_extra") or {}).items():
        payload[_k] = _v
    return payload


def _epoch_ms_to_iso(ms) -> str:
    """epoch 毫秒（洛雪 `lastModified`）→ 引擎 ISO 毫秒戳；非法返回 ""（不传声明值）。"""
    if isinstance(ms, bool) or not isinstance(ms, (int, float)) or ms <= 0:
        return ""
    try:
        dt = datetime.fromtimestamp(ms / 1000.0, timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:23] + "Z"
    except Exception:
        return ""


def _iso_to_epoch_ms(stamp: str) -> int:
    """ISO8601（引擎 stamp）→ epoch 毫秒；解析失败回退 0（不影响确定性：同输入同输出）。"""
    try:
        from datetime import datetime, timezone
        s = stamp
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0


DIALECT = Dialect(
    name="lx-x",
    roots=("lx-x",),
    files=("playlists.json", "lx-x-playlists.json", "settings.json", "user_apis.json"),
    parse=parse_lx,
    render=render_lx,
    capabilities={
        # §2.6：洛雪提交整份文件，"缺席即删除"语义成立（与澜音同逻辑）。
        # 若联调发现只上报增量/局部歌单 → 改回 False（一行，被删内容只进 cleanup 提示）。
        "delete_track": True,
        "delete_playlist": True,
        "create_playlist": True,
        "reorder": False,
        # 第十一轮（2026-10-08 真机：lx 拉取后删歌同步不上去）：洛雪是"整份替换"客户端——
        # 拉到远端后 overwriteListFull 整份覆盖，PUT 内容 = 远端视图 + 本地操作重放，
        # 所以"我们交付过、这次提交缺席"就是用户本地删掉了。据此放松 R1（owned 不再要求
        # 自己提交过）；round_trip_sources 限定只对洛雪能原样解析回来的音源生效——解析会把
        # 白名单外的曲目丢进 opaque，那些缺席**不能**当删除（宁可不删，不可误删）。
        "full_view_submit": True,
        "round_trip_sources": tuple(sorted(_KNOWN_SOURCES)),
    },
    empty_view_on_no_baseline=False,
    file_fallback=True,               # §2.7：地址带任意目录名（真机目录 LX_Muisc 打错也能命中）
    bypass_files=("settings.json", "user_apis.json"),   # 整文件 opaque 旁路（§2.4）
)
