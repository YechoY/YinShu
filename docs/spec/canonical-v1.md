# 歌单同步统一格式规范 · `canonical-v1`

| 项 | 值 |
|---|---|
| 规范 ID | `canonical-v1` |
| 文档版本 | `schemaVersion: 1` |
| 状态 | **草案（Draft）** — 已按 Yecho 评审确定三项可选能力：多平台引用、品质偏好、可用性 |
| 适用范围 | 跨平台歌单同步服务。M1：栖弦（`cyshine-v1`）⇄ 澜音插件；预留 LX 等 |
| 权威性 | 本文件是唯一权威。适配器实现与本规范冲突时，以本规范为准 |
| 关键字 | **必须** MUST ／ **应当** SHOULD ／ **可以** MAY ／ **禁止** MUST NOT（RFC 2119 中文对照） |

规则编号可引用：`P`（原则）、`I`（身份）、`T`（时间）、`E`（扩展）、`S`（序列化）、`V`（校验）、`C`（兼容）、`A`（适配器）。

---

## 速览（30 秒）

```jsonc
{
  "schemaVersion": 1,
  "generatedAt": "2026-10-05T18:00:00Z",
  "playlists": [
    { "id": "pl_01J9Z8Q3K7W2M4X6B8C0D2E4F6", "name": "Yes",
      "createdAt": "2026-09-12T03:11:00Z", "updatedAt": "2026-10-05T17:58:12Z",
      "trackIds": ["tr_01J9Z8Q3K7W2M4X6B8C0D2E4F7"], "ext": {} }
  ],
  "tracks": [
    { "id": "tr_01J9Z8Q3K7W2M4X6B8C0D2E4F7",
      "identity": { "source": "tx", "songId": "003pgMEF0B7mWV" },
      "title": "…", "artists": [{ "name": "…" }], "durationMs": 207000,
      "qualities": [{ "code": "flac24bit", "sizeBytes": 42000000, "available": true }],
      "preferredQuality": "flac24bit",
      "platforms": { "tx": { "songId": "003pgMEF0B7mWV", "playable": true } },
      "addedAt": "…", "updatedAt": "…", "deletedAt": null, "ext": {} }
  ],
  "tombstones": [], "ext": {}
}
```

一句话记住六件事：**身份只有一对**（`source`+`songId`）、**一首歌可挂多平台**、**品质归一但保留原生码**、**删除是墓碑不是缺席**、**不认识的字段进 `ext` 且必须带回去**、**同一首歌只存一份**。

完整示例见 [`examples/canonical-v1.example.json`](examples/canonical-v1.example.json)，机器可校验的 Schema 见 [`canonical-v1.schema.json`](canonical-v1.schema.json)。

---

## 1. 目标与非目标

**目标**：为"多个音乐平台聚合播放器之间同步歌单"提供一个中立、可扩展、可读的数据格式，使任意数量的客户端能通过适配器接入同一份唯一事实，且**新增平台不需要修改已有平台的实现、不需要升版本**。

**非目标**（明确不进同步，见 §9）：音频直链、本地音乐文件路径、播放历史/统计、账号凭据、图片二进制、缓存。

---

## 2. 设计原则

| 编号 | 原则 | 说明 |
|---|---|---|
| `P1` | **身份唯一且不可演化** | 曲目身份 = `(source, songId)`；歌单身份 = 规范 `id`。任何字段变化都不得改变身份。 |
| `P2` | **分层** | 身份层／展示层／平台能力层／品质层／状态层／扩展层。新字段只加在某一层。 |
| `P3` | **未知必须保留** | 读取端遇到不认识的结构 **必须**保留，**禁止**丢弃或报错——这是前向兼容的唯一保障。 |
| `P4` | **规范化可逆** | 归一化（品质、时长、歌手）时 **必须**保留原始值（原生码、原文），保证能无损回写。 |
| `P5` | **一份只存一次** | 曲目集中在 `tracks` 池里，歌单只持有序 `trackIds` 引用。 |
| `P6` | **读宽松、写严格** | 读取端 **应当**容忍缺省与未知；写入端 **必须**输出合法文档。 |

