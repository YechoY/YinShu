# 跨平台歌单同步枢纽（playlist-sync-hub）

> 一句话：做一个**通用 WebDAV 歌单同步枢纽**——任何支持 WebDAV 的音乐客户端，只要在同步设置里填 **一个 URL + 自己的账号密码**，就能与同一账号下的其它客户端（**无论彼此数据格式是否相同**）双向同步歌单：各自新增会合并，各自删除会传播，分钟级生效。

- 项目状态：**实现中**（引擎 PoC ✅ → hub 同步服务 ✅ → 澜音插件改造 ✅ → FastAPI 可视化界面 ✅ → **适配器插件化（加客户端只加一个文件）✅** → **洛雪 `lx-x` 适配器 ✅** → 真机联调（栖弦 / 洛雪）⬜）
- 存放位置：本仓库根目录（clone 后即可用，所有路径均相对项目根动态推导）
- 前身：`ceru-cyshine-webdav` 插件（澜音 ↔ 栖弦 点对点同步）。结论：**点对点做不到"真正同步"，必须把"翻译 + 合并"收进服务端。**

---

## 一页速览

| 问题 | 现状 | 本方案 |
|---|---|---|
| 各播放器 WebDAV 同步各写各的格式 | 栖弦 `cyshine-v1`、澜音插件自造、其它客户端又是另一套 | 服务端**翻译**：内部统一成 `canonical-v1`，对外仍按各客户端格式收发 |
| 单文件里只有"最终状态" | 无法区分"对端删了"和"对端还没同步" | 服务端为**每个客户端保存基线**，做三路合并 |
| 客户端自己的合并逻辑会整节替换 | 两端各有增删时必然丢一边 | 合并只在服务端做一次，客户端**零代码改动** |
| 想接更多平台 | 每加一个平台要改两端代码 | 加一个**适配器**；不认识的格式先**隔离降级**，绝不猜错 |
| 多用户 | 一人一套部署 | **一个公共 URL + 账号密码**；空间与成员体系（管理员统一管理，普通账号凭邀请码加入共享空间） |

```text
栖弦(Android) ─┐
洛雪(LX, lx-x)─┤
澜音插件(可选)─┼─→  【同步枢纽】WebDAV 门面 ─→ 适配器 ─→ canonical 模型 ─→ 合并引擎 ─→ 存储(基线/墓碑)
将来任意客户端─┘        ↑ 认证：URL + 账号密码                ↑ 每个客户端一份基线（含设备维度）
```

---

## 目录地图（src 布局 · 工程规范）

```
playlist-sync-hub/                 # uv 项目根（pyproject.toml / uv.lock / .venv）
├── README.md
├── docs/                          # 文档统一（**先看 docs/README.md 这一页导览**）
│   ├── README.md                  #   文档导览：哪份权威、哪份是历史
│   ├── 01~14 *.md                 #   权威文档，编号即阅读顺序（09 是全部决策记录；13 接入新客户端、14 洛雪）
│   ├── spec/                      #   canonical-v1 规范 + Schema + 校验器 + 样例
│   ├── notes/                     #   历史留档：各轮修改文档 / 复核报告（**非现状**）
│   └── archive/                   #   已废弃方案（方案-v0 / 前身 / 设计说明书 v1）
├── src/                           # 源码（打包：engine + hub 两个包）
│   ├── engine/                    #   合并引擎（canonical / engine / SyncSpace）
│   └── hub/                       #   同步服务（api / store / adapters / run_fastapi）
├── tests/                         # 测试与源码分离
│   ├── engine/                    #   58 条合并引擎用例（33 合并 + 10 故障注入 + 15 多设备）
│   ├── hub/                       #   78 条服务测试（7 适配器 + 8 洛雪 + 18 HTTP 验收 + 7 设备路由 + 9 冒烟 + 29 空间与成员）
│   └── data/                      #   适配器真机样本（只读夹具，见 tests/data/README.md）
├── scripts/                       # 脚本（与源码、测试分离）
│   ├── engine/ hub/               #   诊断/调试脚本（读空间、看 journal、端到端探针）
│   └── dev/                       #   开发工具：run_tests_here.py、check_sync*.py 等
├── webui/                         # 前端 Vue 3 工程（Vite 构建）
│   ├── src/                       #   App.vue + components/（Login/Sidebar/TrackPanel/StatusBar）+ lib/
│   ├── index.html  vite.config.js  package.json
│   └── dist/                      #   构建产物（FastAPI 托管；改动前端后 npm run build）
├── data/spaces/                   # 运行数据（space__main.pkl + _index.json，gitignore）
├── backups/                       # config.json 的快照备份（含密码，gitignore）
├── tmp/                           # 测试临时空间（gitignore；logs/ 保留历次测试输出）
├── plugins/
│   └── ceru-webdav-sync/          # 澜音插件「webdav歌单同步」（id=ceru.webdav-sync）
│       ├── src/                   #   index.js（宿主编排）+ sync-core.js（纯逻辑）
│       ├── test/                  #   12 + 15 条测试
│       ├── ui/  dist/  release/   #   设置页 / 构建产物 / 交付版本
│       └──（本目录是**独立 git 仓库**的开发副本，不并入本仓库；正式仓库在工作区根 `ceru-cyshine-webdav/`）
└── .gitignore                     # 忽略 .venv / node_modules / __pycache__ / tmp / dist / data/spaces / config.json …
```

