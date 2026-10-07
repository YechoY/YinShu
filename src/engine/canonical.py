"""canonical-v1 数据模型（M1 PoC 内部表示）。

对齐 spec/canonical-v1.md 的核心语义：
  - 曲目身份 = (source, songId)，身份键字符串 `<source>:<songId>`（I1/I2）
  - 曲目池去重：同一身份只存一条记录，多歌单引用同一条（P5/I3）
  - 歌单身份 = 规范 id（pl_<ULID>），跨端配对靠 dialect+nativeId 映射（I4）
  - 删除用墓碑表达，物理不删（T2）
  - 本地文件/无法表达的私有内容 → opaque，原样携带、不参与合并、计入 view_hash（I6/E5）
  - 确定性哈希（对象键排序，PF5）
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

# Crockford Base32 字母表（不含 I/L/O/U），与 spec 校验器 ULID_RE 一致
_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid(prefix: str) -> str:
    """生成形如 `{prefix}_<26位Crockford>` 的稳定记录键（记录键，非身份键）。"""
    return prefix + "_" + "".join(random.choice(_ULID_ALPHABET) for _ in range(26))


def identity_key(source: str, song_id: str) -> str:
    """曲目身份键 = `<source>:<songId>`（I1）。跨源同名 id 互不相同。"""
    return f"{source}:{song_id}"


def canonical_hash(obj: object) -> str:
    """确定性内容哈希：字典键排序 + 稳定 JSON + UTF-8（PF5）。

    顺序（playlists[] 顺序、trackIds[] 顺序）不参与合并判定、也不进哈希（T5/E3），
    由调用方在构造待哈希对象时排除顺序敏感字段。
    """
    def norm(o):
        if isinstance(o, dict):
            return {k: norm(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [norm(x) for x in o]
        if isinstance(o, set):
            return sorted(norm(x) for x in o)
        if isinstance(o, frozenset):
            return sorted(norm(x) for x in o)
        return o
    s = json.dumps(norm(obj), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


@dataclass
class Track:
    """曲目池中的一条记录。tr_id 是记录键，identity 才是身份。"""
    tr_id: str
    source: str
    song_id: str
    title: str
    artists: List[dict] = field(default_factory=list)
    added_at: Optional[str] = None
    updated_at: Optional[str] = None
    deleted_at: Optional[str] = None          # 非空 = 墓碑（空间级：这首歌被删除）
    fields: dict = field(default_factory=dict)  # 展示/品质/平台等（不参与合并判定）

    @property
    def key(self) -> str:
        return identity_key(self.source, self.song_id)


@dataclass
class Playlist:
    pl_id: str
    name: str
    track_ids: List[str] = field(default_factory=list)  # 有序 tr_id 引用（P5）
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    deleted_at: Optional[str] = None          # 非空 = 歌单墓碑
    platforms: dict = field(default_factory=dict)  # dialect -> {native_id, native_name}
    fields: dict = field(default_factory=dict)


@dataclass
class Tombstone:
    """删除墓碑。key：曲目用身份键 (source:songId)，歌单用 pl_id（I3/E4）。

    created_at_revision：删除发生时 space.revision，用于确认水位 GC（D21/T3）。
    acked：看过删除后视图的客户端（交付 ack + 提交确认，P0-B）。
    carried：最近一次提交仍带着该键的客户端——还在回推残留 ⇒ 从 acked 撤销（第四轮 §2.4.2）。
    """
    key: str
    element: str               # 'track' | 'playlist'
    deleted_at: str
    origin_client: str
    created_at_revision: int
    target_name: Optional[str] = None
    acked: Set[str] = field(default_factory=set)   # 已看过删除结果（交付/提交确认）
    carried: Set[str] = field(default_factory=set) # 最近提交仍带该键 → 回推中，撤销确认

    def is_live(self) -> bool:
        return True  # 墓碑存活由 GC（确认水位）决定，不以时间为判据（D21）


class InvalidPayload(Exception):
    """客户端提交的负载结构损坏（对应 docs/05 §5.6：解析失败 → 拒绝，基线不动）。"""
