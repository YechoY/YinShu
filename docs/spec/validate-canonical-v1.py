#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""canonical-v1 校验器（仅标准库，无第三方依赖）。

用法:
    python spec/validate-canonical-v1.py spec/examples/canonical-v1.example.json

退出码: 0 = 通过（可能有警告）; 1 = 有错误; 2 = 用法/读取失败。

实现 canonical-v1.md 的 V1–V12 校验项。规则编号见规范文本。
"""
from __future__ import annotations

import json
import re
import sys

ULID_RE = re.compile(r"^(pl|tr)_[0-9A-HJKMNP-TV-Z]{26}$")
ID_RE = re.compile(r"^(pl|tr)_[0-9A-HJKMNP-TV-Z]{26}$")
SRC_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

RANK = {"master": 0, "atmos_plus": 1, "atmos": 2, "hires": 3, "flac24bit": 4,
        "flac": 5, "320k": 6, "192k": 7, "128k": 8}

TOP_KEYS = {"schemaVersion", "generatedAt", "playlists", "tracks", "tombstones", "ext"}
PL_KEYS = {"id", "name", "description", "coverUrl", "createdAt", "updatedAt", "deletedAt",
           "trackIds", "platforms", "ownerRef", "ext"}
PLAT_KEYS = {"nativeId", "nativeName", "ext"}
TR_KEYS = {"id", "identity", "title", "artists", "album", "durationMs", "artworkUrl",
           "releaseDate", "qualities", "preferredQuality", "platforms", "availability",
           "addedAt", "updatedAt", "deletedAt", "ext"}
ID_KEYS = {"source", "songId"}
Q_KEYS = {"code", "sizeBytes", "available", "ext"}
PF_KEYS = {"songId", "mediaMid", "albumId", "qualitys", "playable", "unavailableReason", "ext"}
NQ_KEYS = {"nativeCode", "code", "sizeBytes"}
AVAIL_KEYS = {"state", "reason", "checkedAt"}
TS_KEYS = {"element", "id", "deletedAt", "device", "reason"}

MAX_BYTES_WARN = 1024 * 1024  # S8


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def err(self, path: str, msg: str) -> None:
        self.errors.append(f"{path}: {msg}")

    def warn(self, path: str, msg: str) -> None:
        self.warnings.append(f"{path}: {msg}")

    def check_known_keys(self, path: str, obj: dict, allowed: set[str]) -> None:
        for k in obj:
            if k not in allowed:
                self.warn(f"{path}.{k}", "规范未定义的字段，应当放入 ext（E1/E2）")

    def need(self, path: str, obj: dict, keys: tuple[str, ...]) -> bool:
        ok = True
        for k in keys:
            if k not in obj:
                self.err(path, f"缺少必填字段 {k!r}")
                ok = False
        return ok

    def ts(self, path: str, value: object, nullable: bool = False) -> None:
        if value is None and nullable:
            return
        if not isinstance(value, str) or not ISO_RE.match(value):
            self.err(path, f"必须是 ISO8601 UTC 时间戳（T1），实际 {value!r}")


def is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def check_playlist(rep: Report, pl: dict, i: int, track_ids: set[str]) -> None:
    p = f"playlists[{i}]"
    if not isinstance(pl, dict):
        rep.err(p, "必须是对象")
        return
    rep.need(p, pl, ("id", "name", "createdAt", "updatedAt", "trackIds"))
    rep.check_known_keys(p, pl, PL_KEYS)

    pid = pl.get("id")
    if not isinstance(pid, str) or not ID_RE.match(pid):
        rep.err(f"{p}.id", f"必须是 pl_<ULID> 形式（I4），实际 {pid!r}")

    name = pl.get("name")
    if not isinstance(name, str) or not name.strip():
        rep.err(f"{p}.name", "必须是非空字符串")
    elif len(name) > 512:
        rep.err(f"{p}.name", "长度超过 512")

    for f, limit in (("description", 4096), ("coverUrl", 2048)):
        v = pl.get(f)
        if v is not None and (not isinstance(v, str) or len(v) > limit):
            rep.err(f"{p}.{f}", f"必须为 null 或 ≤{limit} 的字符串")

    rep.ts(f"{p}.createdAt", pl.get("createdAt"))
    rep.ts(f"{p}.updatedAt", pl.get("updatedAt"))
    rep.ts(f"{p}.deletedAt", pl.get("deletedAt"), nullable=True)

    tids = pl.get("trackIds")
    if not isinstance(tids, list):
        rep.err(f"{p}.trackIds", "必须是数组")
    else:
        seen: set[str] = set()
        for j, tid in enumerate(tids):
            if not isinstance(tid, str) or not tid.startswith("tr_"):
                rep.err(f"{p}.trackIds[{j}]", f"必须是 tr_<ULID> 引用，实际 {tid!r}")
                continue
            if tid in seen:
                rep.err(f"{p}.trackIds[{j}]", f"重复引用 {tid}")
            seen.add(tid)
            if tid not in track_ids:
                rep.err(f"{p}.trackIds[{j}]", f"引用的曲目不存在于 tracks[]（S6）：{tid}")

    plats = pl.get("platforms")
    if plats is not None:
        if not isinstance(plats, dict):
            rep.err(f"{p}.platforms", "必须是对象")
        else:
            for code, ref in plats.items():
                q = f"{p}.platforms.{code}"
                if not SRC_RE.match(code):
                    rep.err(f"{q}", "平台 code 必须匹配 ^[a-z][a-z0-9_]{0,31}$（E-SRC-1）")
                if not isinstance(ref, dict):
                    rep.err(q, "必须是对象")
                    continue
                rep.check_known_keys(q, ref, PLAT_KEYS)
                rep.need(q, ref, ("nativeId",))
                nid = ref.get("nativeId")
                if not isinstance(nid, str) or not nid:
                    rep.err(f"{q}.nativeId", "必须是非空字符串（I4 映射表用）")


def check_track(rep: Report, tr: dict, i: int) -> None:
    p = f"tracks[{i}]"
    if not isinstance(tr, dict):
        rep.err(p, "必须是对象")
        return
    rep.need(p, tr, ("id", "identity", "title", "artists", "addedAt", "updatedAt"))
    rep.check_known_keys(p, tr, TR_KEYS)

    tid = tr.get("id")
    if not isinstance(tid, str) or not ULID_RE.match(tid):
        rep.err(f"{p}.id", f"必须是 tr_<ULID> 形式，实际 {tid!r}")

    ident = tr.get("identity")
    if not isinstance(ident, dict):
        rep.err(f"{p}.identity", "必须是对象")
    else:
        rep.check_known_keys(f"{p}.identity", ident, ID_KEYS)
        rep.need(f"{p}.identity", ident, ("source", "songId"))
        src, sid = ident.get("source"), ident.get("songId")
        if not isinstance(src, str) or not SRC_RE.match(src):
            rep.err(f"{p}.identity.source", f"平台 code 非法（E-SRC-1）：{src!r}")
        if not isinstance(sid, str) or not sid.strip():
            rep.err(f"{p}.identity.songId", "必须是非空字符串")
        else:
            if len(sid) > 2048:
                rep.err(f"{p}.identity.songId", "长度超过 2048")
            if isinstance(src, str) and sid.startswith(f"{src}_"):
                rep.err(f"{p}.identity.songId", f"songId 必须裸 id，禁止带 {src}_ 前缀（I2）")

    title = tr.get("title")
    if not isinstance(title, str) or not title.strip():
        rep.err(f"{p}.title", "必须是非空字符串")
    elif len(title) > 4096:
        rep.err(f"{p}.title", "长度超过 4096")

    artists = tr.get("artists")
    if not isinstance(artists, list):
        rep.err(f"{p}.artists", "必须是数组（单歌手也要用数组）")
    else:
        if not artists:
            rep.warn(f"{p}.artists", "为空数组，渲染端应当给占位名")
        for j, a in enumerate(artists):
            if not isinstance(a, dict) or not isinstance(a.get("name"), str) or not a["name"].strip():
                rep.err(f"{p}.artists[{j}]", "必须是 {name: 非空字符串}")

    album = tr.get("album")
    if album is not None:
        if not isinstance(album, dict) or not isinstance(album.get("name"), str) or not album["name"].strip():
            rep.err(f"{p}.album", "必须为 null 或含非空 name 的对象")

    dur = tr.get("durationMs")
    if dur is not None and (not is_int(dur) or dur < 0):
        rep.err(f"{p}.durationMs", f"必须为 null 或非负整数（S4），实际 {dur!r}")

    art = tr.get("artworkUrl")
    if art is not None and (not isinstance(art, str) or len(art) > 2048):
        rep.err(f"{p}.artworkUrl", "必须为 null 或 ≤2048 的字符串")

    rd = tr.get("releaseDate")
    if rd is not None and (not isinstance(rd, str) or not DATE_RE.match(rd)):
        rep.err(f"{p}.releaseDate", "必须为 null 或 YYYY-MM-DD")

    qualities = tr.get("qualities")
    codes: list[str] = []
    if qualities is not None:
        if not isinstance(qualities, list):
            rep.err(f"{p}.qualities", "必须是数组")
        else:
            if len(qualities) > 128:
                rep.err(f"{p}.qualities", "超过 128 项（对齐栖弦 assertContentPage 限制）")
            for j, q in enumerate(qualities):
                qp = f"{p}.qualities[{j}]"
                if not isinstance(q, dict):
                    rep.err(qp, "必须是对象")
                    continue
                rep.check_known_keys(qp, q, Q_KEYS)
                code = q.get("code")
                if not isinstance(code, str) or not code:
                    rep.err(f"{qp}.code", "必须是非空字符串")
                    continue
                codes.append(code)
                if code not in RANK:
                    rep.warn(f"{qp}.code", f"未登记品质码 {code!r}：必须原样保留、禁止静默降级为 128k（E-QUAL），建议 ext.rawCode")
                size = q.get("sizeBytes")
                if size is not None and (not is_int(size) or size < 0):
                    rep.err(f"{qp}.sizeBytes", "必须为非负整数")
            known = [RANK[c] for c in codes if c in RANK]
            if known != sorted(known):
                rep.warn(f"{p}.qualities", "应当按优劣降序排列（Q1：master > atmos_plus > atmos > hires > flac24bit > flac > 320k > 192k > 128k）")

    pref = tr.get("preferredQuality")
    if pref is not None:
        if not isinstance(pref, str):
            rep.err(f"{p}.preferredQuality", "必须为 null 或字符串")
        elif codes and pref not in codes:
            rep.warn(f"{p}.preferredQuality", f"{pref!r} 不在 qualities 里（Q2）")

    plats = tr.get("platforms")
    if plats is not None:
        if not isinstance(plats, dict):
            rep.err(f"{p}.platforms", "必须是对象")
        else:
            for code, ref in plats.items():
                qp = f"{p}.platforms.{code}"
                if not SRC_RE.match(code):
                    rep.err(qp, "平台 code 非法（E-SRC-1）")
                if not isinstance(ref, dict):
                    rep.err(qp, "必须是对象")
                    continue
                rep.check_known_keys(qp, ref, PF_KEYS)
                rep.need(qp, ref, ("songId",))
                if not isinstance(ref.get("songId"), str) or not ref["songId"]:
                    rep.err(f"{qp}.songId", "必须是非空字符串")
                for j, nq in enumerate(ref.get("qualitys") or []):
                    nqp = f"{qp}.qualitys[{j}]"
                    if not isinstance(nq, dict):
                        rep.err(nqp, "必须是对象")
                        continue
                    rep.check_known_keys(nqp, nq, NQ_KEYS)
                    if not isinstance(nq.get("nativeCode"), str) or not nq["nativeCode"]:
                        rep.err(f"{nqp}.nativeCode", "必须保留原生码（P4）")
            if "identity" in tr and isinstance(tr["identity"], dict) and tr["identity"].get("source") not in plats:
                rep.warn(f"{p}.platforms", f"应当包含 identity.source={tr['identity'].get('source')!r} 对应的引用（4.3）")

    avail = tr.get("availability")
    if avail is not None:
        if not isinstance(avail, dict):
            rep.err(f"{p}.availability", "必须是对象")
        else:
            rep.check_known_keys(f"{p}.availability", avail, AVAIL_KEYS)
            rep.need(f"{p}.availability", avail, ("state",))
            if avail.get("state") not in {"ok", "unavailable", "unknown"}:
                rep.err(f"{p}.availability.state", f"必须是 ok/unavailable/unknown，实际 {avail.get('state')!r}")
            rep.ts(f"{p}.availability.checkedAt", avail.get("checkedAt"), nullable=True)

    rep.ts(f"{p}.addedAt", tr.get("addedAt"))
    rep.ts(f"{p}.updatedAt", tr.get("updatedAt"))
    rep.ts(f"{p}.deletedAt", tr.get("deletedAt"), nullable=True)


def validate(doc: object) -> Report:
    rep = Report()
    if not isinstance(doc, dict):
        rep.err("$", "顶层必须是对象")
        return rep

    for k in doc:
        if k not in TOP_KEYS:
            rep.err(f"$.{k}", "顶层禁止出现未定义字段（S-T1），请放入 ext")

    rep.need("$", doc, ("schemaVersion", "generatedAt", "playlists", "tracks"))
    if doc.get("schemaVersion") != 1:
        rep.err("$.schemaVersion", f"本规范必须为 1，实际 {doc.get('schemaVersion')!r}")
    if "generatedAt" in doc:
        rep.ts("$.generatedAt", doc.get("generatedAt"))
    if "ext" in doc and not isinstance(doc["ext"], dict):
        rep.err("$.ext", "必须是对象")

    playlists = doc.get("playlists")
    tracks = doc.get("tracks")
    if not isinstance(playlists, list):
        rep.err("$.playlists", "必须是数组")
        playlists = []
    if not isinstance(tracks, list):
        rep.err("$.tracks", "必须是数组")
        tracks = []

    ids: set[str] = set()
    for i, tr in enumerate(tracks):
        check_track(rep, tr, i)
        if isinstance(tr, dict) and isinstance(tr.get("id"), str):
            if tr["id"] in ids:
                rep.err(f"tracks[{i}].id", f"规范 id 重复：{tr['id']}")
            ids.add(tr["id"])

    ident_seen: dict[str, int] = {}
    for i, tr in enumerate(tracks):
        if not isinstance(tr, dict):
            continue
        ident = tr.get("identity")
        if isinstance(ident, dict) and isinstance(ident.get("source"), str) and isinstance(ident.get("songId"), str):
            key = f"{ident['source']}:{ident['songId']}"
            if key in ident_seen:
                rep.err(f"tracks[{i}].identity", f"身份键重复（I3）：{key}（首次出现在 tracks[{ident_seen[key]}]）")
            else:
                ident_seen[key] = i

    for i, pl in enumerate(playlists):
        check_playlist(rep, pl, i, ids)

    pl_ids: set[str] = set()
    for i, pl in enumerate(playlists):
        if isinstance(pl, dict) and isinstance(pl.get("id"), str):
            if pl["id"] in pl_ids:
                rep.err(f"playlists[{i}].id", f"规范 id 重复：{pl['id']}")
            pl_ids.add(pl["id"])

    toms = doc.get("tombstones", [])
    if not isinstance(toms, list):
        rep.err("$.tombstones", "必须是数组")
    else:
        for i, t in enumerate(toms):
            p = f"tombstones[{i}]"
            if not isinstance(t, dict):
                rep.err(p, "必须是对象")
                continue
            rep.check_known_keys(p, t, TS_KEYS)
            rep.need(p, t, ("element", "id", "deletedAt"))
            if t.get("element") not in {"track", "playlist"}:
                rep.err(f"{p}.element", f"必须是 track/playlist，实际 {t.get('element')!r}")
            if not isinstance(t.get("id"), str) or not t["id"]:
                rep.err(f"{p}.id", "必须是非空字符串")
            rep.ts(f"{p}.deletedAt", t.get("deletedAt"))

    return rep


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    path = argv[1]
    try:
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        print(f"读取/解析失败: {exc}")
        return 2

    rep = validate(doc)
    for w in rep.warnings:
        print(f"WARN  {w}")
    for e in rep.errors:
        print(f"ERROR {e}")

    size = len(json.dumps(doc, ensure_ascii=False).encode("utf-8"))
    n_pl = len(doc.get("playlists", [])) if isinstance(doc, dict) else 0
    n_tr = len(doc.get("tracks", [])) if isinstance(doc, dict) else 0
    n_tm = len(doc.get("tombstones", []) or []) if isinstance(doc, dict) else 0
    print(f"\n{path}: {n_pl} 歌单 / {n_tr} 曲目 / {n_tm} 墓碑 / {size} 字节")
    print(f"结果: {len(rep.errors)} 错误 / {len(rep.warnings)} 警告")
    if size > MAX_BYTES_WARN:
        print(f"WARN  文档 {size} 字节 > 1 MiB，应当分片（S8/PF1）")
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