---

## 3. 顶层结构

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schemaVersion` | integer | **必须** | 本规范为 `1` |
| `generatedAt` | string(ISO8601) | **必须** | 文档生成时间，UTC |
| `playlists` | `Playlist[]` | **必须** | 可以空数组；顺序即用户可见顺序 |
| `tracks` | `Track[]` | **必须** | 曲目池；可以空数组；顺序无业务语义 |
| `tombstones` | `Tombstone[]` | 可以 | 省略时视为 `[]`（服务端持久化用，渲染给客户端时可省略，见 `A5`） |
| `ext` | object | 可以 | 未定义字段的容器，见 §8 |

`S-T1`：顶层 **禁止**出现本表以外的字段——所有扩展 **必须**放进 `ext`。

---

## 4. 数据模型

### 4.1 `Playlist`

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| `id` | string | **必须** | 形如 `pl_<ULID>`；稳定、全局唯一（`I4`） |
| `name` | string | **必须** | trim 后非空，≤ 512 字符 |
| `description` | string \| null | 可以 | ≤ 4096 字符 |
| `coverUrl` | string \| null | 可以 | ≤ 2048 字符 |
| `createdAt` | string(ISO8601) | **必须** | UTC |
| `updatedAt` | string(ISO8601) | **必须** | UTC |
| `deletedAt` | string(ISO8601) \| null | 可以 | 非空 = 墓碑（`T2`） |
| `trackIds` | string[] | **必须** | **有序**；元素在 `tracks[]` 中 **必须**存在；**禁止**重复 |
| `platforms` | object | 可以 | `{ "<平台code>": { "nativeId": string, "nativeName": string, "ext": object } }` |
| `ownerRef` | object | 可以 | 来源歌单：`{ "source": "wy", "id": "2829…", "name": "…", "creator": "…" }` |
| `ext` | object | 可以 | 平台/客户端专有数据 |

### 4.2 `Track`

| 分组 | 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|---|
| 身份 | `id` | string | **必须** | **记录键**，形如 `tr_<ULID>`；仅用于 `trackIds` 引用与操作寻址，**不是身份**（身份见下一行） |
| 身份 | `identity` | object | **必须** | `{ "source": "<平台code>", "songId": "<裸 id>" }`，见 `I1`–`I2`；**这才是身份键** |
| 展示 | `title` | string | **必须** | trim 后非空，≤ 4096 |
| 展示 | `artists` | `{name,id?}[]` | **必须** | **必须**是数组（单歌手也是数组）；`name` 非空 |
| 展示 | `album` | `{name,id?}` \| null | 可以 | — |
| 展示 | `durationMs` | integer \| null | 可以 | 毫秒，≥ 0；**禁止**字符串化（`S4`） |
| 展示 | `artworkUrl` | string \| null | 可以 | ≤ 2048 |
| 展示 | `releaseDate` | string \| null | 可以 | `YYYY-MM-DD` |
| 品质 | `qualities` | `Quality[]` | 可以 | **应当**按优劣降序（`Q1`） |
| 品质 | `preferredQuality` | string \| null | 可以 | 用户偏好；**不参与**合并判定 |
| 平台 | `platforms` | object | 可以 | `{ "<平台code>": PlatformRef }`，见 4.3；**一首歌可以有多个** |
| 状态 | `addedAt` | string(ISO8601) | **必须** | UTC |
| 状态 | `updatedAt` | string(ISO8601) | **必须** | UTC |
| 状态 | `deletedAt` | string(ISO8601) \| null | 可以 | 非空 = 墓碑 |
| 状态 | `availability` | object | 可以 | 见 4.5；**不参与**合并判定 |
| 扩展 | `ext` | object | 可以 | — |

### 4.3 `PlatformRef`（该曲目在某平台上的可用引用）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `songId` | string | **必须** | 该平台的**裸** id（与 `identity.songId` 可能不同：同一首歌在不同平台 id 不同） |
| `mediaMid` | string | 可以 | 平台专有（如 QQ 音乐的 media_mid） |
| `albumId` | string | 可以 | 平台专有 |
| `qualitys` | `{nativeCode, code?, sizeBytes?}[]` | 可以 | **原生码必须保留**（`P4`）；`code` 为归一码 |
| `playable` | boolean | 可以 | 省略视为 `true` |
| `unavailableReason` | string | 可以 | 如 `"版权下架"`、`"需要会员"` |
| `ext` | object | 可以 | 平台专有字段的落点（`E5`） |

> `platforms` 里 **应当**包含 `identity.source` 对应的那一项；其他平台的引用是"同一首歌的另一条可达路径"，用于跨端换源播放。

### 4.4 `Quality`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `code` | string | **必须** | 归一码，见 §5.2；未知码 **必须**原样保留（`E-QUAL`） |
| `sizeBytes` | integer | 可以 | 体积，仅参考，**不参与**合并判定 |
| `available` | boolean | 可以 | 省略视为 `true` |
| `ext` | object | 可以 | 若已归一到近似码，原始码记在 `ext.rawCode` |

### 4.5 `Availability`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `state` | `"ok"` \| `"unavailable"` \| `"unknown"` | **必须** | — |
| `reason` | string \| null | 可以 | 人类可读原因 |
| `checkedAt` | string(ISO8601) \| null | 可以 | 上次探测时间 |

`A-AVAIL`：`availability` 与 `platforms.*.playable` **仅用于展示与提示**，**禁止**用作删除判定依据（判定只看身份与墓碑）。

### 4.6 `Tombstone`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `element` | `"track"` \| `"playlist"` | **必须** | — |
| `id` | string | **必须** | 被删元素的规范 id |
| `deletedAt` | string(ISO8601) | **必须** | UTC |
| `device` | string | 可以 | 发起删除的客户端标识 |
| `reason` | string | 可以 | `"user"` / `"merge"` 等 |

### 4.7 `ext`（扩展容器）

一个 JSON 对象。键 **应当**用命名空间（`"<平台code>.<字段>"` 或反向域名），值 **必须**可 JSON 序列化，整块 **应当** < 64 KiB。

---

## 5. 代码表

### 5.1 平台代码 `source`

| code | 平台 | 依据 |
|---|---|---|
| `tx` | QQ音乐 | 栖弦 `lib/core/models/enums.dart`；澜音 `src/renderer/src/utils/sourceName.ts`（两边一致） |
| `wy` | 网易云 | 同上 |
| `kw` | 酷我 | 同上 |
| `kg` | 酷狗 | 同上 |
| `mg` | 咪咕 | 同上 |

- **`E-SRC-1`**：code **必须**小写 ASCII，`^[a-z][a-z0-9_]{0,31}$`。
- **`E-SRC-2`**：**未知 code 必须原样保留**（进 `identity.source` / `platforms` 键即可，不需要改规范）。新增音源 = 新增一个 code，**不升版本**。
- **`E-SRC-3`**：展示名（QQ音乐/网易云/…）属于 UI 层，**禁止**写进 `identity.source`；需要时放 `platforms.<code>.ext.displayName`。

### 5.2 品质代码 `code`（归一化）

排序（优 → 劣，与栖弦 `lib/core/models/enums.dart:107-115` 的 server sort order 一致）：

```
master > atmos_plus > atmos > hires > flac24bit > flac > 320k > 192k > 128k
```

| 归一 code | 中文 | 各平台原生码（示例；栖弦内部实现可直接照抄） |
|---|---|---|
| `master` | 母带 | 酷我 `20900`；酷狗 `viper_clear`；网易云 `jm` |
| `atmos_plus` | Atmos+ | 酷我 `20501`；网易云 `sk` |
| `atmos` | 杜比全景声 | 酷我 `20201`；酷狗 `viper_atmos`；网易云 `je` |
| `hires` | Hi-Res | 酷我 `4000`；酷狗 `high`；网易云 `hr`；QQ `size_hires` |
| `flac24bit` | 24-bit FLAC | 咪咕 `ZQ`/`ZQ24`；QQ `size_hires`；**LX `flac32bit` 归一到这里** |
| `flac` | 无损 | — |
| `320k` / `192k` / `128k` | — | 兜底常态 |

- **`Q1`**：`qualities` **应当**按上表排序输出。
- **`E-QUAL`**：归一映射表 **必须**放在适配器里；无法映射的码 **必须**原样保留（`code` 用原值或 `ext.rawCode` 记录），**禁止**静默降级成 `128k`（栖弦 `enums.dart:59-60` 亦如此规定）。
- **`Q2`**：`preferredQuality` 若非空，**应当**是该曲目 `qualities[].code` 之一。

参考实现（照抄来源）：栖弦 `lib/core/sdk/internal/tx_quality.dart`、`wy_quality.dart`、`kg_quality.dart`、`kw_quality.dart`、`lib/core/sdk/playlist_adapters/mg_playlist_adapter.dart`、`lib/features/playlists/lx_playlist_import.dart`。

---

## 6. 身份与唯一性

| 编号 | 规则 |
|---|---|
| `I1` | 曲目身份 = `identity.source` + `identity.songId`；身份键字符串为 `<source>:<songId>`。 |
| `I2` | `identity.songId` **必须**是平台裸 id：**禁止**带 `<source>_` 前缀、**禁止**带 `ceru-song:` 之类的包装。渲染到客户端时才拼装（如栖弦要求 `musicId = "<source>_<songId>"`）。 |
| `I3` | `tracks[]` 内身份键 **必须**唯一；重复即非法文档。**注意**：唯一性约束作用在 `tracks[]` 上——**多个歌单的 `trackIds` 引用同一条曲目记录是合法的**（`P5` 的"只存一份"正是指这一点）。跨音源同名 id **必须**视为两首歌（`wy:1234567` ≠ `kw:1234567`）。**禁止**用歌名+歌手作为身份。**墓碑键**：曲目用 `(source, songId)` 的规范化字符串、歌单用歌单 `id`；同一元素出现多条墓碑时按"最晚一条"处理，不产生多份墓碑（评审 E4）。 |
| `I4` | 歌单身份 = 规范 `id`；跨端配对 **必须**通过 `platforms.<code>.nativeId` 映射表（服务端持久化）。名字 **仅**用于首次配对。 |
| `I5` | 身份字段赋值后 **禁止**变更：改歌名、换封面、改归属歌单都不得改变 `id` / `identity`。 |
| `I6` | 本地音乐/文件曲目（无平台 id）**禁止**作为一等元素进入规范文档（它们无法被跨端寻址，见 §9）。**但必须原样携带**：适配器把它们放进 `ext.<client>.opaque.tracks`（不参与合并判定、不参与去重、**不参与跨设备交付**——按 `client_key` 分桶存储与渲染，2026-10-06 D23）、不参与 `trackIds`），渲染时**原样回填**。`ext.*.opaque` **必须计入 `view_hash`**（否则"内容未变"会被误判为"已变"，导致无谓写回），但**不参与**身份键与冲突判定（评审 E5；分桶后各客户端哈希互不影响，语义上不再需要"排除 opaque"，口径见 D23）。理由：客户端（如栖弦 `applyFromSync`）对"缺席"按删除处理，若渲染时丢掉这些曲目，用户的本地音乐会被服务端**间接删除**。 |

---

## 7. 时间戳与删除语义

| 编号 | 规则 |
|---|---|
| `T1` | 所有时间 **必须**是 ISO8601 UTC（`Z` 结尾），秒级或毫秒级。读取端 **应当**接受任意时区偏移并归一为 UTC。 |
| `T2` | 删除 **必须**用 `deletedAt` 墓碑表达，**禁止**用"缺席"表达，**禁止**物理删除记录。 |
| `T3` | 墓碑的保留**不以时间为判据**（D21）：只有在**删除发生时已注册的所有客户端都已在成功交付中见过它**（确认水位）之后才可以 GC；对**无删除能力的客户端（`can_delete=false`，如澜音插件）**"交付即确认"不成立——它删不掉本地副本，必须以其**提交内容里不再携带该条目**为确认（否则墓碑会被它的下一次 GET 提前回收，删除被推回，复检 S11/S15）；>180 天未出现的身份标记为 `retired` 并排除在 GC 条件之外。若运营上仍需一个兜底上界，其语义是"**完全没有确认时的最长保留**"，取**无限（永不按时间 GC）**；**绝不设短 TTL**（30 天与"离线 90 天/6 个月"用例直接冲突）。 |
| `T4` | 对**未知元素**的墓碑 **也必须**保留——否则一个长期离线的旧客户端回来时会把已删元素复活。 |
| `T5` | 顺序（`playlists[]` 顺序、`trackIds[]` 顺序）**不参与**合并判定：本阶段不做顺序同步（见 `docs/01` §1.5）。渲染时按规范的稳定顺序输出；客户端的顺序变化**不视为**一端变更，**也不得进入 `view_hash`**（否则两侧顺序差异会导致 ping-pong 写回，评审 E3）。 |
| `T6` | 各端墙上时钟**不可信**：合并判定 **禁止**直接比较两端的本地时间戳来决定胜负（同步服务用"每客户端基线差分"，见服务规范）。时间戳只用于展示与冲突记录。 |

---

## 8. 扩展机制

| 编号 | 规则 |
|---|---|
| `E1` | 读取端遇到规范未定义的字段 **必须**保留（放入 `ext` 或原样携带），**禁止**报错或丢弃（`P3`）。 |
| `E2` | 写入端 **必须**只输出规范定义的字段 + `ext`。 |
| `E3` | `ext` 键 **应当**命名空间化（`<平台code>.<字段>`）；值 **必须**可 JSON 序列化；整块 **应当** < 64 KiB。 |
| `E4` | 加法演进不升版本：新增平台 code、新增可选字段、新增品质码、新增 `ext` 键。 |
| `E5` | 平台专有数据 **必须**放 `platforms.<code>` 或 `ext.<code>.…`；**禁止**污染通用字段。 |
| `E6` | 破坏性演进（改身份、改结构、改语义）**必须**升 `schemaVersion`，并同时提供新旧两版适配器（`C3`）。 |

---

## 9. 明确不进同步的数据

音频播放直链（会过期）、播放历史/次数/最近播放、缓存与图片二进制、账号凭据与 Cookie、客户端界面状态（主题等由各端自己管；如需跨端同步 **应当**走独立 section/命名空间）。

**例外——必须原样携带（`I6`）**：本地文件曲目与 `file://` 引用、无平台 id 的兜底曲目、以及任何适配器无法表达的客户端私有内容，**必须**放进 `ext.<client>.opaque.*` 并在渲染时原样回填。它们**不参与**合并判定、去重与 `trackIds`，**不参与跨设备交付**（按 `client_key` 分桶存储与渲染，2026-10-06 D23），且**不允许**在渲染时丢失——否则客户端会按"缺席即删除"把它们清掉。

