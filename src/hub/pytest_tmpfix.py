"""pytest 启动期插件：把 Python 临时目录固定到项目 tmp/（相对路径动态推导）。

本机系统 TEMP 不可写，Python tempfile.gettempdir() 会回退到当前工作目录，
并在探测候选目录时把 "blat" 探针文件 / tmp* 空文件残留在项目根。
本插件通过 pyproject.toml 的 addopts "-p hub.pytest_tmpfix" 加载，
执行时机早于 conftest.py（在 pytest 配置/收集之前），从进程最早阶段
杜绝一切 tempfile 探测落进项目根。上传 GitHub 后其他机器同样生效，
因为路径由 __file__ 动态推导，不依赖任何绝对路径。
"""
import tempfile
from pathlib import Path

_TMP = Path(__file__).resolve().parents[2] / "tmp"
_TMP.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(_TMP)
