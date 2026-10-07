# 归档：旧版 stdlib HTTP 入口（已停用，不再被任何代码引用）

这里是从 `src/hub/` 移出来的两个历史文件：

| 文件 | 原路径 | 作用 |
|---|---|---|
| `server.py` | `src/hub/server.py` | 基于标准库 `http.server` 的 `make_server()` |
| `run.py` | `src/hub/run.py` | 旧 CLI 启动入口（`python -m hub.run`） |

## 为什么移出来

1. **双入口语义不一致**：现行入口是 FastAPI 版 `src/hub/api.py` + `src/hub/run_fastapi.py`
   （README、docs/ 与前端都按它写），而 `run.py:37-55` 仍保留**明文密码 `admin123` 回退**，
   与 `api.py` 已改的 pbkdf2 校验不是一套。
2. **测试测的是没人用的路径**：`tests/hub/test_hub.py` 原来 `from hub.server import make_server`，
   8 条冒烟全部跑在旧入口上，真正的线上入口没有任何 HTTP 级覆盖（审查报告 P1-6）。
   现已迁移：`tests/hub/test_hub.py` 直接驱动 FastAPI（端口 `18751+pid%100`），共享工具在
   `tests/hub/helpers.py`。

## 为什么是"归档"而不是"删除"

本项目**不是 git 仓库**，删掉就找不回来了。归档同样让 `src/` 只剩一个入口，需要时随时取回。

## 如果要复活旧入口

不要直接 `Move-Item` 回 `src/hub/`：`server.py` 的配置/认证语义已落后于 `api.py`
（明文密码回退、缺 `pendingRestores`/`pendingDeletions` 端点、无 404 无基线分支）。
复活前先对齐 `api.py` 的行为，或直接改用 FastAPI 入口。

## 现行入口

```bash
uv run python -m hub.run_fastapi                                  # 默认 127.0.0.1:8000
uv run python -m hub.run_fastapi --host 0.0.0.0 --port 8000       # 局域网（手机栖弦）
```