---

## 10. 序列化与校验

| 编号 | 规则 |
|---|---|
| `S1` | UTF-8、无 BOM、合法 JSON（RFC 8259）：**禁止** NaN/Infinity、注释、尾逗号。 |
| `S2` | 字段名 camelCase ASCII；平台与品质 code 小写（允许 `_`）。 |
| `S3` | `null` 与"缺省"语义不同：字段无值 **应当**写 `null`；不适用可省略。 |
| `S4` | 数字 **必须**是 JSON number（**禁止** `"207000"`）；整数无前导零。 |
| `S5` | 字符串长度上限见各字段表；超长 **必须**在适配器层截断/拒绝，**禁止**产出非法文档。 |
| `S6` | `trackIds` 指向的曲目 **必须**存在于 `tracks[]`。 |
| `S7` | 文档 **禁止**包含凭据、直链、手机号等敏感信息。 |
| `S8` | 单文档 **应当** < 1 MiB；更大时 **应当**分片或改多文件结构（走适配器，`E6` 之外的结构演进）。 |

校验项 `V1`–`V12`（由 [`validate-canonical-v1.py`](validate-canonical-v1.py) 实现）：必填字段、类型、`identity` 合法性、身份键唯一、`trackIds` 引用存在且不重复、时间格式、`durationMs` 为整数、`availability.state` 枚举、`platforms.*.songId` 非空、顶层无未知字段、`ext` 为对象、品质排序（警告级）。

