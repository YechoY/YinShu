# tests/data —— 适配器真机样本（只读夹具）

这些是**真实客户端产出的歌单文件**，只用于适配器测试。**不要修改它们**（改了就是伪造证据），也不要为了瘦身而裁剪。

| 文件 | 来源 | 用途 |
|---|---|---|
| `lx_identity.json` | 手工构造 | 洛雪身份规则边界样本（无前缀 id / `meta.id==0` / 未知音源 / 缺字段） |
| `lx_playlists_small.json` | 洛雪真机单歌单（235 首，即云盘 `lx-x-playlists.json`） | parse/render 外形、幂等往返 |
| `lx_playlists_full.json` | 洛雪真机全量（1386 首，即云盘 `LX_Muisc/playlists.json`，867 KB） | 真实分布统计（opaque 三列表 + userList）、大文件解析 |

引用方：`tests/hub/test_adapters_lx.py:52-53`。

> 为什么把 867 KB 的真机文件放进仓库：opaque 三列表（`defaultList` 722 / `loveList` 232 / `tempList` 301）的真实分布只能用全量样本验证；换成裁剪版就等于不再测试真实输入。
