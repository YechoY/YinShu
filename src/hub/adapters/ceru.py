"""ceru-plugin（澜音插件）方言适配器 —— 原 src/hub/adapters.py 原样搬移（第六轮 §1.2）。

函数体与旧文件逐字节一致，仅新增模块级 DIALECT 注册表条目与 RenderContext 薄包装。
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from .base import KEY_SEP, ParseError, RenderContext, Dialect, identity_key


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


def _render(ctx: RenderContext) -> dict:
    """RenderContext 薄包装 → render_ceru（第 4 参是 cleanup）。"""
    return render_ceru(ctx.view, ctx.meta_pool, ctx.native_ids,
                       ctx.pending_cleanup, ctx.generated_at)


DIALECT = Dialect(
    name="ceru-plugin",
    roots=("ceru",),
    files=("sync-v1.json",),
    parse=parse_ceru,
    render=_render,
    capabilities={
        # 2026-10-07 用户拍板：澜音按"能删"处理——插件自身删不掉本地副本是**客户端**要解决的
        # 问题（其开发者正在处理；用户手动删掉即可），枢纽不再为它走"缺席不判删"的特殊路径。
        # 影响：澜音提交里"少了"的曲目按删除处理；交付即 ack 后若又回推，由 merge 的 carried
        # 撤销确认（engine.py 的 t.acked.discard），不会让墓碑被错误 GC。
        "delete_track": True,
        "delete_playlist": True,
        "create_playlist": False,
        "reorder": False,
    },
    empty_view_on_no_baseline=True,
    file_fallback=False,
    # 第六轮 §1 回归修复：澜音插件 SDK 缺删歌 API，本地删不掉、看过删除视图必带回残留。
    # 故"看过视图"不计入确认水位（deliver 不 ack → 墓碑保留 → 残留回推被压制不复活）；
    # 其确认只能来自 merge 的"提交不再带该键"。栖弦为 True（看过即采纳、回推走显式恢复）。
    deliver_ack_on_view=False,
)
