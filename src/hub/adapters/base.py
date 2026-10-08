"""适配器包基础：身份键 / 解析异常 / 渲染上下文 / 方言注册表条目（第六轮 §1）。

从原 src/hub/adapters.py 原样搬移的通用小工具与常量；Dialect/RenderContext 为
第六轮新增——让"加一个客户端 = 新增一个适配器文件 + 注册一行"成立（docs/13）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

# 身份键分隔符
KEY_SEP = ":"


class ParseError(Exception):
    """客户端负载结构损坏 / 无法表达，应返回 400 且不动基线。"""


# ---------------------------------------------------------------------------
# 通用小工具（原 adapters.py 原样搬移）
# ---------------------------------------------------------------------------

def _interval_to_duration_ms(interval) -> Optional[int]:
    """'03:27' → 207000。非法/缺失 → None。"""
    if not isinstance(interval, str):
        return None
    m = re.fullmatch(r"(\d+):(\d{2})", interval.strip())
    if not m:
        return None
    return (int(m.group(1)) * 60 + int(m.group(2))) * 1000


def _duration_ms_to_interval(duration_ms) -> str:
    """207000 → '03:27'（零填充，canonical-v1 §12 硬要求 4）。"""
    if duration_ms is None:
        return "0:00"
    total_s = max(0, int(duration_ms) // 1000)
    return f"{total_s // 60:02d}:{total_s % 60:02d}"


def identity_key(source: str, song_id: str) -> str:
    return f"{source}{KEY_SEP}{song_id}"


# ---------------------------------------------------------------------------
# 第六轮 §1.3：渲染上下文 / 方言注册表条目
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RenderContext:
    """一次交付渲染所需的全部上下文（统一各方言渲染器签名差异）。

    原 render_cyshine / render_ceru 第 4 参不同（ceru=cleanup，cyshine=opaque），
    且 cyshine 多 revision/pl_times/opaque_tracks —— 统一为这一个对象后，
    路由层不再按方言名分叉（docs/13 §1.3）。
    """

    view: dict
    meta_pool: dict
    native_ids: dict
    pending_cleanup: Optional[list]
    opaque: dict
    opaque_tracks: Optional[dict]
    revision: int
    generated_at: str
    playlist_times: dict  # {pl_id: {"created_at":…, "updated_at":…}}


@dataclass(frozen=True)
class Dialect:
    """一个客户端方言的注册表条目（docs/13 §1.3）。

    - name：引擎/存储里用的稳定 id（cyshine-v1 / ceru-plugin / lx-x），不许改；
    - roots/files：路由路径白名单（第一段 / 文件名），路由层按它循环注册；
    - parse：(payload) -> (submission, meta_delta, opaque, warnings)；
    - render：(ctx: RenderContext) -> 客户端 payload；
    - capabilities：能力表（delete_track / delete_playlist / create_playlist / reorder）；
    - empty_view_on_no_baseline：无基线 404 时是否给"200 + 空视图"（原 ceru 例外）；
    - file_fallback：文件名兜底（地址里带任意目录名时，按 basename 分派）。
    """

    name: str
    roots: tuple
    files: tuple
    parse: Callable
    render: Callable
    capabilities: dict
    empty_view_on_no_baseline: bool = False
    file_fallback: bool = False
    deliver_ack_on_view: bool = True  # 第六轮 §1 回归修复：交付"看过删除视图"能否计入确认水位。
                                      # True=栖弦类（看过即应用、下一轮不再带回残留）；
                                      # False=澜音类（删不掉本地、看过必带回，确认只来自提交）。
    bypass_files: tuple = ()          # 第六轮 §2：整文件 opaque 旁路（如洛雪 settings.json /
                                      # user_apis.json）：PUT 原样入库、GET 原样归还（按客户端分桶），
                                      # 不解析、不合并、不跨设备。空=不走旁路。
