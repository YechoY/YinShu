"""canonical-v1 数据模型（音枢内部表示）。

对齐 spec/canonical-v1.md 的核心语义：
  - 曲目身份 = (source, songId)，身份键字符串 `<source>:<songId>`（I1/I2）
  - 曲目池去重：同一身份只存一条记录，多歌单引用同一条（P5/I3）
  - 歌单身份 = 规范 id（pl_<ULID>），跨端配对靠 dialect+nativeId 映射（I4）
  - 删除判定（D34）：客户端"段 updatedAt 变了 + 缺席"= 删；服务端以 deleted_at 做内部标记，物理不删
  - 本地文件/无法表达的私有内容 → opaque，原样携带、不参与合并、计入 view_hash（I6/E5）
  - 确定性哈希（对象键排序，PF5）
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from typing import List, Optional

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
    deleted_at: Optional[str] = None          # 非空 = 已删（服务端内部标记，物理不删；判定由引擎按"段 updatedAt 变 + 缺席"做出）
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
    deleted_at: Optional[str] = None          # 非空 = 已删（服务端内部标记，物理不删；判定由引擎按"段 updatedAt 变 + 缺席"做出）
    platforms: dict = field(default_factory=dict)  # dialect -> {native_id, native_name}
    fields: dict = field(default_factory=dict)


class InvalidPayload(Exception):
    """客户端提交的负载结构损坏（对应 docs/05 §5.6：解析失败 → 拒绝，基线不动）。"""