---

## 11. 兼容性与版本演进

| 编号 | 规则 |
|---|---|
| `C1` | `schemaVersion` 是整数主版本号；同一主版本内，实现 **可以**只支持部分可选字段。 |
| `C2` | 主版本内 **必须**保证前向/后向兼容：老实现遇到新字段保留（`E1`），新实现遇到缺失字段给默认值。 |
| `C3` | 破坏性变更 **必须**升主版本，并在迁移期由服务端**同时**渲染两版（`cyshine-v1` 与 `cyshine-v2`…），客户端逐个升级。 |
| `C4` | 规范改动 **必须**同时更新：本文件、`canonical-v1.schema.json`、示例、校验器、变更记录。 |
| `C5` | 适配器与规范冲突时以规范为准；适配器的取舍 **必须**以注释形式登记（含原因）。 |

---

## 12. 适配器契约

适配器把"某客户端的原生格式"翻译成规范模型，反之亦然。适配器 **禁止**实现合并逻辑——合并只发生在服务端（服务规范另述）。

| 编号 | 要求 |
|---|---|
| `A1` | `parse(payload, ctx) → CanonicalDoc`：**必须**保真（未知字段进 `ext`），**必须**产出合法文档。 |
| `A2` | `render(doc, ctx) → payload`：**必须**满足该客户端的硬要求（见 `A3`），且 **必须**是确定性输出（同输入同输出）。 |
| `A3` | **往返等价**：`parse(render(doc)) ≡ doc`（忽略无语义差异）。任何 lossy 行为 **必须**在 `LOSSY.md` 或代码注释中显式登记并给出理由。 |
| `A4` | 身份映射 **必须**用 `(source, songId)`；**禁止**按歌名判定"是同一首歌"。 |
| `A5` | 删除表达适配客户端能力：**能删的**客户端渲染为删除；**不能删的**（如澜音缺 `removeTracks`）**必须**渲染为"待清理清单/提示"，**禁止**静默忽略。 |
| `A6` | 适配器 **必须**声明能力：`supports_delete`、`supports_create_playlist`、`multi_file`、`needs_propfind`、`max_doc_bytes`，供服务端决定渲染策略。 |
| `A7` | 本地格式转换 **必须**集中在适配器：时长（`"03:27"` ⇄ `207000`）、歌手（字符串 ⇄ 数组）、品质（原生码 ⇄ 归一码）、封面字段名。 |

