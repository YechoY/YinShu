"""hub：同步合并枢纽（音枢 Yinshu）。

复用 engine/ 的合并引擎，包成 HTTP 端点：
  - Basic 认证，每账号一个同步空间
  - GET/PUT /CyShineMusic/sync-v1.json（栖弦 cyshine-v1）
  - GET/PUT /ceru/sync-v1.json（澜音插件 ceru-plugin）
  - GET/PUT /lx-x/playlists.json（洛雪 lx-x；目录名任意，按文件名兜底）
  - 适配器包（adapters/，pkgutil 自动发现注册）+ 元数据池 + 持久化（store）

运行：uv run python -m hub.run_fastapi（在项目根目录；FastAPI 是唯一入口，
旧 stdlib 入口 server.py / run.py 已归档到 docs/archive/legacy-http-entry/）
"""
