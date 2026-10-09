"""测试辅助：构造客户端提交负载。"""
from __future__ import annotations

from typing import List, Optional, Tuple, Union


def tr(source: str, song_id: str) -> dict:
    return {"source": source, "songId": song_id}


def pl(native_id: str, name: str, *tracks: Tuple[str, str],
       modified_at: Optional[str] = None) -> dict:
    """构造歌单提交；modified_at 模拟客户端歌单级修改时刻（如栖弦 updatedAt，第十五轮）。"""
    out = {"native_id": native_id, "name": name,
           "tracks": [tr(s, i) for s, i in tracks]}
    if modified_at is not None:
        out["modified_at"] = modified_at
    return out


def sub(*playlists: dict, opaque: Optional[dict] = None,
        create_only: bool = False) -> dict:
    return {"playlists": list(playlists), "opaque": opaque, "create_only": create_only}