**`cyshine-v1` 渲染硬要求**（栖弦会直接报错或静默错乱，逐条对应 §10）：

1. 三个 section **必须**齐全：`playlists` / `appearance` / `musicSources`；
2. `appearance.data.themeSeedArgb` **必须**是 JSON 整数；
3. `musicId` **必须**形如 `<source>_<songId>`，且 `musicInfo.meta.songId` 是裸 id；
4. 时长 **必须** `mm:ss` 且零填充（`03:27`，不是 `3:27`）；
5. `metadata.artists` **必须**是字符串数组；`qualities` ≤ 128 项、每项字段 ≤ 128 字符；
6. 歌单 **必须**含 `version/id/name/tracks/createdAt/updatedAt`。

---

## 13. 性能与规模

**量级假设**（来自真实数据）：客户端 2–5 个；歌单 3–20 个；曲目 10²–10⁴ 首；单客户端文档 10²–10³ KB
（实测：云端 `sync-v1.json` 245 首 = 237 KB；澜音本地库 267 行）。同步频率：分钟级轮询。

这不是过早优化——有两个已经踩过的真实数字：AList 在 **~108 KB** 处把 PUT 写截断过；这版格式把同一首歌在各歌单
重复存了一遍（同一首歌进 3 个歌单 = 存 3 份）。

