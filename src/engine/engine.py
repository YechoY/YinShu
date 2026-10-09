"""合并引擎（简化版 D34）—— 删除就是删除，加回来就是加回来，各有增删就合并。

唯一规则：
  - 段 updatedAt 变了 + 曲目缺席 = 删除
  - 段没变 + 曲目缺席 = 不动（栖弦整段覆盖客户端可能未采纳远端内容，M1 保护）
  - 在场的全部并集合并

无墓碑、无冷静期、无 suspects、无安全阀、无 I2′ 闸门、无 R1 判据。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from .canonical import (
    InvalidPayload,
    Playlist,
    Track,
    canonical_hash,
    identity_key,
    new_ulid,
)


@dataclass
class ServedBaseline:
    """交付基线：上次真正交付给客户端的视图。"""
    view: Dict[str, dict] = field(default_factory=dict)
    served_revision: int = 0
    view_hash: Optional[str] = None
    stamp: str = ""
    opaque_hash: Optional[str] = None


@dataclass
class Client:
    client_id: str
    dialect: str
    identity_verified: bool = False
    retired: bool = False
    can_delete: bool = True
    full_view_submit: bool = False
    round_trip_sources: Optional[frozenset] = None
    base_served: Optional[ServedBaseline] = None
    base_submitted: Optional[Dict[str, dict]] = None
    opaque: dict = field(default_factory=dict)
    first_seen_revision: int = 0
    last_seen: Optional[str] = None
    last_delivered_stamp: Optional[str] = None
    last_submitted_stamp: Optional[str] = None


@dataclass
class MergeResult:
    status: int
    view: Dict[str, dict] = field(default_factory=dict)
    pending_cleanup: List[dict] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    canonical_mutated: bool = False
    stamp: str = ""


class SyncSpace:
    """一份空间的存储与合并引擎。"""

    KNOWN_DIALECTS = frozenset({"cyshine-v1", "ceru-plugin"})

    @classmethod
    def set_known_dialects(cls, names) -> None:
        cls.KNOWN_DIALECTS = frozenset(names)

    def __init__(self, space_id: str = "space-a") -> None:
        self.space_id = space_id
        self.revision = 0
        self.playlists: Dict[str, Playlist] = {}
        self.playlist_order: List[str] = []
        self.tracks: Dict[str, Track] = {}
        self.identity_to_tr: Dict[str, str] = {}
        self.playlist_aliases: Dict[Tuple[str, str], str] = {}
        self.clients: Dict[str, Client] = {}
        self.journal: List[dict] = []
        self.quarantine: Dict[str, dict] = {}
        self.content_changed_at: Optional[str] = None
        self.order_changed_at: Optional[str] = None
        self.sort_tag: int = 0

    # ---- 客户端注册 -------------------------------------------------------
    def register_client(
        self, client_id: str, dialect: str = "cyshine-v1",
        identity_verified: bool = False, can_delete: bool = True,
        full_view_submit: bool = False,
        round_trip_sources: Optional[frozenset] = None,
    ) -> Client:
        if client_id in self.clients:
            raise ValueError(f"client {client_id!r} 已注册")
        c = Client(client_id=client_id, dialect=dialect,
                   identity_verified=identity_verified, can_delete=can_delete,
                   full_view_submit=full_view_submit,
                   round_trip_sources=round_trip_sources,
                   first_seen_revision=self.revision)
        self.clients[client_id] = c
        return c

    # ---- 提交负载解析 -----------------------------------------------------
    def _parse_tracks(self, raw_tracks) -> List[str]:
        keys: List[str] = []
        seen: Set[str] = set()
        for t in raw_tracks:
            if not isinstance(t, dict):
                raise InvalidPayload("track 必须是对象")
            src = t.get("source"); sid = t.get("songId")
            if not isinstance(src, str) or not src or not isinstance(sid, str) or not sid:
                raise InvalidPayload(f"track 缺合法 source/songId: {t!r}")
            key = identity_key(src, sid)
            if key not in seen:
                seen.add(key)
                keys.append(key)
        return keys

    def _resolve_playlist(self, client: Client, native_id: str, name: str) -> str:
        """歌单身份解析：native-id 别名 → 名字别名（仅首次、仅唯一）→ 新建。"""
        alias_key = (client.dialect, native_id)
        if alias_key in self.playlist_aliases:
            pl_id = self.playlist_aliases[alias_key]
            pl = self.playlists.get(pl_id)
            if pl is not None:
                self._note_native(pl, client.dialect, native_id, name)
                return pl_id
        name = (name or "").strip()
        if name:
            hit = [pid for pid, pl in self.playlists.items()
                   if pl.deleted_at is None and pl.name == name]
            if len(hit) == 1:
                pl_id = hit[0]
                self.playlist_aliases[alias_key] = pl_id
                self._note_native(self.playlists[pl_id], client.dialect, native_id, name)
                return pl_id
        pl_id = new_ulid("pl")
        now = self._now()
        pl = Playlist(pl_id=pl_id, name=name, created_at=now, updated_at=now)
        pl.platforms[client.dialect] = {"native_id": native_id, "native_name": name}
        self.playlists[pl_id] = pl
        self.playlist_order.append(pl_id)
        self.playlist_aliases[alias_key] = pl_id
        return pl_id

    @staticmethod
    def _note_native(pl: "Playlist", dialect: str, native_id: str, name: str) -> None:
        pf = pl.platforms.setdefault(dialect, {})
        if not pf.get("native_id"):
            pf["native_id"] = native_id
        pf["native_name"] = name

    def _ingest(self, client: Client, submission: dict) -> Dict[str, dict]:
        """把客户端提交解析成 canonical 视图 {pl_id: {"name", "tracks", "modified_at"}}。"""
        if not isinstance(submission, dict):
            raise InvalidPayload("submission 必须是对象")
        playlists = submission.get("playlists")
        if not isinstance(playlists, list):
            raise InvalidPayload("submission.playlists 必须是数组")
        parsed: List[Tuple[str, str, Tuple[str, ...], Optional[str]]] = []
        for pl in playlists:
            if not isinstance(pl, dict):
                raise InvalidPayload("playlist 必须是对象")
            native_id = pl.get("native_id")
            name = pl.get("name") or ""
            if not isinstance(native_id, str) or not native_id:
                raise InvalidPayload(f"playlist 缺 native_id: {pl!r}")
            raw_tracks = pl.get("tracks")
            if not isinstance(raw_tracks, list):
                raise InvalidPayload(f"playlist {native_id!r} 的 tracks 必须是数组")
            keys = self._parse_tracks(raw_tracks)
            mod = pl.get("modified_at")
            parsed.append((native_id, name, keys, mod if isinstance(mod, str) and mod else None))
        view: Dict[str, dict] = {}
        for native_id, name, keys, mod in parsed:
            pl_id = self._resolve_playlist(client, native_id, name)
            view[pl_id] = {"name": name, "tracks": keys, "modified_at": mod}
        return view

    @staticmethod
    def _now() -> str:
        from datetime import datetime, timezone
        s = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        return s[:23] + "Z"

    @staticmethod
    def _parse_stamp(stamp: Optional[str]):
        if not stamp:
            return None
        from datetime import datetime, timezone
        for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                return datetime.strptime(stamp, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

    @classmethod
    def _bump(cls, stamp: str, millis: int = 1) -> str:
        dt = cls._parse_stamp(stamp)
        if dt is None:
            return stamp
        from datetime import timedelta
        s = (dt + timedelta(milliseconds=millis)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        return s[:23] + "Z"

    # ---- 渲染 -------------------------------------------------------------
    def render_for(self, client: Client) -> Tuple[Dict[str, dict], List[dict]]:
        view: Dict[str, dict] = {}
        for pl_id in self.playlist_order:
            pl = self.playlists.get(pl_id)
            if pl is None or pl.deleted_at is not None:
                continue
            live = []
            for tr_id in pl.track_ids:
                tr = self.tracks.get(tr_id)
                if tr is not None and tr.deleted_at is None:
                    live.append(tr.key)
            view[pl_id] = {"name": pl.name, "tracks": tuple(live)}
        return view, []

    def _view_hash(self, client: Client, view: Dict[str, dict]) -> str:
        norm = {pl_id: {"name": e["name"], "tracks": sorted(e["tracks"])}
                for pl_id, e in view.items()}
        if client.opaque:
            norm["__opaque__"] = client.opaque
        return canonical_hash(norm)

    def _submission_matches(self, client: Client, view: Dict[str, dict]) -> bool:
        sub = client.base_submitted
        if not sub:
            return True
        for pl_id, e in sub.items():
            delivered = view.get(pl_id)
            if delivered is None:
                return False
            if set(e.get("tracks") or ()) != set(delivered.get("tracks") or ()):
                return False
        return True

    def _next_stamp(self, client: Client, view: Dict[str, dict]) -> str:
        bsrv = client.base_served
        if bsrv is not None and bsrv.stamp:
            same_view = bsrv.view == view
            same_opaque = (bsrv.opaque_hash == canonical_hash(client.opaque or {}))
            if same_view and same_opaque and self._submission_matches(client, view):
                return bsrv.stamp
            base_src = bsrv.stamp
        else:
            base_src = ""
        base_src = max([x for x in (base_src, client.last_submitted_stamp or "") if x]
                       or ["1970-01-01T00:00:00.000Z"])
        base_dt = self._parse_stamp(base_src) or self._parse_stamp("1970-01-01T00:00:00.000Z")
        m = None
        for c in (self.content_changed_at, self.order_changed_at):
            dt = self._parse_stamp(c)
            if dt is not None and (m is None or dt > m):
                m = dt
        if m is None or m <= base_dt:
            from datetime import timedelta
            return (base_dt + timedelta(milliseconds=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:23] + "Z"
        return m.strftime("%Y-%m-%dT%H:%M:%S.%fZ")[:23] + "Z"

    def deliver(self, client_id: str, now: Optional[str] = None) -> MergeResult:
        client = self.clients[client_id]
        if client.base_served is None and client.base_submitted is None:
            return MergeResult(status=404, view={}, meta={"error": "no baseline"})
        now = now or self._now()
        self._mark_retired(now)
        view, cleanup = self.render_for(client)
        stamp = self._next_stamp(client, view)
        served = ServedBaseline(
            view=view, served_revision=self.revision,
            view_hash=self._view_hash(client, view), stamp=stamp,
            opaque_hash=canonical_hash(client.opaque or {}),
        )
        client.base_served = served
        client.last_delivered_stamp = stamp
        self.journal.append({"client": client_id, "revision": self.revision,
                             "action": "deliver", "at": type(self)._now()})
        return MergeResult(status=200, view=view, pending_cleanup=cleanup, stamp=stamp)

    def peek_view(self, dialect: str, can_delete: bool = True):
        tmp = Client(client_id=f"__peek__:{dialect}", dialect=dialect,
                     identity_verified=True, can_delete=can_delete)
        view, cleanup = self.render_for(tmp)
        cands = [x for x in (self.content_changed_at, self.order_changed_at) if x]
        stamp = max(cands) if cands else self._now()
        return view, cleanup, stamp

    # ---- 内部：曲目/歌单操作 ---------------------------------------------
    def _add_track(self, key: str, now: str) -> Tuple[Track, bool]:
        if key in self.identity_to_tr:
            tr = self.tracks[self.identity_to_tr[key]]
            if tr.deleted_at is not None:
                tr.deleted_at = None
                tr.updated_at = now
                return tr, True
            return tr, False
        src, _, sid = key.partition(":")
        tr = Track(tr_id=new_ulid("tr"), source=src, song_id=sid,
                   title=sid, artists=[{"name": "?"}], added_at=now, updated_at=now)
        self.tracks[tr.tr_id] = tr
        self.identity_to_tr[key] = tr.tr_id
        return tr, True

    def _append_to_playlist(self, pl_id: str, tr_id: str, now: str) -> bool:
        pl = self.playlists[pl_id]
        if tr_id not in pl.track_ids:
            pl.track_ids.append(tr_id)
            pl.updated_at = now
            return True
        return False

    def _ensure_playlist(self, pl_id: str, name: str, now: str) -> bool:
        pl = self.playlists.get(pl_id)
        changed = False
        if pl is None:
            self.playlists[pl_id] = Playlist(pl_id=pl_id, name=name,
                                             created_at=now, updated_at=now)
            self.playlist_order.append(pl_id)
            changed = True
        else:
            if pl.deleted_at is not None:
                pl.deleted_at = None
                changed = True
                if pl_id not in self.playlist_order:
                    self.playlist_order.append(pl_id)
            if pl.name != name:
                pl.name = name
                changed = True
            if changed:
                pl.updated_at = now
        return changed

    def _remove_track_from_all_playlists(self, key: str, now: str) -> bool:
        """删除一首歌：标记 deleted_at，从所有歌单摘除。"""
        if key not in self.identity_to_tr:
            return False
        tr_id = self.identity_to_tr[key]
        tr = self.tracks[tr_id]
        if tr.deleted_at is not None:
            return False
        tr.deleted_at = now
        tr.updated_at = now
        for pl in self.playlists.values():
            if tr_id in pl.track_ids:
                pl.track_ids = [x for x in pl.track_ids if x != tr_id]
                pl.updated_at = now
        return True

    def _delete_playlist_internal(self, pl_id: str, now: str) -> bool:
        pl = self.playlists.get(pl_id)
        if pl is None or pl.deleted_at is not None:
            return False
        pl.deleted_at = now
        pl.updated_at = now
        if pl_id in self.playlist_order:
            self.playlist_order.remove(pl_id)
        return True

    # ---- 合并主流程 -------------------------------------------------------
    def merge(self, client_id: str, submission: dict, now: Optional[str] = None,
              client_modified_at: Optional[str] = None) -> MergeResult:
        """PUT 语义：解析 → 差分 → 合并 → 落库 → 渲染回该客户端。

        唯一规则：段 updatedAt 变了 + 缺席 = 删；段没变 + 缺席 = 不动；在场的并集。
        """
        client = self.clients[client_id]
        now = now or self._now()
        self._mark_retired(now)
        if client_modified_at:
            client.last_submitted_stamp = client_modified_at
        before_pl = set(self.playlists)

        if not isinstance(submission, dict):
            return MergeResult(status=400, view={}, meta={"error": "submission 必须是对象"})

        if client.dialect not in self.KNOWN_DIALECTS:
            self.quarantine[client_id] = {"payload": submission, "first_seen": now}
            return MergeResult(status=200, view={}, meta={"isolated": True, "dialect": client.dialect})

        try:
            view = self._ingest(client, submission)
        except InvalidPayload as exc:
            return MergeResult(status=400, view={}, meta={"error": str(exc)})
        client.last_seen = now
        client.opaque = submission.get("opaque") or {}

        if client.base_submitted is None:
            return self._adopt_initial(client, view, now, before_pl)

        bsrv = client.base_served
        base_view = bsrv.view if bsrv is not None else {}
        base_keys = set(base_view.keys())
        cur_keys = set(view.keys())

        # 可信客户端（身份已验证 + 有删除能力 + 未退休）
        trusted = (client.identity_verified and client.can_delete and not client.retired)
        full_view = bool(client.full_view_submit and client.identity_verified
                         and not client.retired and client.can_delete)

        # 歌单级删除判据：
        #   可信客户端 owned_pl = base_served ∪ base_submitted（交付过或自己提交过 = 拥有过）
        #   不可信 owned_pl = base_served ∩ base_submitted（保守：两边都有才算）
        sub_view = client.base_submitted or {}
        if full_view or trusted:
            owned_pl = base_keys | set(sub_view.keys())
        else:
            owned_pl = base_keys & set(sub_view.keys())

        # 曲目级删除判据：
        #   可信客户端：owned_t 初始为空（M1 保护：整段覆盖客户端可能未采纳远端内容）。
        #   段 updatedAt 变了 → base_served 里缺席的都算删（用户真编辑过该歌单）。
        #   不可信客户端：base_served ∩ base_submitted（保守交集）。
        owned_t: Dict[str, Set[str]] = {}
        for pl in owned_pl:
            base = set(base_view.get(pl, {}).get("tracks", ()))
            if full_view and client.round_trip_sources is not None:
                # 洛雪 full_view：交付过的且音源在白名单内的 = 拥有过
                owned_t[pl] = {k for k in base if k.split(":", 1)[0] in client.round_trip_sources}
            elif not trusted:
                sub = set(sub_view[pl].get("tracks", ())) if pl in sub_view else set()
                owned_t[pl] = base & sub
            else:
                owned_t[pl] = set()
        if trusted:
            for pl in owned_pl:
                cur_meta = view.get(pl) or {}
                prev_meta = sub_view.get(pl) or {}
                cur_mod = cur_meta.get("modified_at")
                prev_mod = prev_meta.get("modified_at")
                # 段动过 = cur_mod 存在且(prev_mod 缺失或两者不同)。
                # 都缺失 → 段原样回传（M1 保护只靠 base ∩ sub，不额外判删）。
                if not cur_mod or (prev_mod and cur_mod == prev_mod):
                    continue
                base = set(base_view.get(pl, {}).get("tracks", ()))
                if not base:
                    continue
                srcs = client.round_trip_sources
                extra = {k for k in base
                         if (srcs is None or k.split(":", 1)[0] in srcs)
                         and k not in set(cur_meta.get("tracks", ()))}
                if extra:
                    owned_t.setdefault(pl, set()).update(extra)

        removed_pl = owned_pl - cur_keys

        added_t: Dict[str, Set[str]] = {}
        removed_t: Dict[str, Set[str]] = {}
        for pl in base_keys | cur_keys:
            cur = set(view.get(pl, {}).get("tracks", ()))
            base = set(base_view.get(pl, {}).get("tracks", ()))
            added_t[pl] = cur - base
            if pl in removed_pl:
                removed_t[pl] = set()
            else:
                removed_t[pl] = owned_t.get(pl, set()) - cur

        # never_owned：缺席但从未拥有过 → 不判删（诊断字段）
        never_owned_pl = (base_keys - cur_keys) - owned_pl
        never_owned_t: Dict[str, Set[str]] = {}
        for pl in base_keys:
            base = set(base_view[pl].get("tracks", ()))
            owned = owned_t.get(pl, set())
            cur = set(view.get(pl, {}).get("tracks", ()))
            never_owned_t[pl] = (base - cur) - owned

        # ---- 事务（单写者） ----
        mutated = False

        # 新增歌单（并集）
        added_playlists: Set[str] = set()
        for pl in sorted(added_pl := (cur_keys - base_keys)):
            if self._ensure_playlist(pl, view[pl]["name"], now):
                mutated = True
                added_playlists.add(pl)

        # 新增曲目（并集 + 去重）
        added_head: Dict[str, List[str]] = {}
        added_tracks: Dict[str, Set[str]] = {}
        for pl in sorted(view.keys()):
            for key in view[pl]["tracks"]:
                tr, ch = self._add_track(key, now)
                inserted = self._append_to_playlist(pl, tr.tr_id, now)
                if inserted or ch:
                    mutated = True
                    added_tracks.setdefault(pl, set()).add(key)
                if inserted:
                    added_head.setdefault(pl, []).append(tr.tr_id)

        # 歌单改名
        for pl in sorted(view.keys()):
            if pl not in self.playlists or self.playlists[pl].deleted_at is not None:
                continue
            if self.playlists[pl].name != view[pl]["name"]:
                self.playlists[pl].name = view[pl]["name"]
                self.playlists[pl].updated_at = now
                mutated = True

        # 删除
        removed_playlists: Set[str] = set()
        for pl in sorted(removed_pl):
            if self._delete_playlist_internal(pl, now):
                mutated = True
                removed_playlists.add(pl)
        removed_tracks: Dict[str, Set[str]] = {}
        for pl in sorted(removed_t.keys()):
            for key in sorted(removed_t[pl]):
                if self._remove_track_from_all_playlists(key, now):
                    mutated = True
                    removed_tracks.setdefault(pl, set()).add(key)

        # 排序同步：集合不变但顺序不同 → 应用提交顺序
        reordered = False
        for pl in view.keys():
            pl_ = self.playlists.get(pl)
            if pl_ is None or pl_.deleted_at is not None:
                continue
            cur_live = [tid for tid in pl_.track_ids
                        if tid in self.tracks and self.tracks[tid].deleted_at is None]
            desired = []
            for key in view[pl]["tracks"]:
                tid = self.identity_to_tr.get(key)
                if tid is not None:
                    tr = self.tracks.get(tid)
                    if tr is not None and tr.deleted_at is None:
                        desired.append(tid)
            if set(cur_live) == set(desired) and desired and desired != cur_live:
                pl_.track_ids = desired
                pl_.updated_at = now
                reordered = True

        # 新增置顶
        for pl, tids in added_head.items():
            pl_ = self.playlists.get(pl)
            if pl_ is None or pl_.deleted_at is not None:
                continue
            head = [tid for tid in tids if tid in pl_.track_ids]
            if not head:
                continue
            head_set = set(head)
            pl_.track_ids = head + [tid for tid in pl_.track_ids if tid not in head_set]
            pl_.updated_at = now

        if set(self.playlists) != before_pl:
            mutated = True
        if reordered:
            self.sort_tag += 1
            self.order_changed_at = now
        if mutated:
            self.revision += 1
            self.content_changed_at = now

        client.base_submitted = {pl_id: {"name": e["name"], "tracks": e["tracks"],
                                         "modified_at": e.get("modified_at")}
                                 for pl_id, e in view.items()}

        meta = {
            "added_playlists": sorted(added_playlists),
            "removed_playlists": sorted(removed_playlists),
            "added_tracks": {pl: sorted(ks) for pl, ks in added_tracks.items() if ks},
            "removed_tracks": {pl: sorted(ks) for pl, ks in removed_tracks.items() if ks},
            "revision": self.revision,
            "sort_tag": self.sort_tag,
            "mutated": mutated,
            "reordered": reordered,
            "never_owned": {
                "playlists": sorted(never_owned_pl),
                "tracks": {pl: sorted(ks) for pl, ks in never_owned_t.items() if ks},
            },
        }
        self.journal.append({"client": client_id, "revision": self.revision,
                             "meta": meta, "at": now})

        view_out, cleanup = self.render_for(client)
        return MergeResult(status=200, view=view_out, pending_cleanup=cleanup,
                           meta=meta, canonical_mutated=mutated)

    # ---- 首次接入 ---------------------------------------------------------
    def _adopt_initial(self, client: Client, view: Dict[str, dict], now: str,
                       before_pl: Set[str]) -> MergeResult:
        """首次接入只增不删。"""
        mutated = False
        added_pl: Set[str] = set()
        added_t: Dict[str, List[str]] = {}
        for pl in sorted(view.keys()):
            if self._ensure_playlist(pl, view[pl]["name"], now):
                mutated = True
                added_pl.add(pl)
        added_head: Dict[str, List[str]] = {}
        for pl in sorted(view.keys()):
            for key in view[pl]["tracks"]:
                tr, ch = self._add_track(key, now)
                inserted = self._append_to_playlist(pl, tr.tr_id, now)
                if inserted or ch:
                    mutated = True
                    added_t.setdefault(pl, []).append(key)
                if inserted:
                    added_head.setdefault(pl, []).append(tr.tr_id)
        # 排序同步：已有歌单集合不变但顺序不同 → 应用提交顺序
        reordered = False
        for pl in view.keys():
            pl_ = self.playlists.get(pl)
            if pl_ is None or pl_.deleted_at is not None:
                continue
            cur_live = [tid for tid in pl_.track_ids
                        if tid in self.tracks and self.tracks[tid].deleted_at is None]
            desired = []
            for key in view[pl]["tracks"]:
                tid = self.identity_to_tr.get(key)
                if tid is not None:
                    tr = self.tracks.get(tid)
                    if tr is not None and tr.deleted_at is None:
                        desired.append(tid)
            if set(cur_live) == set(desired) and desired and desired != cur_live:
                pl_.track_ids = desired
                pl_.updated_at = now
                reordered = True
        # 新增置顶（必须在排序同步之后，否则排序同步会覆盖置顶结果）
        for pl, tids in added_head.items():
            pl_ = self.playlists.get(pl)
            if pl_ is None or pl_.deleted_at is not None:
                continue
            head = [tid for tid in tids if tid in pl_.track_ids]
            if not head:
                continue
            head_set = set(head)
            pl_.track_ids = head + [tid for tid in pl_.track_ids if tid not in head_set]
            pl_.updated_at = now
        if set(self.playlists) != before_pl:
            mutated = True
        if mutated:
            self.revision += 1
            self.content_changed_at = now
        if reordered:
            self.sort_tag += 1
            self.order_changed_at = now
        client.base_submitted = {pl_id: {"name": e["name"], "tracks": e["tracks"],
                                         "modified_at": e.get("modified_at")}
                                 for pl_id, e in view.items()}

        meta = {"adopted": True,
                "added_playlists": sorted(added_pl),
                "added_tracks": {pl: sorted(set(ks)) for pl, ks in added_t.items() if ks},
                "removed_playlists": [], "removed_tracks": {},
                "revision": self.revision, "sort_tag": self.sort_tag,
                "mutated": mutated, "reordered": reordered}
        self.journal.append({"client": client.client_id, "revision": self.revision,
                             "meta": meta, "at": now})
        view_out, cleanup = self.render_for(client)
        return MergeResult(status=201, view=view_out, pending_cleanup=cleanup,
                           meta=meta, canonical_mutated=mutated)

    # ---- 客户端生命周期 ---------------------------------------------------
    _RETIRED_AFTER_DAYS = 180

    def _mark_retired(self, now: str) -> None:
        try:
            from datetime import datetime, timedelta, timezone
            cutoff = datetime.now(timezone.utc) - timedelta(days=type(self)._RETIRED_AFTER_DAYS)
            for c in self.clients.values():
                if c.retired or not c.last_seen:
                    continue
                seen = self._parse_stamp(c.last_seen)
                if seen is not None and seen < cutoff:
                    c.retired = True
        except Exception:
            pass

    # ---- UI 权威操作 ------------------------------------------------------
    def reorder_playlists(self, order: List[str], origin: str = "ui") -> None:
        live = [pl_id for pl_id in self.playlist_order
                if pl_id in self.playlists and self.playlists[pl_id].deleted_at is None]
        if sorted(order) != sorted(live):
            raise ValueError("order 必须恰好包含当前全部存活歌单")
        if order == self.playlist_order:
            return
        now = self._now()
        self.playlist_order = list(order)
        self.sort_tag += 1
        self.order_changed_at = now
        self.journal.append({"client": origin, "revision": self.revision,
                             "sort_tag": self.sort_tag,
                             "action": "reorder", "at": now})

    def delete_playlist(self, pl_id: str, origin: str = "ui") -> None:
        pl = self.playlists.get(pl_id)
        if pl is None or pl.deleted_at is not None:
            raise KeyError(pl_id)
        now = self._now()
        self._delete_playlist_internal(pl_id, now)
        self.revision += 1
        self.content_changed_at = now
        self.journal.append({"client": origin, "revision": self.revision,
                             "action": "playlist_delete",
                             "removed_playlists": [pl.name], "at": now})

    def reorder_tracks(self, pl_id: str, order: List[str], origin: str = "ui") -> None:
        pl = self.playlists.get(pl_id)
        if pl is None or pl.deleted_at is not None:
            raise KeyError(pl_id)
        live = [tr_id for tr_id in pl.track_ids
                if tr_id in self.tracks and self.tracks[tr_id].deleted_at is None]
        live_keys = [self.tracks[tr_id].key for tr_id in live]
        if sorted(order) != sorted(live_keys):
            raise ValueError("order 必须恰好包含该歌单当前全部歌曲")
        if order == live_keys:
            return
        now = self._now()
        pl.track_ids = [self.identity_to_tr[k] for k in order]
        pl.updated_at = now
        self.sort_tag += 1
        self.order_changed_at = now
        self.journal.append({"client": origin, "revision": self.revision,
                             "sort_tag": self.sort_tag,
                             "action": "reorder_tracks", "playlist": pl.name, "at": now})

    def remove_track_from_playlist(self, pl_id: str, key: str, origin: str = "ui") -> None:
        pl = self.playlists.get(pl_id)
        if pl is None or pl.deleted_at is not None:
            raise KeyError(pl_id)
        tr_id = self.identity_to_tr.get(key)
        if tr_id is None or tr_id not in pl.track_ids:
            raise KeyError(key)
        now = self._now()
        self._remove_track_from_all_playlists(key, now)
        self.revision += 1
        self.content_changed_at = now
        self.journal.append({"client": origin, "revision": self.revision,
                             "action": "track_delete", "playlist": pl.name,
                             "removed_tracks": {pl_id: [key]}, "at": now})


Engine = SyncSpace
