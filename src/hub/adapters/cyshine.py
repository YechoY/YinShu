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
        # 第十五轮：带上歌单级 updatedAt（用户数据的段修改时刻）——引擎用它区分
        # "真编辑过该歌单"（缺席曲目=删除意图）与"未采纳远端内容"（缺席≠删除，M1 保护）。
        # 格式容错：栖弦历史数据 updatedAt 有带 Z / 不带 Z 两种，这里只透传字符串，
        # 引擎侧仅做"是否变化"比较，不解析时刻。
        updated_at = item.get("updatedAt")
        playlists.append({"native_id": str(pl_id), "name": name, "tracks": tracks,
                          "modified_at": updated_at if isinstance(updated_at, str) and updated_at else None})
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
    # songId 可能是 int（网易/QQ 等平台 id 在 JSON 里是数字），统一转 str
    if isinstance(song_id, int) and not isinstance(song_id, bool):
        song_id = str(song_id)
    if not isinstance(src, str) or not src or not isinstance(song_id, str) or not song_id:
        return None   # 本地文件 / 无平台 id → I6 opaque 原样携带

    raw = minfo.get("meta")
    show_meta = {
        "source": src,
        "title": track.get("name") or minfo.get("name"),
        "singer": track.get("singer") or minfo.get("singer"),
        "album": track.get("albumName"),
        "quality": track.get("quality"),
        "pic_url": track.get("picUrl"),
        "music_info_id": minfo.get("id"),
        "duration_ms": _interval_to_duration_ms(minfo.get("interval")),
        # 完整 meta 留档（音质明细/hash/albumId 等平台扩展字段），渲染时回填进
        # musicInfo.meta——否则自家提交的数据经枢纽渲染回来也只剩裸 songId。
        "raw_meta": dict(raw) if isinstance(raw, dict) and raw else None,
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
    # 第十四轮修复：默认值补全只针对**缺失**的 section——有 data 但缺 modifiedAt 的
    # 真实数据必须保留（只补时间戳），此前一刀切替换会把用户上传的主题/音源丢成默认 0。
    appearance = opaque.get("appearance")
    if not isinstance(appearance, dict) or "data" not in appearance:
        appearance = {"data": {"themeSeedArgb": 0}, "modifiedAt": generated_at}
    elif "modifiedAt" not in appearance:
        appearance = {**appearance, "modifiedAt": generated_at}
    music_sources = opaque.get("musicSources")
    if not isinstance(music_sources, dict) or "data" not in music_sources:
        music_sources = {"data": [], "modifiedAt": generated_at}
    elif "modifiedAt" not in music_sources:
        music_sources = {**music_sources, "modifiedAt": generated_at}

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
    # 统一完整格式：musicInfo.meta 必须带封面/专辑/音质明细与平台扩展字段——
    # 栖弦播放页从 music.meta.picUrl 取封面、meta.qualitys 取可切换音质，缺失即
    # "列表页有封面（读顶层 picUrl）、进播放详情页封面和音质全没"（2026-10-10 真机）。
    # 数据可能来自任意方言：优先栖弦自报的 raw_meta，其次洛雪留档的 lx_raw_meta。
    # 注意：raw_meta 存在但只有 songId（栖弦对非本音源歌曲无法解析完整 meta）时，
    # 不能 break 掉——必须继续合并 lx_raw_meta 的音质明细，否则播放页音质列表只剩
    # 合成下限（2026-10-10 排查：栖弦在线 flac 播成 128k 的枢纽侧根因之一）。
    music_meta: dict = {}
    for raw_key in ("raw_meta", "lx_raw_meta"):
        raw = meta.get(raw_key)
        if isinstance(raw, dict) and raw:
            for _k, _v in raw.items():
                music_meta.setdefault(_k, _v)   # raw_meta 优先，lx_raw_meta 补缺
    music_meta["songId"] = song_id                      # 规范身份覆盖陈旧值
    music_meta["albumName"] = meta.get("album") or music_meta.get("albumName") or ""
    music_meta["picUrl"] = meta.get("pic_url") or music_meta.get("picUrl") or ""
    # 平台原生数字 id（源脚本取 URL 用）：未知就不写——写 <source>_<id> 前缀串
    # 反而让脚本拿着垃圾 id 去请求（脚本会自动回退 songId）
    native_id = meta.get("music_info_id") or meta.get("lx_num_id")
    if native_id in (None, "", 0, "0"):
        music_meta.pop("id", None)
    else:
        music_meta["id"] = native_id
    _ensure_qualitys_array(music_meta, meta.get("quality"))
    return {
        "musicId": music_id,                                # 硬要求 3：<source>_<songId>
        "name": title,
        "singer": singer,
        "albumName": music_meta["albumName"],
        "source": src,
        "quality": meta.get("quality") or "",
        "picUrl": music_meta["picUrl"],
        "musicInfo": {
            "id": meta.get("music_info_id") or music_id,
            "name": title,
            "singer": singer,
            "source": src,
            "interval": _duration_ms_to_interval(meta.get("duration_ms")),  # 硬要求 4
            "meta": music_meta,
        },
    }


def _ensure_qualitys_array(music_meta: dict, fallback_quality=None) -> None:
    """保证 meta.qualitys 是非空数组（播放页音质切换直接解引用它）。

    有 qualitys/_qualitys 明细就保真；只有枢纽已知最高档时合成 [最高档, 128k 下限]。
    音质代码与栖弦 Quality 枚举同域（master/hires/flac/320k/128k...），未知代码客户端自行丢弃。
    """
    qs = music_meta.get("qualitys")
    if isinstance(qs, list) and qs:
        return
    qm = music_meta.get("_qualitys")
    if isinstance(qm, dict) and qm:
        music_meta["qualitys"] = [
            {"type": t, "size": v.get("size")}
            if isinstance(v, dict) and v.get("size") is not None else {"type": t}
            for t, v in qm.items() if t is not None
        ]
        return
    q = fallback_quality if fallback_quality else "128k"
    music_meta["qualitys"] = ([{"type": q}] +
                              ([{"type": "128k"}] if str(q) != "128k" else []))


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