| 编号 | 规则 |
|---|---|
| `PF1` | 单文档 **应当** < 1 MiB（≈5000 首去重曲目）；超过 **必须**分片（每歌单一个文件 / 分页），分片对客户端透明，由适配器决定。 |
| `PF2` | **必须**用曲目池去重（`P5`）：同一首歌在 N 个歌单只出现一次。 |
| `PF3` | 传输 **必须**支持 gzip。JSON 文本通常 5–8× 压缩（237 KB → ~35 KB）——移动端流量最大的一笔。 |
| `PF4` | 条件读：**不得**对"只接受 2xx"的客户端（实测：栖弦 `webdav_client.dart:68-69/91` 把 `304` 判为失败）返回 `304`——内容未变时 **必须**返回 `200` + 相同正文与相同 `ETag`。对确认支持条件读的客户端，适配器 **可以**启用 `If-None-Match` → `304`（按方言配置）。 |
| `PF5` | 变更检测与 ETag **必须**用规范化哈希（对象键排序、数组按规范顺序），**禁止**逐字符比较或靠时间戳猜。 |
| `PF6` | 合并 **必须** O(n+m)（哈希表差分）；**禁止** O(n²) 的名字两两匹配（旧插件按歌单名线性匹配的教训）。 |
| `PF7` | `ext` **应当** < 64 KiB/对象；**禁止**塞入日志、二进制、直链。 |
| `PF8` | 字段 **禁止**冗余：同一个 id 只存一处；时间戳只保留 `addedAt`/`updatedAt`/`deletedAt` 三个语义。 |