## 运行方式

依赖统一由 **uv** 管理（项目根目录）：

```bash
# 启动同步服务 + 可视化界面（FastAPI，默认 127.0.0.1:8000）
# 账号：有 config.json 时以其中账号为准；没有时默认 admin/admin123（仅本机测试）
uv run python -m hub.run_fastapi

# 局域网联调（手机栖弦要连）
uv run python -m hub.run_fastapi --host 0.0.0.0 --port 8000

# 全量测试（136 条 = 引擎 58 + hub 78；含多设备 M1/M2/M6/M7/M8、P0.5 待确认删除卡、
# 多账户共用歌单 §1 opaque 分桶 / §2 确认卡去重·自动关闭·归属权限 / §3 空间与成员 + 配额 +
# 复核 P0-1 读鉴权 / P0-2 邀请码不重复消耗·viewer 禁列码 / P1-3 建号空间校验 / P1-4 审计过滤次序 /
# P1-5 改名同步 invited_by / P1-6 不变量修复告警 / 第三轮 空间重命名 5 条 + peek_view 纯读 1 条 +
# 第四轮 删除免二次确认 13 条：毫秒时间戳压过提交 / 交付即确认+回推撤销 / 冷静期压制 /
# 澜音缺席不删 / 可信批量直删 / C2 展示名冲突 / C3 me/space 展示名 / C6 挑战头分离 /
# 端到端改名+删除恢复无卡）
uv run pytest

# 前端开发/构建（webui/ 内）
cd webui && npm run dev      # 开发热更新
cd webui && npm run build    # 构建到 dist/（FastAPI 自动托管）

# 插件测试（35 条 = sync-core 12 + 集成 23）
cd plugins/ceru-webdav-sync && npm test
```

浏览器打开 `http://127.0.0.1:8000/` 即 AList 式可视化界面（浅色毛玻璃、5 秒自动刷新、本地时间）。

客户端填入（用户只填**一个 URL + 账号密码**；多设备的设备身份由客户端自己发 `X-Hub-Device` 头，用户不用拼地址）：
- 栖弦：`http://<host>:<port>/CyShineMusic/sync-v1.json`
- 澜音插件：`http://<host>:<port>/ceru/sync-v1.json`
- 洛雪（lx-x）：`http://<host>:<port>/lx-x/playlists.json`（目录名写 `LX_Muisc` 也行——文件名兜底；`settings.json` / `user_apis.json` 整文件旁路）
- 认证：Basic（用户名/密码，config.json 可配置账号与空间）

## 阅读顺序（新人 15 分钟版）

0. [`docs/README.md`](docs/README.md) → 一眼看清哪份文档是权威、哪份只是历史留档
1. 本 README 的"一页速览"
2. `docs/01-需求与验收.md` → 知道要满足什么
3. `docs/03-总体架构.md` → 知道整体长什么样
4. `docs/05-合并引擎.md` → 知道唯一会丢数据的地方怎么保证不丢
5. `docs/07-路线图与验收用例.md` → 知道怎么一步步做出来并验收

## 关键决策速查

