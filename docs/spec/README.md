# `spec/` — 歌单同步统一格式规范

跨平台歌单同步服务的数据规范族。**这里定义"唯一事实"长什么样**；各客户端原生格式由适配器翻译过来。

| 文件 | 内容 |
|---|---|
| [`canonical-v1.md`](canonical-v1.md) | **主规范**：原则、字段表、代码表、身份规则、时间与删除语义、扩展机制、序列化与校验、兼容性、适配器契约、性能与规模 |
| [`canonical-v1.schema.json`](canonical-v1.schema.json) | JSON Schema（draft 2020-12），机器可校验 |
| [`examples/canonical-v1.example.json`](examples/canonical-v1.example.json) | 完整示例：多平台引用、未知品质码、不可用状态、墓碑、命名空间化 `ext` |
| [`validate-canonical-v1.py`](validate-canonical-v1.py) | 校验器（仅标准库），实现规范里的 `V1`–`V12` |

校验示例：

```pwsh
python spec\validate-canonical-v1.py spec\examples\canonical-v1.example.json
```

## 30 秒读懂

1. **身份只有一对**：曲目 = `identity.source` + `identity.songId`（`tx` + 裸 id）；歌单 = 规范 `id` + 各端 `nativeId` 映射。
2. **一首歌可挂多平台**：`platforms` 是 map，同一首歌在 QQ 和网易云都有就都留着。
3. **品质归一但保留原生码**：`qualities[].code` 用统一码，`platforms.<p>.qualitys[].nativeCode` 存原文。
4. **删除是墓碑不是缺席**：`deletedAt` 非空；墓碑的 GC 只看**确认水位**（删除发生时已注册的客户端都已在成功交付中见过它）**+ 身份退休（>180 天）**，**不设时间 TTL**（D20/D21）；**无删除能力的客户端（澜音）的"确认"必须来自提交内容**（它删不掉本地副本）；未知元素的墓碑也要留（防复活）。
5. **不认识的字段必须带回去**：进 `ext`，读取端禁止丢弃或报错。
6. **同一首歌只存一份**：曲目在 `tracks[]` 池里，歌单只存有序 `trackIds` 引用（省体积、防膨胀）。**多个歌单引用同一条曲目记录是合法的**——唯一性约束只要求 `tracks[]` 内身份不重复（`I3`）。

## 怎么新增一个平台

1. 在规范 §5.1 加一行 `source` code（小写，如 `tx`/`lx`）——**不升版本**。
2. 写一个适配器 `parse` / `render`，把该平台原生格式映射到规范模型。
3. 把该平台的品质原生码映射补进适配器的品质表（参考栖弦 `lib/core/sdk/internal/*_quality.dart`）。
4. 声明能力：`supports_delete` / `supports_create_playlist` / `multi_file` / `needs_propfind` / `max_doc_bytes`。
5. 写往返测试：`parse(render(doc)) ≡ doc`；lossy 行为登记在注释或 `LOSSY.md`。
6. 加一条示例（可放 `examples/`），跑校验器。
7. 更新 `canonical-v1.md` 的变更记录。

## 怎么新增一个字段

- **加法**（新可选字段、新品质码、新 `ext` 键）→ 不升版本；读取端必须容忍缺失。
- **平台专有字段** → 放 `platforms.<code>` 或 `ext.<code>.<field>`，**不要**污染通用字段。
- **破坏性变更**（改身份、改结构、改语义）→ 升 `schemaVersion`，服务端在迁移期同时渲染两版。

## 术语速查

**平台 code**：`tx`=QQ音乐、`wy`=网易云、`kw`=酷我、`kg`=酷狗、`mg`=咪咕（依据栖弦 `lib/core/models/enums.dart` 与澜音 `src/renderer/src/utils/sourceName.ts`）。

**品质优先级**：`master` > `atmos_plus` > `atmos` > `hires` > `flac24bit` > `flac` > `320k` > `192k` > `128k`。

## 相关文档

- [`../docs/03-总体架构.md`](../docs/03-总体架构.md) — 服务端架构、WebDAV 门面、请求时序、多用户与配额
- [`../docs/04-数据规范与适配器.md`](../docs/04-数据规范与适配器.md) — 适配器契约与逐客户端字段映射
- [`../docs/05-合并引擎.md`](../docs/05-合并引擎.md) — 合并语义（`base_served` 差分、墓碑、安全阀）
- [`../docs/10-评审记录与整改.md`](../docs/10-评审记录与整改.md) — 本规范的修订依据（哪些规则因评审而改）
- [`../reference/设计说明书-v1.md`](../reference/设计说明书-v1.md) — 历史设计说明书（保留决策脉络，不再维护）