> 结构上的性能红利：`identity` 只有一对字段、`platforms` 用 map、`qualities` 数量小、时间戳固定三个
> ⇒ 解析与哈希都便宜；曲目池去重让体积随"歌单数"而不是"歌单×曲目"增长。

---

## 14. 完整示例

见 [`examples/canonical-v1.example.json`](examples/canonical-v1.example.json)：含 2 个歌单、4 首曲目（其中 1 首同时在 `tx` 与 `wy` 可用、1 首 `unavailable`、1 首带未知品质码）、1 条墓碑、以及命名空间化的 `ext`。

校验：

```pwsh
python spec\validate-canonical-v1.py spec\examples\canonical-v1.example.json
```

---

## 15. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| 1 | 2026-10-05 | 初稿。确定：曲目身份 `(source, songId)`、多平台引用 `platforms`、品质归一 + 原生码保留、`preferredQuality` 偏好、`availability` 提示（不参与判定）、墓碑删除语义、`ext` 前向兼容机制、适配器契约与 `cyshine-v1` 渲染硬要求；补充 §13 性能与规模（`PF1`–`PF8`）。 |

### 待定

1. 规范 id 是否强制 ULID（当前"**应当**"）：需要一个跨语言可用的 26 字符实现。
2. `appearance` / `musicSources` 这类"客户端自己的 section"是否也纳入本规范（当前由服务端整段 LWW 存储，不进规范模型）。
3. 多文件/分片结构（`S8`）是否定义为 `canonical-v1-multi`，还是留给适配器自行决定。
