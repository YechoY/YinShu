"""合并引擎（M1 PoC）—— 实现 docs/05 §5 的不变量 I1–I7 与 §5.3 算法。

设计原则：宁可少同步，不可错删除。

核心概念：
  空间 canonical     枢纽认定的"真相"（歌单 + 曲目池 + 墓碑 + revision）
  交付基线 base_served   上次真正交付给客户端的视图 + served_revision（I2′ 并发闸门与 GC 水位；
                         与 base_submitted 的交集 = 这台设备"真的拥有过"的条目，R1 删除判据。
                         例外：整份替换方言（D30，full_view_submit）与**可信客户端的歌单级**
                         （D32）只用 base_served，见 merge()）
  提交基线 base_submitted 上次提交的视图（拥有水位；R1：仅"自己提交过"的条目才允许判删除）
  墓碑 tombstone     显式删除标记；存活期由确认水位决定（D21），不以时间为判据

本文件不涉及 WebDAV 协议层（那是 M2），只做纯逻辑合并。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from .canonical import (
    InvalidPayload,
    Playlist,
    Tombstone,
    Track,
    canonical_hash,
    identity_key,
    new_ulid,
)


@dataclass
class ServedBaseline:
    """交付基线：上次真正交付给客户端的视图 + served_revision + view_hash。"""
    view: Dict[str, dict] = field(default_factory=dict)   # pl_id -> {"name", "tracks": frozenset}
    served_revision: int = 0
    view_hash: Optional[str] = None
    stamp: str = ""                   # P0-2：上次交付的确定性 modifiedAt（内容不变则不变）
    opaque_hash: Optional[str] = None # 上次交付时的 opaque 快照哈希（I6/E5）


@dataclass
class Client:
    """一个同步参与者。设备身份由 (dialect, native_id 映射) 识别（docs/03 §3.6）。"""
    client_id: str
    dialect: str                     # 方言族，如 'cyshine-v1' / 'ceru-plugin'
    identity_verified: bool = False  # 是否已越过删除点的可信身份（D21 判据）
    retired: bool = False            # >180 天无活动 → 排除在墓碑 GC 条件外
    can_delete: bool = True          # capabilities().deleteTrack（方言能力表见 adapters.CAPABILITIES；2026-10-07 起澜音也=True）
    deliver_ack_on_view: bool = True  # 第六轮 §1 回归修复：交付（GET）"看过删除视图"能否计入确认水位。
                                     # True=栖弦这类"看过即能应用删除、下一轮提交不再带回"的客户端；
                                     # False=澜音这类"本地删不掉、看过必带回残留"的客户端——看过≠确认，
                                     # 其确认只能来自 merge 的"提交不再带该键"（P1-3 意图在 can_delete=True 后
                                     # 的等价表达；否则 deliver ack → gc 删墓碑 → 残留回推直接复活）。
    full_view_submit: bool = False   # 第十一轮 R1 放松：提交是"整份替换"（洛雪：以远端为底 replay 后整份 PUT）
                                     # ⇒ 交付过的条目这次缺席就是删除意图。栖弦=False（section 级 LWW 可能
                                     # 保留自己的旧段 M1，放松会误删别人加的歌）；澜音插件=False（本地删不掉，
                                     # 缺席可能只是导入失败）。见 merge() 里 owned_* 的注释。
    round_trip_sources: Optional[frozenset] = None  # 该方言能原样往返回来的音源前缀（洛雪白名单）；
                                     # None = 未知 ⇒ 放松时不判删（宁可不删，不可误删）
    base_served: Optional[ServedBaseline] = None
    base_submitted: Optional[Dict[str, dict]] = None  # 拥有水位（R1）：与 base_served 的交集构成删除判据
                                      # （例外：整份替换方言 D30、以及**可信客户端的歌单级** D32，只用 base_served）
    opaque: dict = field(default_factory=dict)        # I6：客户端私有内容，原样携带
    first_seen_revision: int = 0
    last_seen: Optional[str] = None
    last_delivered_stamp: Optional[str] = None        # P0-2：上次交付的 modifiedAt
    last_submitted_stamp: Optional[str] = None        # P0-A：客户端本次提交声明的 modifiedAt


@dataclass
class MergeResult:
    """一次 merge() 的结果。status：201 首建 / 200 正常 / 400 坏负载 / 404 无基线。"""
    status: int
    view: Dict[str, dict]                        # 渲染给客户端的视图（canonical 真值）
    pending_cleanup: List[dict] = field(default_factory=list)
    meta: dict = field(default_factory=dict)     # added/removed/suspects/deferred/restores
    canonical_mutated: bool = False
    stamp: str = ""                              # P0-2：本次交付的确定性 modifiedAt


class SyncSpace:
    """一份空间的存储与合并引擎（单写者：同空间串行，跨空间并行）。"""

    # 安全阀阈值（docs/05 §5.3.5，可配）
    SAFE_PL_DELETE_COUNT = 2          # 单次删除歌单下限数（docs/11 D3：与比例 AND）
    SAFE_PL_DELETE_RATIO = 0.50       # 且 >= 空间的 50%
    SAFE_TR_DELETE_RATIO = 0.50       # 单歌单删除曲目比例
    SAFE_TR_DELETE_MIN = 10           # 且 >= 10 首

    # 第四轮 §2.4.3（P0-C）：删除"冷静期"——删除刚发生后的重加仍按残留压制
    # （栖弦整段覆盖客户端删除后可能马上带着旧视图回推）；越过冷静期且看过删除
    # 结果的重加才视为显式恢复。可被 policy.restore_grace_seconds 覆盖。
    RESTORE_GRACE_SECONDS = 120

    # 已适配方言（D11：未识别方言隔离存储、不跨格式合并）。
    # 第六轮 §1.6：由 hub.adapters 包在导入时注入注册表全量（set_known_dialects）；
    # 未加载适配器包（engine 单测直接 import）时保留内建默认，行为不变。
    KNOWN_DIALECTS = frozenset({"cyshine-v1", "ceru-plugin"})

    @classmethod
    def set_known_dialects(cls, names) -> None:
        """注入已知方言集（来源：hub.adapters.REGISTRY keys，见第六轮 §1.6）。"""
        cls.KNOWN_DIALECTS = frozenset(names)

    def __init__(self, space_id: str = "space-a") -> None:
        self.space_id = space_id
        self.revision = 0
        self.playlists: Dict[str, Playlist] = {}
        self.playlist_order: List[str] = []
        self.tracks: Dict[str, Track] = {}
        self.identity_to_tr: Dict[str, str] = {}   # 身份键 -> tr_id（曲目池去重）
        self.tombstones: Dict[str, Tombstone] = {} # key -> Tombstone
        self.playlist_aliases: Dict[Tuple[str, str], str] = {}  # (dialect, native_id) -> pl_id
        self.clients: Dict[str, Client] = {}
        self.journal: List[dict] = []
        self.pending_deletions: Dict[str, dict] = {}  # 待确认删除（安全阀挂起）
        self.pending_restores: Dict[str, dict] = {}   # 待确认恢复（墓碑压制；P1-1 默认关闭）
        self.quarantine: Dict[str, dict] = {}         # 未知方言隔离容器（D11）
        # 第四轮引擎策略（可被 policy 覆盖，_repair_engine_fields 兜底默认值）：
        # 第八轮修正：这个开关**不能**叫 confirm_restore——它与同名方法
        # SyncSpace.confirm_restore(key)（见本文件末尾的确认通道）撞名：
        #   ① 旧 pkl 里没有该实例属性时 hasattr() 会摸到方法（恒为真），
        #      `_repair_engine_fields` 因此永远补不上默认值，`_note_restore` 里
        #      `if not self.confirm_restore` 拿到的是绑定方法（真值）→ 恢复卡照旧产生；
        #   ② 一旦实例真的写入 bool，又会遮住方法，使 /api/pending-restores/{key}/confirm
        #      调用 `eng.confirm_restore(key)` 报 "'bool' object is not callable"。
        # 故改名为 restore_cards_enabled，并在加载时清掉遗留的同名 bool。
        self.restore_cards_enabled: bool = False      # P1-1：默认不再产生待确认恢复卡
        # 第八轮（2026-10-08 用户拍板「不要再弹确认了」）：批量删除安全阀**不再产生确认卡**。
        # False（默认）三分支见 merge()：可信端直删生效（meta.safety_valve.applied）、
        # 不可信端既不生效也不出卡（meta.safety_valve.withheld）；
        # True 才回到旧行为（超阈值 → 挂起 → 产生"待确认删除"卡 → 前端 PendingCard 弹确认）。
        self.delete_cards_enabled: bool = False
        self.trusted_direct_delete: bool = True       # P1-2：可信客户端批量删除直接生效
        self.restore_grace_seconds: Optional[float] = None  # P0-C：None=用类常量
        # P0-2：确定性交付——内容（增删）真变时刻与排序真变时刻分离；
        # revision 只在内容变化时推进，排序变化走 sort_tag/order_changed_at（P1-9 裁决）。
        self.content_changed_at: Optional[str] = None
        self.order_changed_at: Optional[str] = None
        self.sort_tag: int = 0

    # ---- 客户端注册 -------------------------------------------------------
    def register_client(
        self,
        client_id: str,
        dialect: str = "cyshine-v1",
        identity_verified: bool = False,
        can_delete: bool = True,
        deliver_ack_on_view: bool = True,
        full_view_submit: bool = False,
        round_trip_sources: Optional[frozenset] = None,
    ) -> Client:
        if client_id in self.clients:
            raise ValueError(f"client {client_id!r} 已注册")
        c = Client(client_id=client_id, dialect=dialect,
                   identity_verified=identity_verified, can_delete=can_delete,
                   deliver_ack_on_view=deliver_ack_on_view,
                   full_view_submit=full_view_submit,
                   round_trip_sources=round_trip_sources,
                   first_seen_revision=self.revision)
        self.clients[client_id] = c
        return c

    # ---- 提交负载解析 -----------------------------------------------------
    def _parse_tracks(self, raw_tracks) -> List[str]:
        """解析为有序身份键列表（保留客户端提交顺序，去重）。"""
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
        """歌单身份解析（I4）：native-id 别名 → 名字别名（仅首次、仅唯一）→
        跨方言同名墓碑抑制（P0-7）→ 新建。"""
        alias_key = (client.dialect, native_id)
        if alias_key in self.playlist_aliases:
            pl_id = self.playlist_aliases[alias_key]
            pl = self.playlists.get(pl_id)
            if pl is not None:
                self._note_native(pl, client.dialect, native_id, name)
                return pl_id
        # 名字别名：只在"新建"时用一次（N6：空名/重名禁止自动合并）
        name = (name or "").strip()
        if name:
            hit = [pid for pid, pl in self.playlists.items() if pl.deleted_at is None and pl.name == name]
            if len(hit) == 1:
                pl_id = hit[0]
                self.playlist_aliases[alias_key] = pl_id
                self._note_native(self.playlists[pl_id], client.dialect, native_id, name)
                return pl_id
            # P0-7：跨方言歌单墓碑抑制——同名存活墓碑且提交者未越过删除点 ⇒
            # 复用墓碑 id（不新建），由 merge 的 added_pl 分支转"待确认恢复"卡。
            for key, t in self.tombstones.items():
                if t.element == "playlist" and t.target_name == name:
                    if not self._crossed_deletion_point(client, t):
                        self.playlist_aliases[alias_key] = key
                        return key
                    break   # 已越过删除点 ⇒ 允许新建（fall through）
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
        """P0-6：native-id/名字别名命中已有歌单时，登记本端 native id（渲染优先用）。"""
        pf = pl.platforms.setdefault(dialect, {})
        if not pf.get("native_id"):
            pf["native_id"] = native_id
        pf["native_name"] = name

    def _ingest(self, client: Client, submission: dict) -> Dict[str, dict]:
        """把客户端提交解析成 canonical 视图 {pl_id: {"name", "tracks": tuple}}。

        tracks 为有序 tuple（保留提交顺序，去重）——这是排序同步的基础。

        P0-5：先"纯校验"（不改任何状态）通过后，再统一"事务落库"——
        任何歌单/曲目解析失败都在校验阶段抛错，不会留下半截 canonical（副作用回滚）。
        """
        if not isinstance(submission, dict):
            raise InvalidPayload("submission 必须是对象")
        playlists = submission.get("playlists")
        if not isinstance(playlists, list):
            raise InvalidPayload("submission.playlists 必须是数组")
        # 第一段：纯校验（无副作用）
        parsed: List[Tuple[str, str, Tuple[str, ...]]] = []
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
            parsed.append((native_id, name, keys))
        # 第二段：事务落库（身份解析有副作用：新建歌单/登记别名/写 order）
        view: Dict[str, dict] = {}
        for native_id, name, keys in parsed:
            pl_id = self._resolve_playlist(client, native_id, name)
            view[pl_id] = {"name": name, "tracks": keys}
        return view

    @staticmethod
    def _now() -> str:
        """UTC 毫秒级时间戳（第四轮 P0-A：modifiedAt 毫秒精度，压过客户端本地 wall clock）。"""
        from datetime import datetime, timezone
        s = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        return s[:23] + "Z"   # %f 6 位 → 截到毫秒（.SSS）

    @staticmethod
    def _parse_stamp(stamp: Optional[str]):
        """解析秒级/毫秒级 ISO8601 UTC 时间戳；失败返回 None（调用方按保守值处理）。"""
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
        """时间戳 +N 毫秒单调推进；解析失败原样返回。"""
        dt = cls._parse_stamp(stamp)
        if dt is None:
            return stamp
        from datetime import timedelta
        s = (dt + timedelta(milliseconds=millis)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        return s[:23] + "Z"

    # ---- 渲染 -------------------------------------------------------------
    def render_for(self, client: Client) -> Tuple[Dict[str, dict], List[dict]]:
        """把 canonical 真值渲染成该客户端看到的视图（docs/05 §5.3.3）。

        返回 (view, pending_cleanup)。view 为 {pl_id: {"name","tracks"}}（排除已删），
        pending_cleanup 为"删除但该客户端只能标记待清理"的条目（A5，澜音）。
        """
        view: Dict[str, dict] = {}
        for pl_id in self.playlist_order:
            pl = self.playlists.get(pl_id)
            if pl is None or pl.deleted_at is not None:
                continue
            # 保留 track_ids 顺序（提交/追加顺序），只过滤已删曲目
            live = []
            for tr_id in pl.track_ids:
                tr = self.tracks.get(tr_id)
                if tr is not None and tr.deleted_at is None:
                    live.append(tr.key)
            view[pl_id] = {"name": pl.name, "tracks": tuple(live)}
        cleanup: List[dict] = []
        if not client.can_delete:
            for key, t in self.tombstones.items():
                cleanup.append({"key": key, "kind": t.element, "deleted_at": t.deleted_at})
        return view, cleanup

    def _view_hash(self, client: Client, view: Dict[str, dict]) -> str:
        """交付内容哈希（P1-9 裁决）：顺序**不参与**哈希（spec T5/E3），
        歌单与曲目都按身份键排序归一；opaque 计入（I6/E5，客户端私有节变化=内容变化）。"""
        norm = {pl_id: {"name": e["name"], "tracks": sorted(e["tracks"])}
                for pl_id, e in view.items()}
        if client.opaque:
            norm["__opaque__"] = client.opaque
        return canonical_hash(norm)

    def _submission_matches(self, client: Client, view: Dict[str, dict]) -> bool:
        """"客户端手上那份"是否就是"我们这次要交付的这份"。

        **第十二轮（2026-10-08 用户真机：洛雪删掉的歌在栖弦里删不掉）**：客户端提交里出现过、
        而我们**不采纳**的内容（墓碑压制 `suppressed_*`、R1 不判删的 `never_owned`、
        安全阀 `withheld`、I2′ `suspects`）会让交付视图和它手上那份不一致。此时若还按
        "相对上次交付内容没变"短路返回旧戳，客户端（栖弦按 section 级 LWW"远端时间更大才采纳"）
        会认为远端没更新 → 保留本地副本 → 那首歌在设备侧**永远删不掉**，且每轮同步都回推一次
        （每次都被压制）。所以交付戳必须压过它**自己声明的** modifiedAt（P0-A 的本意，
        这里补上被 `same_view` 短路绕开的那一半）。

        只比对客户端**声明过**的歌单：它没提交过的歌单（别人新建的）不影响判据，
        避免"整份视图条款不同"造成每轮都推进戳（S3 写放大）。
        """
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
        """P0-2+P0-A：确定性 modifiedAt。

        **第四轮 P0-A（根因 A）**：交付时间戳必须严格大于客户端刚提交的
        modifiedAt（client.last_submitted_stamp）。栖弦客户端按"远端时间大才采纳"
        整段丢弃（sync_models.dart），枢纽时间戳若打不过客户端本地 wall clock，
        删除在设备侧永远不生效并被回推。实现：base = max(上次交付值, 本次提交声明)；
        交付值 = max(内容/排序最后真变时刻, base)；若无真变或真变不超过 base，
        则取 base +1ms（毫秒单调压过提交声明）。
        仅当相对该客户端上次交付内容（含顺序）或 opaque 确有变化，**或者客户端手上那份
        （它上次提交的视图）仍与本次交付不同**才推进；三者都相同 ⇒ 原样返回上次交付值（字节全同）。
        后者是第十二轮补的：被墓碑压制 / R1 不判删 / 安全阀保留的条目会让"我们交付的"与
        "它提交的"长期不一致，只看交付视图会短路成旧戳，设备侧就永远删不掉（见 `_submission_matches`）。"""
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
        """模拟 GET 成功交付：推进 base_served（I1），并更新墓碑确认水位。

        P0-1：无基线客户端（从未交付且从未提交）→ 404，不推进基线、不写 journal——
        逼栖弦走"远端不存在 ⇒ 以本地为准 ⇒ PUT"（docs/03 §3.5.1）。

        **第四轮 P0-B（根因 B）**：恢复"交付即确认"——本端交付的视图**不含**该键，
        说明这台设备至少**看过删除后的视图**（栖弦可能未应用删除，但那由 merge 的
        carried 回推检查负责撤销确认）；配合 merge 端"提交还带键 ⇒ 撤销 ack"，
        墓碑确认水位 = "看过删除后视图 ∧ 未再回推"（第四轮 §2.4.2 D21 修订）。
        """
        client = self.clients[client_id]
        if client.base_served is None and client.base_submitted is None:
            return MergeResult(status=404, view={}, meta={"error": "no baseline"})
        now = now or self._now()
        self._mark_retired(now)
        view, cleanup = self.render_for(client)
        stamp = self._next_stamp(client, view)
        served = ServedBaseline(
            view=view,
            served_revision=self.revision,
            view_hash=self._view_hash(client, view),
            stamp=stamp,
            opaque_hash=canonical_hash(client.opaque or {}),
        )
        client.base_served = served
        client.last_delivered_stamp = stamp
        # 交付即确认（P0-B）：交付视图不再携带该键 → ack（看过删除后视图）；
        # 该客户端后续若回推残留，merge 会把它从 acked 撤销（carried 检查）。
        # **第六轮 §1 回归修复**：P1-3 配套原依赖 can_delete=False（澜音看过 ≠ 能应用、
        # 必带回），2026-10-07 用户拍板澜音 can_delete=True 后失效——deliver ack →
        # 全员 ack → gc 删墓碑 → 残留回推直接复活。现改由方言能力
        # deliver_ack_on_view 表达"看过删除视图能否计入确认水位"：栖弦=True（看过即采纳，
        # 回推走显式恢复语义）；澜音=False（本地删不掉、看过必带回，确认只能来自 merge 的
        # "提交不再带该键"）。can_delete=False 的端点永远不算（老行为不变）。
        if self.tombstones:
            view_track_keys = {k for e in view.values() for k in e["tracks"]}
            for key, t in self.tombstones.items():
                if not (client.can_delete and client.deliver_ack_on_view):
                    continue
                # 第十二轮（真机：删除被复活）：D21 的 ack 判据是"看过删除后视图 ∧ **未再回推**"，
                # 而这里原本只看了前半句。`carried` 记的正是"**删除之后**的某次提交里还带着这个键"
                # ——它自己声明本地仍有这一条，说明它没应用这次删除（栖弦收到旧戳会保留本地副本、
                # 每轮回推）。这种客户端不能算"看过即采纳"，否则它一被记 ack 就可能凑齐全员确认
                # → GC 收走墓碑 → 它下一次回推成了**无墓碑的普通重加** = 已确认的删除被复活。
                if t.carried and client_id in t.carried:
                    continue
                if t.element == "playlist":
                    present = key in view
                else:
                    present = key in view_track_keys
                if not present:
                    t.acked.add(client_id)
        self._gc_tombstones()   # ack 到位后按确认水位 GC（D21/T3）
        # 拉取也是同步动作：留日志（含"无变更"的 GET），便于排查与可视化
        self.journal.append({"client": client_id, "revision": self.revision,
                             "action": "deliver", "at": type(self)._now()})
        return MergeResult(status=200, view=view, pending_cleanup=cleanup, stamp=stamp)

    def peek_view(self, dialect: str, can_delete: bool = True):
        """v3 只读成员（viewer）拉取：渲染当前**权威视图**的只读快照。

        与 deliver 的区别——viewer 不能 PUT、永远建不了基线，deliver 会对其返回 404；
        故这里用一个**不注册进 self.clients 的临时 Client** 调 render_for：
        不推进任何 base_served/base_submitted、不 ack 墓碑、不触发 GC、不写 journal、
        不改 revision/stamp。纯读，不参与合并，因此不影响 R1/I2′/GC/安全阀任何不变量。
        返回 (view, pending_cleanup, stamp)，stamp 取内容/排序最后真变时刻（确定性）。
        """
        tmp = Client(client_id=f"__peek__:{dialect}", dialect=dialect,
                     identity_verified=True, can_delete=can_delete)
        view, cleanup = self.render_for(tmp)
        cands = [x for x in (self.content_changed_at, self.order_changed_at) if x]
        stamp = max(cands) if cands else self._now()
        return view, cleanup, stamp

    # ---- 内部：曲目/歌单操作（返回是否有实际变化） -------------------------
    def _add_track(self, key: str, now: str) -> Tuple[Track, bool]:
        """把身份键加入曲目池（去重：已存在则复用记录）。返回 (track, changed)。"""
        if key in self.identity_to_tr:
            tr = self.tracks[self.identity_to_tr[key]]
            if tr.deleted_at is not None:
                tr.deleted_at = None   # 显式恢复：清空间级墓碑
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
        """确保歌单存在且存活（未删/改名则更新）。返回是否有实际变化。"""
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
                # 复活已删歌单：必须恢复显示顺序，否则歌单存在却不在渲染序列里
                if pl_id not in self.playlist_order:
                    self.playlist_order.append(pl_id)
            if pl.name != name:
                pl.name = name
                changed = True
            if changed:
                pl.updated_at = now
        return changed

    def _remove_track_global(self, key: str, now: str, origin: str, commit_revision: int) -> None:
        """空间级删除一首歌：写墓碑、标记 deleted_at、从所有歌单摘除（P5 去重 → 全局）。"""
        if key in self.identity_to_tr:
            tr = self.tracks[self.identity_to_tr[key]]
            tr.deleted_at = now
            for pl in self.playlists.values():
                if tr.tr_id in pl.track_ids:
                    pl.track_ids = [x for x in pl.track_ids if x != tr.tr_id]
                    pl.updated_at = now
        self._write_tombstone("track", key, now, origin, commit_revision)

    def _write_tombstone(self, element: str, key: str, now: str, origin: str,
                         commit_revision: int, target_name: Optional[str] = None) -> None:
        """commit_revision：删除提交后的 revision（删除在此版本起对全体可见）。

        供确认水位 GC（D21/T3）与"是否越过删除点"判定（I7）使用。必须在推进
        self.revision 之前以 `self.revision + 1` 传入（删除必然使 revision +1）。

        target_name：歌单墓碑存"被删歌单的名字"（P0-7 跨方言同名抑制需要按名字命中）；
        曲目墓碑的 target_name 即身份键本身。
        """
        if key in self.tombstones:
            # 同一元素多条墓碑按最晚一条处理（I3/E4）
            self.tombstones[key].deleted_at = now
            self.tombstones[key].origin_client = origin
            self.tombstones[key].created_at_revision = commit_revision
            if target_name:
                self.tombstones[key].target_name = target_name
            return
        self.tombstones[key] = Tombstone(
            key=key, element=element, deleted_at=now, origin_client=origin,
            created_at_revision=commit_revision, target_name=target_name or key)

    def _live_tombstone(self, key: str) -> Optional[Tombstone]:
        return self.tombstones.get(key)

    def _age_seconds(self, ts: str) -> float:
        """墓碑删除时间距现在的秒数；解析失败按 0（保守：永不越过冷静期）。"""
        dt = self._parse_stamp(ts)
        if dt is None:
            return 0.0
        from datetime import datetime, timezone
        return (datetime.now(timezone.utc) - dt).total_seconds()

    def _grace_seconds(self) -> float:
        """冷静期秒数：实例配置优先，未配置用类常量。"""
        if self.restore_grace_seconds is not None:
            return float(self.restore_grace_seconds)
        return float(type(self).RESTORE_GRACE_SECONDS)

    def _crossed_deletion_point(self, client: Client, t: Tombstone) -> bool:
        """提交者是否已越过删除点（D21，第四轮 P0-C 修订）。

        是 → 显式恢复（清墓碑直接生效）；否 → 墓碑压制（抑制重加）。
        判据 = ① 看过删除结果（交付水位已到墓碑创建之后）∧ ② 越过冷静期
        （删除后 RESTORE_GRACE_SECONDS 秒内的重加一律按残留压制，防止栖弦
        整段覆盖客户端刚删完就带着旧视图回推把删除顶回去）。

        **不再限定 origin_client**：任何一台同空间可信设备在看过删除结果、
        且冷静期过后重加，都视为用户意图的显式恢复（删了又加 = 撤销删除），
        而不是残留回推。残留回推的主要形态（未看过删除结果 / 冷静期内）仍被压制。

        **无删除能力的客户端（`can_delete=False`，澜音插件）永远不算越过删除点**：
        它删不掉自己本地的副本，下一轮提交里带上已删条目属于**被迫重加**，
        不是"用户想恢复" ⇒ 走墓碑压制（复检 S11/S15，见 修改意见.md §12.2）。
        """
        if not (client.identity_verified and not client.retired):
            return False
        if not client.can_delete:
            return False
        if client.base_served is None:
            return False
        if client.base_served.served_revision < t.created_at_revision:
            return False   # 没看过删除结果：回推残留
        if self._age_seconds(t.deleted_at) < self._grace_seconds():
            return False   # 冷静期内：仍按残留压制
        return True

    def _note_restore(self, kind: str, key: str, now: str,
                      context: Optional[str] = None,
                      tracks: Optional[List[str]] = None,
                      name: Optional[str] = None,
                      client_id: Optional[str] = None) -> None:
        """登记一条"待确认恢复"候选（墓碑压制，I7/D20）。同 key 合并上下文。

        **第四轮 P1-1**：默认（restore_cards_enabled=False）不再产生待确认恢复卡——
        抑制信息由 merge meta 的 suppressed_playlists/suppressed_tracks 承载，
        重加要么被墓碑压制、要么越冷静期后直接显式恢复，不需要人工确认。
        仅当 policy.confirm_restore=True（旧行为开关，落到引擎字段
        restore_cards_enabled；第八轮改名，原字段名与同名方法撞名，见 __init__）时
        才写 pending_restores。

        多账户共用歌单 §2：补记发起客户端/账号（确认卡只能由本人/admin 处理，
        前端可显示"来自：<账号>"）。同 key 已存在时不覆盖发起者（先到先记）。
        """
        if not self.restore_cards_enabled:
            return
        rec = self.pending_restores.get(key)
        if rec is None:
            rec = {"kind": kind, "key": key,
                   "reason": "身份不可信的重加被墓碑压制", "at": now}
            self.pending_restores[key] = rec
        if client_id is not None:
            rec.setdefault("client", client_id)
            rec.setdefault("account", client_id.split(":", 1)[0])
        if kind == "track":
            rec.setdefault("playlists", [])
            if context is not None and context not in rec["playlists"]:
                rec["playlists"].append(context)
        else:
            rec["tracks"] = tracks or []
            rec["name"] = name

    def _upsert_pending_deletion(self, client_id: str, reason: str,
                                 playlists: Set[str], tracks: Dict[str, Set[str]],
                                 now: str) -> str:
        """待确认删除卡的**去重/合并**（多账户共用歌单 §2）：

        同一客户端 + 同一 reason 的未处理卡 → 复用旧 pid，把新的 playlists/tracks
        并入（并集），只更新 created_at；找不到 → 新建（带 account 归属字段）。
        """
        for pid, card in self.pending_deletions.items():
            if card.get("client") == client_id and card.get("reason") == reason:
                card["playlists"] |= playlists
                for pl, ks in tracks.items():
                    card["tracks"].setdefault(pl, set())
                    card["tracks"][pl] |= ks
                card["created_at"] = now
                return pid
        pid = new_ulid("del")
        self.pending_deletions[pid] = {
            "space": self.space_id, "client": client_id, "reason": reason,
            "account": client_id.split(":", 1)[0],
            "playlists": set(playlists),
            "tracks": {pl: set(ks) for pl, ks in tracks.items()},
            "created_at": now,
        }
        return pid

    def _close_pending_deletions(self, client_id: str, view: Dict[str, dict]) -> None:
        """自动关闭（多账户共用歌单 §2）：本次提交**又带回了**卡里的条目
        （歌单重新出现 / 曲目 key 重新出现）→ 用户不删了，卡应该消失。
        只关**本客户端**发起的卡，不影响别的客户端/管理员。
        """
        if not self.pending_deletions:
            return
        submitted_keys = {k for e in view.values() for k in e["tracks"]}
        for pid, card in list(self.pending_deletions.items()):
            if card.get("client") != client_id:
                continue
            pls = card.get("playlists") or set()
            trs = card.get("tracks") or {}
            if pls and not pls.isdisjoint(view.keys()):
                self.pending_deletions.pop(pid, None)
                continue
            if trs and any(k in submitted_keys for ks in trs.values() for k in ks):
                self.pending_deletions.pop(pid, None)

    # ---- 安全阀 -----------------------------------------------------------
    def _safety_trigger(self, removed_pl: Set[str], removed_t: Dict[str, Set[str]]) -> bool:
        live_pl = [p for p in self.playlist_order if self.playlists[p].deleted_at is None]
        n_live = len(live_pl)
        # P2-17：n_live==0（删到 0 张 = 100% 删除）不绕过安全阀——按绝对数下限判定
        if n_live == 0:
            return (len(removed_pl) >= self.SAFE_PL_DELETE_COUNT
                    or any(len(keys) >= self.SAFE_TR_DELETE_MIN
                           for keys in removed_t.values()))
        # 歌单：百分比主导、绝对数只做下限（docs/11 D3）：ratio ≥ 50% 且 count ≥ 2。
        # 用 AND 而非 OR，避免"小空间里删 1 个歌单(100%) 就被拦"（docs/11 D3 场景）。
        if len(removed_pl) >= self.SAFE_PL_DELETE_COUNT and (
            len(removed_pl) / n_live >= self.SAFE_PL_DELETE_RATIO):
            return True
        for pl_id, keys in removed_t.items():
            pl = self.playlists.get(pl_id)
            if pl is None or pl.deleted_at is not None:
                continue
            live = [tr_id for tr_id in pl.track_ids
                    if tr_id in self.tracks and
                    self.tracks[tr_id].deleted_at is None]
            if live and len(keys) / len(live) >= self.SAFE_TR_DELETE_RATIO \
               and len(keys) >= self.SAFE_TR_DELETE_MIN:
                return True
        return False

    # ---- 合并主流程 -------------------------------------------------------
    def merge(self, client_id: str, submission: dict, now: Optional[str] = None,
              client_modified_at: Optional[str] = None) -> MergeResult:
        """PUT 语义：解析 → 差分 → 三路合并 → 落库 → 渲染回该客户端。

        返回 200/201 渲染视图；base_served 不在此推进（I1/F3：PUT 响应不算交付）。

        client_modified_at（P0-A）：客户端提交声明的 modifiedAt，记录到
        client.last_submitted_stamp——交付时间戳必须严格压过它（根因 A）。
        """
        client = self.clients[client_id]
        now = now or self._now()
        self._mark_retired(now)
        if client_modified_at:
            client.last_submitted_stamp = client_modified_at
        before_pl = set(self.playlists)   # P1-10：revision 漏计修复（新增空歌单也算真变）

        if not isinstance(submission, dict):
            return MergeResult(status=400, view={}, meta={"error": "submission 必须是对象"})
        if submission.get("create_only") and client.base_submitted is not None:
            # createOnly（If-None-Match: * 语义，用例 25）：文件已存在 → 按基线合并（docs/03:88，
            # 栖弦把 412 当失败会整轮重跑；本阶段绝不主动回 412）
            pass
        if client.dialect not in self.KNOWN_DIALECTS:
            # 未知方言隔离（D11）：不参与跨格式合并，其它客户端不受影响
            self.quarantine[client_id] = {"payload": submission, "first_seen": now}
            return MergeResult(status=200, view={},
                               meta={"isolated": True, "dialect": client.dialect})

        try:
            view = self._ingest(client, submission)
        except InvalidPayload as exc:
            # 结构损坏：400，落库前拒绝，基线不动（docs/05 §5.6）
            return MergeResult(status=400, view={}, meta={"error": str(exc)})
        client.last_seen = now
        client.opaque = submission.get("opaque") or {}

        if client.base_submitted is None:
            return self._adopt_initial(client, view, now, before_pl)

        bsrv = client.base_served
        base_view = bsrv.view if bsrv is not None else {}
        base_keys = set(base_view.keys())
        cur_keys = set(view.keys())
        added_pl = cur_keys - base_keys

        # 规则 R1（多设备改造 §3.2，核心）：删除只在"这台设备自己曾经提交过该条目，
        # 而这次提交里没有"时成立。owned = base_served（交付过）∩ base_submitted（自己提交过）。
        # "交付过"≠"采纳了"：栖弦这类"整段覆盖"客户端先 GET 再按 section 级 LWW 决定写不写回，
        # 它可能保留自己的旧段（M1）——只按 base_served 判删除会把**别人**加的内容误判成
        # 这台设备的删除，两台手机都丢数据。交集后：别人加的、自己没提交过的条目不判删除（只并集）。
        sub_view = client.base_submitted or {}
        # 第十一轮（2026-10-08 用户真机：lx 拉取后在本地删歌，同步不上去）：
        # 对"整份替换型"客户端放松 R1 —— owned = base_served（交付过的）即可，不再要求
        # "自己也提交过"。理由：洛雪拿到远端后 overwriteListFull 整份覆盖，之后的 PUT 内容
        # = 远端视图 + 本地操作重放，所以它这次缺席、而我们交付过的条目就是它本地删掉了。
        # 仍有三道约束：① 只对该方言能原样往返回来的音源生效（round_trip_sources；洛雪解析
        # 把白名单外的曲目丢进 opaque，那些缺席不算删除）；② I2′ 并发闸门（期间别端写过 →
        # 只记 suspects、不判删）；③ 安全阀。栖弦/澜音插件保持旧口径（见 Client 字段注释）。
        full_view = bool(client.full_view_submit and client.identity_verified
                         and not client.retired and client.can_delete)
        # 第十三轮（2026-10-08 用户真机：lx 加的歌单在栖弦里删掉，同步不上去）：**歌单级**
        # 再放松一档给"可信客户端"（判据与安全阀 trusted 完全一致：身份已验证 + can_delete
        # + 未退休）。理由：栖弦的 `sections.playlists` 是**整份**导出（`exportForSync` 遍历
        # 本地全部歌单、无过滤）且整份 LWW —— 它这次没提交的歌单，就是它本地没有的那张；
        # 而歌单的添加**不会**自动进 base_submitted（栖弦远端胜出时 applyFromSync 整份替换本地，
        # 那一轮 `_syncOnce` 因 merged == remote 直接 return、根本不上传），于是缺席永远落在
        # never_owned、永远删不掉。曲目级 owned_t 不动（真机数据损失都发生在曲目层，见上面 M1/D28）。
        trusted = (client.identity_verified and client.can_delete and not client.retired)
        if full_view or trusted:
            owned_pl = set(base_keys)
        else:
            owned_pl = base_keys & set(sub_view.keys())
        owned_t: Dict[str, Set[str]] = {}
        for pl in owned_pl:
            base = set(base_view[pl].get("tracks", ()))
            if full_view and client.round_trip_sources is not None:
                owned_t[pl] = {k for k in base if k.split(":", 1)[0] in client.round_trip_sources}
            else:
                sub = set(sub_view[pl].get("tracks", ())) if pl in sub_view else set()
                owned_t[pl] = base & sub
        removed_pl = owned_pl - cur_keys

        added_t: Dict[str, Set[str]] = {}
        removed_t: Dict[str, Set[str]] = {}
        for pl in base_keys | cur_keys:
            cur = set(view.get(pl, {}).get("tracks", ()))
            base = set(base_view.get(pl, {}).get("tracks", ()))
            added_t[pl] = cur - base
            if pl in removed_pl:
                # 第九轮（2026-10-08 真机事故「合并歌单消失，Yes 从 253 掉到 21」）：
                # 整份歌单消失（客户端不再提交该歌单）只删**歌单本身**，不据此派生曲目级删除。
                # 派生/合并歌单里的曲目往往同时躺在来源歌单里（真机：Yes ∩ 合并歌单 = 232/235），
                # 按 R1 逐曲判删会写**全局墓碑**（Track.deleted_at），把来源歌单里的同一批歌一起
                # 清空。网页端删歌单（delete_playlist）本来就只删歌单、不墓碑曲目，这里与之对齐：
                # 删歌单的语义就是"这张歌单没了"，曲目留在库里、留在别的歌单里。
                removed_t[pl] = set()
            else:
                removed_t[pl] = owned_t.get(pl, set()) - cur

        # P0.5（R1 配套）：被 R1 保护掉的"缺席"——base_served 有、但该设备**从未提交过**
        # （base_submitted 无）、本次提交里也没有。第三轮修改：同空间成员设备互信，
        # **不再出确认卡**——直接视为删除（写墓碑）；误删由墓碑 + 待确认恢复卡兜底
        # （另一端本地残留回推时可恢复）。I2′ 并发轮次仍只记 suspects（旧视图不判删）。
        never_owned_pl = (base_keys - cur_keys) - owned_pl
        never_owned_t: Dict[str, Set[str]] = {}
        # 注意：必须遍历**全部** base 歌单（含仍存在的歌单）——曲目级缺席（如 y 从 p1 里
        # 消失但 p1 仍在）只会在 base_keys ∩ cur_keys 里出现；只遍历 base_keys - cur_keys
        # 会漏掉这一层（补丁 2）。
        for pl in base_keys:
            base = set(base_view[pl].get("tracks", ()))
            owned = owned_t.get(pl, set())
            cur = set(view.get(pl, {}).get("tracks", ()))
            never_owned_t[pl] = (base - cur) - owned

        mutated = False
        suspects = {}
        # I2'：期间有别端写过 → 禁止产生删除（只并集 + 记候选）
        if bsrv is not None and self.revision != bsrv.served_revision:
            suspects = {pl: sorted(keys) for pl, keys in removed_t.items() if keys}
            if removed_pl:
                suspects["__playlists__"] = sorted(removed_pl)
            # P0.5：并发轮次的 never_owned 缺席也并入 suspects（journal 留痕），**不写卡**
            # （I2′ 已表明"可能拿旧视图回写"，此时连 owned 删除都降级，出卡只会造成噪音）。
            for pl, keys in never_owned_t.items():
                if keys:
                    suspects[pl] = sorted(set(suspects.get(pl, [])) | set(keys))
            if never_owned_pl:
                suspects["__playlists__"] = sorted(
                    set(suspects.get("__playlists__", [])) | never_owned_pl)
            never_owned_pl = set()
            never_owned_t = {pl: set() for pl in never_owned_t}
            removed_pl = set()
            removed_t = {pl: set() for pl in removed_t}

        # 第四轮 P1-3 + 第七轮真机事故（「Yes」歌单被同步带走 5 首）：
        # never_owned 缺席**永不判删**。只有"自己提交过"（owned = base_served ∩ base_submitted）
        # 的缺席才表达删除意图；"交付过、但从未提交过"只说明这台设备看不到 / 放不进它
        # （导入失败、能力受限、本地副本缺失、用户还没打开过这个歌单）——把它当删除会在真机上
        # 误删别人刚加的歌。**曲目级** can_delete=True 也不例外：能删本地 ≠ 它缺席就是删除意图。
        # （**歌单级**自第十三轮 D32 起对可信客户端放松成 owned_pl = base_served，故这里的
        #   never_owned_pl 对它们恒为空；仍走这条的只有 can_delete=False / 未验证 / 已退休的端点。）
        # 这里只留副本供 meta 排查；removed_* 一概不加。I2′ 并发轮次仍并入 suspects。
        no_pl = set(never_owned_pl)
        no_t = {pl: set(ks) for pl, ks in never_owned_t.items() if ks}

        # 安全阀（I5）：单次删除超阈值 → 挂起该批删除，其余照常应用。
        # **第四轮 P1-2**：trusted_direct_delete 空间配置（默认 True）——可信客户端
        # （身份已验证 + 有删除能力 + 未退休，即同空间互信的栖弦）批量删除直接生效，
        # 不再挂起确认卡；只有不可信/无删除能力的端点（如澜音、匿名）仍走安全阀挂起。
        # **第八轮（2026-10-08 用户拍板「不要再弹确认了」）**：delete_cards_enabled=False
        # （默认）时安全阀**永不产生"待确认删除"卡**。三分支：
        #   ① 可信 + trusted_direct_delete → 直接生效（meta.safety_valve.applied 留痕）；
        #   ② 不可信端点 → **既不生效也不出卡**：它没有删除能力，可能只是"没拿到这批曲目"，
        #      不能据此删掉别人的歌；meta.safety_valve.withheld 留痕；
        #   ③ policy.confirm_delete=True（delete_cards_enabled）→ 回到旧的 deferred + 挂卡通道。
        deferred = {}
        applied_valve = None
        withheld_valve = None
        direct = self.trusted_direct_delete and trusted
        if self._safety_trigger(removed_pl, removed_t):
            batch = {
                "playlists": sorted(removed_pl),
                "tracks": {pl: sorted(keys) for pl, keys in removed_t.items() if keys},
            }
            if direct:
                applied_valve = batch                    # 可信直删：超阈值也直接生效
            elif self.delete_cards_enabled:
                deferred = batch                         # 显式开启，才回到"待确认删除"卡
            else:
                withheld_valve = batch                   # 默认：不挂卡、也不应用
            if not direct:
                removed_pl = set()
                removed_t = {pl: set() for pl in removed_t}

        # ---- 事务（单写者） ----
        suppressed_pl: Set[str] = set()
        suppressed_t: Set[str] = set()
        # 新增歌单（并集 + I7 墓碑压制 / 显式恢复）
        for pl in sorted(added_pl):
            live = self._live_tombstone(pl)
            if live is not None:
                if self._crossed_deletion_point(client, live):
                    del self.tombstones[pl]           # 显式恢复：清墓碑
                    if self._ensure_playlist(pl, view[pl]["name"], now):
                        mutated = True
                else:
                    suppressed_pl.add(pl)
                    self._note_restore("playlist", pl, now, context=pl,
                                       tracks=list(view[pl]["tracks"]),
                                       name=view[pl]["name"], client_id=client_id)
                continue
            if self._ensure_playlist(pl, view[pl]["name"], now):
                mutated = True

        # 新增曲目（并集 + (source,songId) 去重；I7 墓碑压制）
        # 顺序（第七轮，用户拍板）：本轮新增的曲目放到歌单**最顶部**，按提交顺序排列，
        # 不再追加到末尾。已有曲目的相对位置不动（纯重排仍由下面的「排序同步」段处理）。
        added_head: Dict[str, List[str]] = {}
        for pl in sorted(view.keys()):
            if pl in suppressed_pl:
                continue   # 歌单被压制 → 整单转待确认恢复，不逐曲处理
            for key in view[pl]["tracks"]:   # 保留提交顺序
                live = self._live_tombstone(key)
                if live is not None:
                    if self._crossed_deletion_point(client, live):
                        del self.tombstones[key]      # 显式恢复：清墓碑
                        tr, ch = self._add_track(key, now)
                        inserted = self._append_to_playlist(pl, tr.tr_id, now)
                        if inserted or ch:
                            mutated = True
                        if inserted:
                            added_head.setdefault(pl, []).append(tr.tr_id)
                    else:
                        suppressed_t.add(key)
                        self._note_restore("track", key, now, context=pl, client_id=client_id)
                    continue
                tr, ch = self._add_track(key, now)
                inserted = self._append_to_playlist(pl, tr.tr_id, now)
                if inserted or ch:
                    mutated = True
                if inserted:
                    added_head.setdefault(pl, []).append(tr.tr_id)

        # 歌单改名（I4：名字不参与身份，仅展示；已有歌单直接更新）
        for pl in sorted(view.keys()):
            if pl in suppressed_pl or pl not in self.playlists or self.playlists[pl].deleted_at is not None:
                continue
            if self.playlists[pl].name != view[pl]["name"]:
                self.playlists[pl].name = view[pl]["name"]
                self.playlists[pl].updated_at = now
                mutated = True

        # 删除（写墓碑；I3 删除优先；I2 消失必须有墓碑）。
        # 删除必然使 revision +1，故提交 revision = 当前 +1（D21 确认水位用）。
        commit_revision = self.revision + 1
        for pl in sorted(removed_pl):
            self._delete_playlist(pl, now, client.client_id, commit_revision)
            mutated = True
        for pl in sorted(removed_t.keys()):
            for key in sorted(removed_t[pl]):
                self._remove_track_global(key, now, client.client_id, commit_revision)
                mutated = True

        # ---- 排序同步：已有歌单若集合不变但提交顺序不同 → 应用提交顺序（纯重排传播）。
        # P1-9 裁决：顺序是同步内容（客户端重排应传播），但**不进 view_hash、不推进 revision**；
        # 只更新 order_changed_at / sort_tag（"tag+1"），modifiedAt 靠它推动客户端拉取新顺序。
        # 只有集合完全一致时才整体重排，避免与"新增/删除"场景混排冲突。
        reordered = False
        for pl in view.keys():
            if pl in suppressed_pl:
                continue
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

        # 新增置顶（第七轮，用户拍板；必须在「排序同步」之后执行）：本轮新增的曲目整体挪到
        # 歌单最顶部，保持提交顺序；已有曲目之间的相对顺序不动。
        # 之所以放在排序同步之后：集合相等时上面会整体改写为提交顺序，会把置顶覆盖掉
        # （第七轮第一次改动的回归原因）。这里再执行一次，两种路径都收敛到"新增在最顶部"。
        # 注意：不做墓碑压制检查以外的判断——只动仍在歌单里的 tid。
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

        # 删除确认（第四轮 P0-B，D21 修订）：所有客户端的确认水位 =
        # "看过删除后视图（deliver 交付不含该键 / 本次提交不再携带该键）" ∧
        # "当前没有回推残留（最近提交不再带该键）"。
        # 栖弦这类"整段覆盖"客户端 GET 后本地可能仍保留副本（下一轮提交照样带回来），
        # 一旦回推 → 从 acked 撤销（carried 记录），墓碑继续存活压制，已确认删除不复活。
        submitted_keys = {k for e in view.values() for k in e["tracks"]}
        for key, t in self.tombstones.items():
            carried = (key in view) if t.element == "playlist" else (key in submitted_keys)
            if carried:
                t.carried.add(client_id)
                t.acked.discard(client_id)
            else:
                t.carried.discard(client_id)
                t.acked.add(client_id)   # 提交不再带该键 = 确认
                # 注：can_delete=False（澜音）也按"提交内容"确认（D21）——GET 看过不算数
                # （删不掉本地、必带回），但提交不再带 = 真采纳；deliver 段已对澜音关掉 ack。

        self._gc_tombstones()
        # P1-10：新增空歌单在 _ingest 已入库，但 mutated 可能漏计——以"歌单集合是否真变"兜底
        if set(self.playlists) != before_pl:
            mutated = True
        if reordered:
            self.sort_tag += 1
            self.order_changed_at = now
        if mutated:
            self.revision += 1
            self.content_changed_at = now

        # I1：仅提交成功后推进 base_submitted（拥有水位；R1 删除判据 = base_served ∩ base_submitted）
        # 例外（D30 整份替换方言、D32 可信客户端的歌单级）只用 base_served，不走这个交集。
        client.base_submitted = {pl_id: {"name": e["name"], "tracks": e["tracks"]}
                                 for pl_id, e in view.items()}

        meta = {
            "added_playlists": sorted(added_pl),
            "removed_playlists": sorted(removed_pl),
            # 第十二轮：被墓碑压制（未采纳）的键**不算新增**——旧写法把"客户端回推已删曲目"
            # 也记成 added_tracks，日志看起来像"合并提交成功、只是没生效"，排查时误导。
            "added_tracks": {pl: sorted(ks - suppressed_t) for pl, ks in added_t.items()
                             if ks - suppressed_t},
            "removed_tracks": {pl: sorted(ks) for pl, ks in removed_t.items() if ks},
            "suspects": suspects,
            "deferred": deferred,
            # 第八轮：安全阀不再弹卡，但把"超阈值的那批删除"留在 meta 里可审计
            "safety_valve": {"applied": applied_valve, "withheld": withheld_valve},
            "suppressed_playlists": sorted(suppressed_pl),
            "suppressed_tracks": sorted(suppressed_t),
            "revision": self.revision,
            "sort_tag": self.sort_tag,
            "mutated": mutated,
            "reordered": reordered,
        }
        self.journal.append({"client": client_id, "revision": self.revision,
                             "meta": meta, "at": now})

        # 自动关闭（多账户共用歌单 §2）：本次提交带回了卡里条目 → 卡消失（本客户端的卡）
        self._close_pending_deletions(client_id, view)

        # 安全阀挂起的删除 → 待确认删除队列（docs/05 §5.3.5）；
        # 同一 client+reason 已有未处理卡 → 复用 pid 并集合并（去重，§2）
        if deferred and (deferred["playlists"] or deferred["tracks"]):
            pid = self._upsert_pending_deletion(
                client_id, None, set(deferred["playlists"]),
                {pl: set(ks) for pl, ks in deferred["tracks"].items()}, now)
            meta["pending_delete_id"] = pid
        # 第七轮：never_owned 缺席一律不判删 ⇒ 永不为真。字段保留给旧客户端/前端兼容，
        # 语义固定为 False（详见上方 never_owned 注释与 docs）。
        meta["never_owned"] = {
            "playlists": sorted(no_pl),
            "tracks": {pl: sorted(ks) for pl, ks in no_t.items() if ks},
        }
        meta["never_owned_applied"] = False

        view, cleanup = self.render_for(client)
        return MergeResult(status=200, view=view, pending_cleanup=cleanup,
                           meta=meta, canonical_mutated=mutated)

    # ---- 首次接入 ---------------------------------------------------------
    def _adopt_initial(self, client: Client, view: Dict[str, dict], now: str,
                       before_pl: Set[str]) -> MergeResult:
        """D8：任何客户端首次接入只增不删；仍受 I7 墓碑压制（身份不可信）。

        before_pl 必须在 _ingest 之前采集（_ingest 已建歌单），
        否则"新增空歌单"无法判定为真变（P1-10）。
        """
        mutated = False
        added_pl: Set[str] = set()
        added_t: Dict[str, List[str]] = {}
        suppressed_pl: Set[str] = set()
        suppressed_t: Set[str] = set()
        for pl in sorted(view.keys()):
            live = self._live_tombstone(pl)
            if live is not None:
                suppressed_pl.add(pl)
                self._note_restore("playlist", pl, now, context=pl,
                                   tracks=sorted(view[pl]["tracks"]),
                                   name=view[pl]["name"], client_id=client.client_id)
                continue
            if self._ensure_playlist(pl, view[pl]["name"], now):
                mutated = True
                added_pl.add(pl)
        added_head: Dict[str, List[str]] = {}
        for pl in sorted(view.keys()):
            if pl in suppressed_pl:
                continue
            for key in view[pl]["tracks"]:   # 保留提交顺序
                if self._live_tombstone(key) is not None:
                    suppressed_t.add(key)
                    self._note_restore("track", key, now, context=pl, client_id=client.client_id)
                    continue
                tr, ch = self._add_track(key, now)
                inserted = self._append_to_playlist(pl, tr.tr_id, now)
                if inserted or ch:
                    mutated = True
                    added_t.setdefault(pl, []).append(key)
                if inserted:
                    added_head.setdefault(pl, []).append(tr.tr_id)

        # 新增置顶（第八轮补：首次接入曾把新增追加到末尾，用户报"栖弦新增的歌跑到了最底下"）。
        # 与 merge() 段同一规则：本轮新增的曲目整体挪到该歌单最顶部，按提交顺序排列；
        # 已有序曲目之间的相对顺序不动。空间为空（真·首次建空间）时结果与"按提交顺序建单"一致。
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
        client.base_submitted = {pl_id: {"name": e["name"], "tracks": e["tracks"]}
                                 for pl_id, e in view.items()}

        meta = {"adopted": True,
                "added_playlists": sorted(added_pl),
                # 第十二轮：同 merge——压制掉的键不算新增（见 merge 里的注释）
                # 注意这里 added_t 是 dict[str, list]（采纳路径按顺序收集），先转 set 再算差集
                "added_tracks": {pl: sorted(set(ks) - suppressed_t) for pl, ks in added_t.items()
                                 if set(ks) - suppressed_t},
                "removed_playlists": [], "removed_tracks": {},
                "suppressed_playlists": sorted(suppressed_pl),
                "suppressed_tracks": sorted(suppressed_t),
                "revision": self.revision, "sort_tag": self.sort_tag,
                "mutated": mutated, "reordered": False}
        self.journal.append({"client": client.client_id, "revision": self.revision,
                             "meta": meta, "at": now})
        view, cleanup = self.render_for(client)
        return MergeResult(status=201, view=view, pending_cleanup=cleanup,
                           meta=meta, canonical_mutated=mutated)

    # ---- 删除/恢复辅助 ----------------------------------------------------
    def _delete_playlist(self, pl_id: str, now: str, origin: str, commit_revision: int) -> None:
        pl = self.playlists.get(pl_id)
        tname = pl_id
        if pl is not None:
            pl.deleted_at = now
            pl.updated_at = now
            tname = pl.name or pl_id     # P0-7：墓碑记录被删歌单名，供跨方言同名抑制
            if pl_id in self.playlist_order:
                self.playlist_order.remove(pl_id)
        self._write_tombstone("playlist", pl_id, now, origin, commit_revision,
                              target_name=tname)

    def _gc_tombstones(self) -> None:
        """确认水位 GC（D21/T3，第四轮 P0-B）：删除发生时已注册、且未退休的
        客户端都已**看过删除后视图**（acked 由 deliver 交付 + merge 提交确认共同维护，
        carried 回推会自动撤销 ack）→ 可 GC。

        看过删除视图 = 根因 A 修好后客户端已应用删除（时间戳压过本地），不会再回推；
        一直在回推残留的设备 acked 会被 merge 撤销 ⇒ 墓碑保留 ⇒ 已确认删除不复活。
        纯 GET / 已同步安静的设备交付即 ack ⇒ 墓碑能收走（不再永久累积）。

        **第十二轮补（2026-10-08 真机事故）**：`carried` 非空 ⇒ 明知道还有客户端在往回推这个键，
        一律不收——只靠 acked 判定会与"回推中"赛跑：某个交付瞬间它 ack 了 → 全员 ack → 收走墓碑
        → 它下一轮回推（本地还没删掉）就把已删条目**复活**（真机 rev 21：洛雪删掉的歌又出现在
        栖弦「纯音乐」第一位）。等它真的采纳（提交不再带该键）后 carried 清空，墓碑才收得走。"""
        now_clients = {cid: c for cid, c in self.clients.items()
                       if not c.retired and c.first_seen_revision <= self.revision}
        for key, t in list(self.tombstones.items()):
            if t.carried:
                continue
            if all(cid in t.acked for cid in now_clients):
                del self.tombstones[key]

    # ---- 客户端生命周期（第四轮 P0-B） ------------------------------------
    # 惰性退休：last_seen 超过 _RETIRED_AFTER_DAYS 未活动 → retired=True，
    # 退出墓碑确认水位（防止某台死设备永久钉住墓碑）。merge/deliver 时惰性检查，
    # 不做后台定时器。
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
            pass   # 退休判定失败不影响主流程

    # ---- 确认通道 ---------------------------------------------------------
    def confirm_delete(self, pending_id: str) -> MergeResult:
        """用户确认挂起的删除 → 写墓碑、推进 revision（docs/05 §5.3.5 确认通道）。"""
        p = self.pending_deletions.pop(pending_id, None)
        if p is None:
            raise KeyError(f"pending_delete {pending_id!r} 不存在")
        now = self._now()
        mutated = False
        commit_revision = self.revision + 1
        for pl in p["playlists"]:
            self._delete_playlist(pl, now, p["client"], commit_revision)
            mutated = True
        for pl, keys in p["tracks"].items():
            for key in keys:
                self._remove_track_global(key, now, p["client"], commit_revision)
                mutated = True
        if mutated:
            self.revision += 1
            self.content_changed_at = now
        self.journal.append({"client": p["client"], "revision": self.revision,
                             "action": "confirm_delete", "at": now})
        return MergeResult(status=200, view=self.render_for(self.clients[p["client"]])[0],
                           canonical_mutated=mutated)

    def reject_delete(self, pending_id: str) -> None:
        """用户放弃挂起的删除：丢弃，条目保留（30 天未处理自动按此结案）。"""
        self.pending_deletions.pop(pending_id, None)

    def confirm_restore(self, key: str) -> None:
        """用户确认恢复被墓碑压制的条目 → 真正加回并清墓碑（docs/05 I7/D20 确认通道）。"""
        rec = self.pending_restores.pop(key, None)
        now = self._now()
        mutated = False
        if rec is None:
            self.tombstones.pop(key, None)
            return
        if rec["kind"] == "playlist":
            if key in self.tombstones:
                del self.tombstones[key]
            if self._ensure_playlist(key, rec.get("name") or "", now):
                mutated = True
            for tk in rec.get("tracks", []):
                if tk in self.tombstones:
                    del self.tombstones[tk]
                tr, ch = self._add_track(tk, now)
                if self._append_to_playlist(key, tr.tr_id, now) or ch:
                    mutated = True
        else:  # track：加回记录到的歌单
            for pl_id in rec.get("playlists", []):
                if pl_id not in self.playlists or self.playlists[pl_id].deleted_at is not None:
                    continue
                if key in self.tombstones:
                    del self.tombstones[key]
                tr, ch = self._add_track(key, now)
                if self._append_to_playlist(pl_id, tr.tr_id, now) or ch:
                    mutated = True
        if mutated:
            self.revision += 1
            self.content_changed_at = now
        self.journal.append({"client": "user-confirm", "revision": self.revision,
                             "action": "confirm_restore", "key": key, "at": now})

    def reject_restore(self, key: str) -> None:
        """用户保持删除：丢弃恢复候选，墓碑保留。"""
        self.pending_restores.pop(key, None)

    # ---- UI 权威操作（网页管理端） ----------------------------------------
    def reorder_playlists(self, order: List[str], origin: str = "ui") -> None:
        """网页端重排歌单显示顺序（全局变更，客户端下次拉取生效）。

        order 必须恰好包含当前全部存活歌单（不重不漏）。
        P1-9 裁决：排序变化**不推进 revision（内容版本）**，只走 sort_tag/order_changed_at；
        渲染 modifiedAt 依赖 order_changed_at 推动客户端拉取新顺序。
        """
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
        """网页端删除歌单：写墓碑并推进 revision（所有客户端下次拉取时同步删除）。

        origin 形如 "ui:<user>"，用于日志追溯。
        """
        pl = self.playlists.get(pl_id)
        if pl is None or pl.deleted_at is not None:
            raise KeyError(pl_id)
        now = self._now()
        commit_revision = self.revision + 1
        self._delete_playlist(pl_id, now, origin, commit_revision)
        self.revision += 1
        self.content_changed_at = now
        self.journal.append({"client": origin, "revision": self.revision,
                             "action": "playlist_delete",
                             "removed_playlists": [pl.name], "at": now})

    def reorder_tracks(self, pl_id: str, order: List[str], origin: str = "ui") -> None:
        """网页端重排歌单内歌曲顺序（全局变更，客户端下次拉取生效）。

        order 是曲目身份键（"src:sid"）列表，必须恰好包含该歌单当前全部存活歌曲。
        """
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
        """网页端删除歌单内一首歌：全局删除（写墓碑，从所有歌单摘除并同步传播）。"""
        pl = self.playlists.get(pl_id)
        if pl is None or pl.deleted_at is not None:
            raise KeyError(pl_id)
        tr_id = self.identity_to_tr.get(key)
        if tr_id is None or tr_id not in pl.track_ids:
            raise KeyError(key)
        now = self._now()
        commit_revision = self.revision + 1
        self._remove_track_global(key, now, origin, commit_revision)
        self.revision += 1
        self.content_changed_at = now
        self.journal.append({"client": origin, "revision": self.revision,
                             "action": "track_delete", "playlist": pl.name,
                             "removed_tracks": {pl_id: [key]}, "at": now})


# 便捷别名
Engine = SyncSpace
