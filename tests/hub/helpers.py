"""hub HTTP 测试的共享工具（两个 test 文件都用）。

- `HttpClient`：Basic 认证 + GET/PUT/POST，返回 (status, headers, parsed_json)
- `cyshine_payload` / `ceru_payload`：两端线格式的构造器
- `cyshine_tracks`：从栖弦渲染结果里取 {歌单 id: {musicId...}}
- `rmtree_force`：Windows 上删除（Python/.NET/PowerShell 均假成功）→ rename 兜底
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request


def do_request(req):
    """发请求 → (status, headers, body)。非 2xx 也返回，不抛异常。
    timeout=30：本机 Windows IO（Defender/索引）间歇慢，10s 会误报超时。"""
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            parsed = json.loads(body.decode("utf-8")) if body else None
            return r.status, r.headers, parsed
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, e.headers, json.loads(body.decode("utf-8")) if body else None
        except Exception:
            return e.code, e.headers, body


class HttpClient:
    def __init__(self, base: str, user: str, pw: str):
        self.base = base.rstrip("/")
        tok = base64.b64encode(f"{user}:{pw}".encode()).decode()
        self.headers = {"Authorization": f"Basic {tok}",
                        "Content-Type": "application/json; charset=utf-8"}

    def get(self, path, extra_headers=None):
        h = dict(self.headers)
        if extra_headers:
            h.update(extra_headers)
        return do_request(urllib.request.Request(self.base + path, headers=h))

    def put(self, path, payload):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data,
                                     headers=self.headers, method="PUT")
        return do_request(req)

    def post(self, path, payload=None):
        data = json.dumps(payload or {}).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data,
                                     headers=self.headers, method="POST")
        return do_request(req)


def cyshine_payload(playlists: list, local_tracks: dict = None) -> dict:
    """栖弦 payload。playlists: [{id,name,tracks:[(src,sid,title)]}]
    local_tracks: {playlist_id: [原始 track 对象]} —— 本地/文件曲目（I6 opaque）。"""
    data = []
    for pl in playlists:
        tracks = []
        for src, sid, title in pl["tracks"]:
            tracks.append({
                "musicId": f"{src}_{sid}", "name": title, "singer": "歌手",
                "albumName": "专辑", "source": src, "quality": "hires",
                "picUrl": "", "musicInfo": {
                    "id": f"{src}_{sid}", "name": title, "singer": "歌手",
                    "source": src, "interval": "03:27",
                    "meta": {"songId": sid},
                },
            })
        if local_tracks and str(pl["id"]) in local_tracks:
            tracks.extend(local_tracks[str(pl["id"])])
        data.append({"version": 1, "id": pl["id"], "name": pl["name"],
                     "tracks": tracks, "createdAt": "2026-10-05T00:00:00Z",
                     "updatedAt": "2026-10-05T00:00:00Z"})
    return {"schemaVersion": 1, "generatedAt": "2026-10-05T00:00:00Z",
            "sections": {"playlists": {"data": data},
                         "appearance": {"data": {"themeSeedArgb": 4289230000}},
                         "musicSources": {"data": []}}}


def ceru_payload(playlists: list) -> dict:
    """澜音插件线格式。playlists: [{id,name,tracks:[(src,sid,title)]}]"""
    out = []
    for pl in playlists:
        out.append({"id": pl["id"], "name": pl["name"], "tracks": [
            {"source": s, "songId": i, "title": t, "singer": "歌手",
             "album": "专辑", "durationMs": 207000} for s, i, t in pl["tracks"]]})
    return {"schemaVersion": 1, "playlists": out}


def cyshine_tracks(payload) -> dict:
    """{歌单 native id: {musicId...}}（本地曲目 musicId 为 None）。"""
    out = {}
    for pl in payload["sections"]["playlists"]["data"]:
        out[pl["id"]] = {t.get("musicId") for t in pl["tracks"]}
    return out


def _gone_dir(path):
    """PowerShell Test-Path 双视角验证目录是否真的没了。
    本机 os.path.exists 假阴性/假阳性均不可靠（2026-10-06 实测：os.remove/.NET 删除
    返回成功但 Test-Path 双视角仍 True），所以不依赖 exists 短路。"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", f"Test-Path -LiteralPath '{path}'"],
            capture_output=True, timeout=30)
        return r.stdout.strip() != b"True"
    except Exception:
        return False


def rmtree_force(path):
    """本机 Windows 的删除不可靠：os.remove / .NET File.Delete / PowerShell Remove-Item
    都实测"返回成功但文件仍在"（Test-Path 双视角确认），cmd rd 也失败；
    改名/移动可靠（os.rename / Move-Item 实测可用）。
    策略：先 shutil.rmtree（正常系统快路径）→ Test-Path 双验证 → 仍残留则
    rename 到 `.trash-<ts>`（原路径立即消失，残留集中带统一前缀，不散落根目录）。"""
    shutil.rmtree(path, ignore_errors=True)
    if _gone_dir(path):
        return
    trash = f"{path}.trash-{time.strftime('%Y%m%d%H%M%S')}-{os.getpid()}"
    try:
        os.rename(path, trash)
    except Exception:
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"Move-Item -LiteralPath '{path}' -Destination '{trash}' -Force"],
                capture_output=True, timeout=30)
        except Exception:
            pass