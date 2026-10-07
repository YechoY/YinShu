"""方言适配器（docs/04 §4.3/4.4）：把客户端原生格式 ↔ 引擎 submission/view 互译。

适配器是纯函数，不含合并逻辑——合并只发生在引擎（docs/05）。任何 lossy 行为须在此登记。

本文件按设计文档描述的格式实现（docs/02 §2.2.1 + docs/04 §4.4.1 + spec/canonical-v1 §12 渲染硬要求）；
真实栖弦 237KB 全字段的精确对齐留待 M0.5 真机取证（样本/源码当前不在项目目录）。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# 身份键分隔符
KEY_SEP = ":"


class ParseError(Exception):
    """客户端负载结构损坏 / 无法表达，应返回 400 且不动基线。"""


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------

def _interval_to_duration_ms(interval) -> Optional[int]:
    """'03:27' → 207000。非法/缺失 → None。"""
    if not isinstance(interval, str):
        return None
    m = re.fullmatch(r"(\d+):(\d{2})", interval.strip())
    if not m:
        return None
    return (int(m.group(1)) * 60 + int(m.group(2))) * 1000


def _duration_ms_to_interval(duration_ms) -> str:
    """207000 → '03:27'（零填充，canonical-v1 §12 硬要求 4）。"""
    if duration_ms is None:
        return "0:00"
    total_s = max(0, int(duration_ms) // 1000)
    return f"{total_s // 60:02d}:{total_s % 60:02d}"


def identity_key(source: str, song_id: str) -> str:
    return f"{source}{KEY_SEP}{song_id}"


# ---------------------------------------------------------------------------
# cyshine-v1（栖弦）—— parse / render
# ---------------------------------------------------------------------------

# 栖弦顶层结构（docs/02 §2.2.1）：
#   { "schemaVersion":1, "sections":{ "playlists":{ "data":[ {version,id,name,tracks,createdAt,updatedAt} ]},
#                                       "appearance":{...}, "musicSources":{...} } }
# 曲目：{musicId,name,singer,albumName,source,quality,picUrl,
#         musicInfo:{id,name,singer,source,interval,meta:{songId,...}}}

def parse_cyshine(payload: dict) -> Tuple[dict, Dict[str, dict], dict, List[str]]:
    """栖弦 payload → (引擎 submission, 元数据池增量, opaque, warnings)。

    元数据池增量：{identity_key: {title,singer,album,duration_ms,quality,pic_url,music_info_id}}。
    opaque：栖弦 appearance/musicSources 原样透传（docs/04 §4.4.1，枢纽不参与其内容）；
    本地/文件曲目（无法确定平台身份）按 I6 原样收集进 opaque.tracks（不进 playlists），
    渲染时回填——否则栖弦按"缺席即删"会清掉用户的本地音乐（P0-3）。
    """
    warnings: List[str] = []
    if not isinstance(payload, dict):
        raise ParseError("payload 必须是对象")
    if payload.get("schemaVersion") not in (1,):
        raise ParseError(f"不支持的 schemaVersion: {payload.get('schemaVersion')!r}")
    sections = payload.get("sections")
    if not isinstance(sections, dict):
        raise ParseError("缺少 sections")
    pl_section = sections.get("playlists")
    if not isinstance(pl_section, dict) or not isinstance(pl_section.get("data"), list):
        raise ParseError("sections.playlists.data 必须是数组")

    playlists = []
    meta_delta: Dict[str, dict] = {}
    opaque_tracks: Dict[str, list] = {}
    for item in pl_section["data"]:
        if not isinstance(item, dict):
            raise ParseError("playlists.data 元素必须是对象")
        pl_id = item.get("id")
        name = item.get("name") or ""
        if not isinstance(pl_id, str) or not pl_id:
            raise ParseError(f"歌单缺 id: {item!r}")
        raw_tracks = item.get("tracks")
        if not isinstance(raw_tracks, list):
            raise ParseError(f"歌单 {pl_id!r} 的 tracks 必须是数组")
        tracks = []
        local = []
        for tr in raw_tracks:
            r = _cyshine_track_identity(tr)
            if r is None:
                # I6：本地/文件曲目原样携带（不参与合并/去重/身份），渲染时回填
                local.append(tr)
                continue
            src, sid, meta = r
            tracks.append({"source": src, "songId": sid})
            meta_delta.setdefault(identity_key(src, sid), {})
            # 展示字段并入元数据池（只存一份，P5）；缺失字段以已存在的为准
            merged = meta_delta[identity_key(src, sid)]
            for k, v in meta.items():
                if v is not None and k not in merged:
                    merged[k] = v
        playlists.append({"native_id": str(pl_id), "name": name, "tracks": tracks})
        if local:
            opaque_tracks[str(pl_id)] = local
            warnings.append(f"歌单 {pl_id} 含 {len(local)} 首本地/文件曲目（按 I6 opaque 携带）")

    opaque = {
        "appearance": sections.get("appearance"),
        "musicSources": sections.get("musicSources"),
        "tracks": opaque_tracks or None,
    }
    submission = {"playlists": playlists, "opaque": opaque, "create_only": False}
    return submission, meta_delta, opaque, warnings


def _cyshine_track_identity(track) -> Optional[Tuple[str, str, dict]]:
    """推 (source, songId, 展示字段)。本地/文件曲目推不出平台身份 → None（P0-3，不再 400）。"""
    if not isinstance(track, dict):
        raise ParseError("track 必须是对象")
    minfo = track.get("musicInfo")
    minfo = minfo if isinstance(minfo, dict) else {}
    meta = minfo.get("meta")
    meta = meta if isinstance(meta, dict) else {}

    src = track.get("source") or minfo.get("source")
    song_id = meta.get("songId")
    if not song_id:
        # 从 musicId 去掉 `<source>_` 前缀（docs/04 §4.4.1）
        mid = track.get("musicId") or ""
        if isinstance(mid, str) and src and mid.startswith(str(src) + "_"):
            song_id = mid[len(str(src)) + 1:]
    if not isinstance(src, str) or not src or not isinstance(song_id, str) or not song_id:
        return None   # 本地文件 / 无平台 id → I6 opaque 原样携带

    show_meta = {
        "source": src,
        "title": track.get("name") or minfo.get("name"),
        "singer": track.get("singer") or minfo.get("singer"),
        "album": track.get("albumName"),
        "quality": track.get("quality"),
        "pic_url": track.get("picUrl"),
        "music_info_id": minfo.get("id"),
        "duration_ms": _interval_to_duration_ms(minfo.get("interval")),
    }
    return str(src), str(song_id), show_meta


def render_cyshine(view: Dict[str, dict], meta_pool: Dict[str, dict],
                   native_ids: Dict[str, str], opaque: Optional[dict],
                   revision: int, generated_at: str,
                   pl_times: Optional[Dict[str, dict]] = None,
                   opaque_tracks: Optional[Dict[str, list]] = None) -> dict:
    """引擎 view → 栖弦 payload（满足 canonical-v1 §12 渲染硬要求 1–6）。

    pl_times：歌单 canonical 的 createdAt/updatedAt（P0-2：不得用渲染时刻，否则字节全同被破坏）。
    opaque_tracks：I6 本地/文件曲目按歌单回填（追加末尾，保序稳定）。
    """
    playlists_data = []
    for pl_id, info in view.items():
        # 歌单 id：优先用该端 native id，缺失回退 canonical id（I4）
        plid = native_ids.get(pl_id, pl_id)
        tracks = [_render_cyshine_track(k, meta_pool) for k in info["tracks"]]  # 保留歌单内顺序
        if opaque_tracks:
            locals_ = opaque_tracks.get(str(plid))
            if locals_:
                tracks.extend(locals_)   # I6 原样回填（每首歌一个元素，字段原样）
        t = (pl_times or {}).get(pl_id)
        created = (t.get("created_at") if t else None) or generated_at
        updated = (t.get("updated_at") if t else None) or generated_at
        playlists_data.append({
            "version": 1,
            "id": plid,
            "name": info["name"],
            "tracks": tracks,
            "createdAt": created,
            "updatedAt": updated,
        })

    opaque = opaque or {}
    appearance = opaque.get("appearance")
    if not isinstance(appearance, dict):
        appearance = {"data": {"themeSeedArgb": 0}}   # 硬要求 2：必须是 JSON 整数
    music_sources = opaque.get("musicSources")
    if not isinstance(music_sources, dict):
        music_sources = {"data": []}

    return {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "sections": {
            # 硬要求：随空间 revision 单调推进；内容不变则不推进（docs/04 §4.4.1 / 评审 S3）
            "playlists": {"data": playlists_data, "modifiedAt": generated_at,
                          "revision": revision},
            "appearance": appearance,
            "musicSources": music_sources,
        },
    }


def _render_cyshine_track(key: str, meta_pool: Dict[str, dict]) -> dict:
    src, song_id = key.split(KEY_SEP, 1)
    meta = meta_pool.get(key, {})
    title = meta.get("title") or song_id
    singer = meta.get("singer") or ""
    music_id = f"{src}_{song_id}"
    return {
        "musicId": music_id,                                # 硬要求 3：<source>_<songId>
        "name": title,
        "singer": singer,
        "albumName": meta.get("album") or "",
        "source": src,
        "quality": meta.get("quality") or "",
        "picUrl": meta.get("pic_url") or "",
        "musicInfo": {
            "id": meta.get("music_info_id") or music_id,
            "name": title,
            "singer": singer,
            "source": src,
            "interval": _duration_ms_to_interval(meta.get("duration_ms")),  # 硬要求 4
            "meta": {"songId": song_id},                    # 硬要求 3：裸 id
        },
    }


# ---------------------------------------------------------------------------
# ceru-plugin（澜音插件）—— parse / render
#
# 澜音插件 SDK 缺"删歌/删歌单/建歌单"，capabilities().deleteTrack = false
# （docs/02 §2.2.2、docs/04 §4.4.3）。因此：
#   - parse：接受其提交的简单列表格式；
#   - render：把"本地应删的歌"放进待清理清单（不静默丢弃，A5）。
# ---------------------------------------------------------------------------

def parse_ceru(payload: dict) -> Tuple[dict, Dict[str, dict], dict, List[str]]:
    """澜音插件提交 → (submission, 元数据增量, opaque, warnings)。

    线格式：{ "playlists": [ { "id", "name", "tracks": [ {"source","songId","title","singer","album","durationMs"} ] } ] }
    """
    warnings: List[str] = []
    if not isinstance(payload, dict):
        raise ParseError("payload 必须是对象")
    raw = payload.get("playlists")
    if not isinstance(raw, list):
        raise ParseError("playlists 必须是数组")
    playlists, meta_delta = [], {}
    for item in raw:
        if not isinstance(item, dict):
            raise ParseError("playlist 元素必须是对象")
        pl_id = item.get("id") or item.get("native_id")
        name = item.get("name") or ""
        if not isinstance(pl_id, str) or not pl_id:
            raise ParseError(f"歌单缺 id: {item!r}")
        raw_tracks = item.get("tracks") or []
        tracks, meta = _ceru_tracks(raw_tracks)
        playlists.append({"native_id": pl_id, "name": name, "tracks": tracks})
        for k, v in meta.items():
            meta_delta.setdefault(k, {}).update({kk: vv for kk, vv in v.items() if vv is not None})
    submission = {"playlists": playlists, "opaque": None, "create_only": False}
    return submission, meta_delta, {"appearance": None, "musicSources": None}, warnings


def _ceru_tracks(raw_tracks) -> Tuple[List[dict], Dict[str, dict]]:
    tracks, meta = [], {}
    for tr in raw_tracks:
        if not isinstance(tr, dict):
            raise ParseError("track 必须是对象")
        src = tr.get("source"); sid = tr.get("songId")
        if not isinstance(src, str) or not src or not isinstance(sid, str) or not sid:
            raise ParseError(f"曲目缺 source/songId: {tr!r}")
        key = identity_key(src, sid)
        tracks.append({"source": src, "songId": sid})
        meta[key] = {
            "source": src,
            "title": tr.get("title"),
            "singer": tr.get("singer"),
            "album": tr.get("album"),
            "duration_ms": tr.get("durationMs"),
            "quality": tr.get("quality"),
            "pic_url": tr.get("picUrl"),
        }
    return tracks, meta


def render_ceru(view: Dict[str, dict], meta_pool: Dict[str, dict],
                native_ids: Dict[str, str], cleanup: List[dict],
                generated_at: str) -> dict:
    """引擎 view → 澜音格式 + 待清理清单（deleteTrack=false，A5）。"""
    playlists = []
    for pl_id, info in view.items():
        plid = native_ids.get(pl_id, pl_id)
        tracks = []
        for k in info["tracks"]:   # 保留歌单内顺序
            src, sid = k.split(KEY_SEP, 1)
            meta = meta_pool.get(k, {})
            tracks.append({
                "source": src, "songId": sid,
                "title": meta.get("title") or sid,
                "singer": meta.get("singer") or "",
                "album": meta.get("album") or "",
                "durationMs": meta.get("duration_ms"),
                "quality": meta.get("quality") or "",
                "picUrl": meta.get("pic_url") or "",
            })
        playlists.append({"id": plid, "name": info["name"], "tracks": tracks})
    return {
        "schemaVersion": 1,
        "generatedAt": generated_at,
        "playlists": playlists,
        "cleanup": cleanup,   # 本地应删但该端无删歌能力 → 待清理清单（A5）
    }


# 方言能力表（docs/04 §4.3 capabilities()）
CAPABILITIES = {
    "cyshine-v1": {"delete_track": True, "delete_playlist": True,
                   "create_playlist": True, "reorder": False},
    # 2026-10-07 用户拍板：澜音按"能删"处理——插件自身删不掉本地副本是**客户端**要解决的
    # 问题（其开发者正在处理；用户手动删掉即可），枢纽不再为它走"缺席不判删"的特殊路径。
    # 影响：澜音提交里"少了"的曲目按删除处理；交付即 ack 后若又回推，由 merge 的 carried
    # 撤销确认（engine.py 的 t.acked.discard），不会让墓碑被错误 GC。
    "ceru-plugin": {"delete_track": True, "delete_playlist": True,
                    "create_playlist": False, "reorder": False},
}
