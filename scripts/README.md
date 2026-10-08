# scripts —— 一次性排查 / 复现脚本（不是产品代码）

这里的东西**没有任何产品路径依赖**，用完可以直接删；写文档时不要把某个脚本当成接口。

| 目录 | 放什么 |
|---|---|
| `scripts/engine/` | 引擎问题的定点复现（安全阀、墓碑 GC、T02/T07/T10 等单跑脚本） |
| `scripts/hub/` | 枢纽数据 / 日志 / 空间文件的只读探针（`inspect_space.py`、`inspect_journal.py`、`peek_ceru_db.py` …）与端到端脚本（`end-to-end-ceru-check.mjs`） |
| `scripts/dev/` | 环境绕行与历史补丁：`run_tests_here.py`（本机受限环境下跑 pytest 的入口）、`check_sync*.py`、`dbg_*.py`、`apply_p05_*.py`、`repro_*.py` |

运行方式（Windows，必须用仓库自带 venv，系统 python 缺依赖会假报 module error）：

```powershell
& .\.venv\Scripts\python.exe scripts\engine\debug_safety.py
& .\.venv\Scripts\python.exe -m pytest tests\engine -q
```

约定：

1. 新脚本按 `engine` / `hub` / `dev` 三选一放，**不要丢在仓库根目录**；
2. 一次性脚本用完即删，不要留在 `src/`、`tests/` 里；
3. 需要"证明某个语义"的东西请写成 `tests/` 下的用例，而不是脚本。
