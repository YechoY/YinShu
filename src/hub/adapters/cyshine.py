"""cyshine-v1（栖弦）方言适配器 —— 原 src/hub/adapters.py 原样搬移（第六轮 §1.2）。

函数体与旧文件逐字节一致，仅新增模块级 DIALECT 注册表条目与 RenderContext 薄包装。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .base import KEY_SEP, ParseError, RenderContext, Dialect, _duration_ms_to_interval, _interval_to_duration_ms, identity_key


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
    # P0-A：客户端声明的时刻由适配器交给引擎（api.py 只读 submission.client_modified_at，
    # 不认任何方言字段名——加客户端不改枢纽）。
    modified_at = pl_section.get("modifiedAt")
    if isinstance(modified_at, str) and modified_at:
        submission["client_modified_at"] = modified_at
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


def _render(ctx: RenderContext) -> dict:
    """RenderContext 薄包装 → render_cyshine（第 4 参 opaque、另带 revision/pl_times/opaque_tracks）。"""
    return render_cyshine(ctx.view, ctx.meta_pool, ctx.native_ids, ctx.opaque,
                          ctx.revision, ctx.generated_at, pl_times=ctx.playlist_times,
                          opaque_tracks=ctx.opaque_tracks)


DIALECT = Dialect(
    name="cyshine-v1",
    roots=("CyShineMusic",),
    files=("sync-v1.json",),
    parse=parse_cyshine,
    render=_render,
    capabilities={
        "delete_track": True,
        "delete_playlist": True,
        "create_playlist": True,
        "reorder": False,
    },
    empty_view_on_no_baseline=False,
    file_fallback=False,
)
