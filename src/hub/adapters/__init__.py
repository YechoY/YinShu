"""方言适配器包（第六轮 §1）：自动发现 + 注册表 + 兼容转发。

- 自动发现：pkgutil 扫描本包模块，收集各模块的 `DIALECT`（Dialect 条目）进 REGISTRY。
  加一个新客户端 = 新增一个 <名字>.py（导出 DIALECT），不改 api.py / store.py / 引擎。
- 兼容层：旧符号（ParseError / KEY_SEP / identity_key / CAPABILITIES / parse_* / render_*）
  继续从 `hub.adapters` 顶层导出，`from . import adapters` 的旧引用零变化。
- 引擎注入：模块加载完成时把注册表方言集注入 engine.SyncSpace.KNOWN_DIALECTS
  （§1.6；未加载本包时引擎保留内建默认值，engine 单测不受影响）。
"""
from __future__ import annotations

import importlib
import pkgutil
from typing import Dict

from .base import KEY_SEP, ParseError, RenderContext, Dialect, identity_key

# 兼容层：两个既有方言的直接符号（新方言只需要注册 DIALECT，无需顶层导出）
from .cyshine import parse_cyshine, render_cyshine
from .ceru import parse_ceru, render_ceru


def _discover() -> Dict[str, Dialect]:
    registry: Dict[str, Dialect] = {}
    for _m in pkgutil.iter_modules(__path__):
        if _m.name == "base" or _m.name.startswith("_"):
            continue
        _mod = importlib.import_module(f"{__name__}.{_m.name}")
        _d = getattr(_mod, "DIALECT", None)
        if _d is not None and getattr(_d, "name", None):
            registry[_d.name] = _d
    return registry


# 方言注册表：{name: Dialect}。路由层 / 能力表 / 引擎隔离 都以它为唯一来源。
REGISTRY: Dict[str, Dialect] = _discover()

# 能力表（docs/04 §4.3 capabilities()）：单一来源 = 注册表条目的 capabilities。
# 旧引用 `adapters.CAPABILITIES.get(dialect, {})` 继续可用。
CAPABILITIES: Dict[str, dict] = {name: d.capabilities for name, d in REGISTRY.items()}


def _inject_engine_known_dialects() -> None:
    """§1.6：把注册表方言集注入引擎 KNOWN_DIALECTS（未知方言隔离以它为准）。"""
    try:
        from engine.engine import SyncSpace

        SyncSpace.set_known_dialects(tuple(REGISTRY))
    except Exception:  # 引擎未就绪时静默——engine 单测直接 import 时用内建默认
        pass


_inject_engine_known_dialects()

__all__ = [
    "REGISTRY",
    "CAPABILITIES",
    "KEY_SEP",
    "ParseError",
    "RenderContext",
    "Dialect",
    "identity_key",
    "parse_cyshine",
    "render_cyshine",
    "parse_ceru",
    "render_ceru",
]