| 编号 | 决策 |
|---|---|
| D1 | 手机端"分钟级"= 打开 App / 切回前台即同步（客户端无定时器，不额外耗电） |
| D2 | 与 AList 并存，不替换（AList 继续服务别的用途） |
| D3 | 第一阶段客户端**零代码改动**，只改同步地址 |
| D4 | 每个客户端一份基线做差分，**不升级客户端文件格式** |
| D5 | 统一格式（`canonical-v1`）只存在于服务端内部 |
| D6 | 曲目身份 = `source + songId`；歌单身份 = 服务端映射表（不靠名字） |
| D7 | 删除用**墓碑**表达；同一元素"一加一删"时**删除优先** |
| D8 | 任何客户端**首次接入只增不删** |
| D10 | 接入 = **一个 URL + 一组账号密码**（HTTP Basic over HTTPS），空间即账号数据隔离边界 |
| D11 | 未识别方言**隔离存储、不跨格式合并**，记录发现事件，事后补适配器自动并入 |
| D12 | 本项目独立成目录 `playlist-sync-hub`（engine / hub / plugins 三层） |
| D15 | **甲方案**：枢纽自己做 WebDAV 门面（客户端只认它） |
| D16 | 先跑在本机 Windows，但按"以后能搬到云服务器"实现 |
| D17 | 多用户隔离真做（空间与成员体系 v3：管理员/成员/只读三角色，普通账号凭邀请码加入共享空间，账号配额） |
| D18 | 客户端范围 = **栖弦 + 澜音插件** |
| D20 | **墓碑压制**：换设备/重装后，已删条目不复活，转"待确认恢复"卡 |
| D21 | **墓碑不以时间为判据**：抑制看**身份可信度**，GC 看**确认水位** + 身份退休 180 天 |
| D22 | 第一版范围 = **删除自动生效**；确认卡只出现在异常情形（大比例删除 / 无法归因的提交） |

完整决策与理由见 [`docs/09-决策记录.md`](docs/09-决策记录.md)。

## 下一步

- [x] 引擎 PoC（38 条用例全绿：三路合并 / 墓碑 / 安全阀 / 排序语义）
- [x] hub 同步服务（Basic 认证 + 方言路由 + 持久化 + 命令行日志）
- [x] 澜音插件改造「webdav歌单同步」（手动导入弹窗 / 封面 / 品质 / 排序 / 详细日志）
- [x] **工程规范重构**：src 布局、测试独立 tests/、uv 统一管理、FastAPI 重写
- [x] **AList 式可视化界面**（浅色毛玻璃、本地时间、5 秒自动刷新、搜索/品质筛选）
- [x] **入口收敛**：旧 stdlib 入口归档（`docs/archive/legacy-http-entry/`），全部测试跑在 FastAPI 路径上
- [x] **多账户共用歌单**：opaque 按客户端分桶隔离；确认卡去重/自动关闭/归属权限（403）；空间管理（/api/spaces、改空间路由、删账号默认保留孤儿空间数据、禁静默新建）
- [x] **空间重命名**（展示名可变、id 稳定、客户端零影响）+ 全局策略开关（允许成员生成邀请码）+ 浏览器原生认证弹窗根因修复（WebDAV 挑战头拆分 + 前端 401 站内化）
- [x] **删除免二次确认（第四轮）**：同空间编辑者删除/恢复直接生效、全程无确认卡——枢纽交付时间戳毫秒单调压过客户端提交（根因 A）；墓碑确认 = 交付删除后视图 ∧ 未回推（交付即 ack、回推撤销 ack）；恢复判据去掉"仅删除发起者本人"（看过删除结果 + 冷静期后重加 = 显式恢复）；安全阀仅对不可信端挂起（可信栖弦批量直删）；澜音（can_delete=False）缺席只记录不删除
- [x] **适配器插件化（第六轮）**：适配器从单文件手写路由表重构为 `src/hub/adapters/` 包 + `pkgutil` 自动发现注册表；**新增客户端 = 新增一个文件**，`api.py` / `store.py` / `engine.py` 零改动（验收：`rg "cyshine-v1|ceru-plugin" src/hub/api.py src/hub/store.py` 0 命中）
- [x] **洛雪 `lx-x` 接入**：整份 `playlists.json` 三路合并、`defaultList/loveList/tempList` 按客户端分桶、`settings.json` / `user_apis.json` 整文件旁路、最小 207 `PROPFIND`（见 `docs/14-洛雪接入.md`）
- [x] **设备身份**：`X-Hub-Device` 头 / `/d/{设备}` 路径二选一（头优先），同一账号多台设备各自独立基线、互不误删
- [ ] 栖弦真机接入验证（局域网联调需 hub 监听 0.0.0.0）
- [ ] 栖弦端补设备头（`docs/notes/客户端对接说明.md` §1；不补也能用，靠 `/d/<设备名>` 兜底）
- [ ] 洛雪端补设备头（对接说明 §2.2；同上）
- [ ] 澜音插件重打包 `dist/` + `release/`（当前制品早于源码，未含设备头与"去待清理清单"改动）
