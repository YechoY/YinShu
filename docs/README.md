# 文档导览

本仓库文档只保留**与当前实现一致**的内容。历史设计稿、各轮评审与决策记录、过程笔记仅在本地留存（见 .gitignore），需要考古时按 git 历史找回。

## 文档清单

| 文件 | 内容 |
|---|---|
| [12-空间与成员体系](12-空间与成员体系.md) | 空间 / 成员 / 角色 / 邀请码 / 配额的设计 |
| [13-接入新客户端](13-接入新客户端.md) | 适配器插件化后，新增客户端只需加一个文件 |
| [14-洛雪接入](14-洛雪接入.md) | `lx-x` 方言：文件结构、身份规则、旁路边界 |
| [spec/](spec/README.md) | `canonical-v1` 对外契约：规范、JSON Schema、校验器、示例 |

> 合并引擎的当前规则见根目录 [README](../README.md) 的"架构原理"一节；
> 判定细节直接看 `src/engine/engine.py`（代码即文档）。

## 文档 ↔ 代码对应关系

| 想知道… | 看代码 |
|---|---|
| 合并/删除/时间戳怎么算 | `src/engine/engine.py`、`src/engine/canonical.py` |
| 各客户端格式怎么翻译 | `src/hub/adapters/`（`base.py` + 各方言文件） |
| HTTP / WebDAV 路由、账号空间 API | `src/hub/api.py` |
| 持久化、空间运行时 | `src/hub/store.py` |
| 前端界面 | `webui/src/` |
| 引擎用例 | `tests/engine/` |
| 服务/HTTP 用例 | `tests/hub/` |

## 常用命令

```bash
uv run yinshu                     # 启动（或用 run.ps1 / run.sh 一键脚本）
uv run pytest                     # 全量测试（逐模块跑更稳，见根 README）
cd webui && npm run build         # 前端构建到 webui/dist
```
