"""测试辅助：构造客户端提交负载。"""
from __future__ import annotations

from typing import List, Optional, Tuple, Union


def tr(source: str, song_id: str) -> dict:
    return {"source": source, "songId": song_id}


def pl(native_id: str, name: str, *tracks: Tuple[str, str]) -> dict:
    return {"native_id": native_id, "name": name,
            "tracks": [tr(s, i) for s, i in tracks]}


def sub(*playlists: dict, opaque: Optional[dict] = None,
        create_only: bool = False) -> dict:
    return {"playlists": list(playlists), "opaque": opaque, "create_only": create_only}
