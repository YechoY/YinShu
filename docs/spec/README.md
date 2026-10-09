# `spec/` — 歌单同步统一格式规范

跨平台歌单同步服务的数据规范族。**这里定义"唯一事实"长什么样**；各客户端原生格式由适配器翻译过来。

| 文件 | 内容 |
|---|---|
| [`canonical-v1.md`](canonical-v1.md) | **主规范**：原则、字段表、代码表、身份规则、时间与删除语义、扩展机制、序列化与校验、兼容性、适配器契约、性能与规模 |
| [`canonical-v1.schema.json`](canonical-v1.schema.json) | JSON Schema（draft 2020-12），机器可校验 |
| [`examples/canonical-v1.example.json`](examples/canonical-v1.example.json) | 完整示例：多平台引用、未知品质码、不可用状态、已删记录、命名空间化 `ext` |
| [`validate-canonical-v1.py`](validate-canonical-v1.py) | 校验器（仅标准库），实现规范里的 `V1`–`V12` |

校验示例：

```pwsh
python spec\validate-canonical-v1.py spec\examples\canonical-v1.example.json
```

## 30 秒读懂

1. **身份只有一对**：曲目 = `identity.source` + `identity.songId`（`tx` + 裸 id）；歌单 = 规范 `id` + 各端 `nativeId` 映射。
2. **一首歌可挂多平台**：`platforms` 是 map，同一首歌在 QQ 和网易云都有就都留着。
3. **品质归一但保留原生码**：`qualities[].code` 用统一码，`platforms.<p>.qualitys[].nativeCode` 存原文。
4. **删除 = 段 updatedAt 变了 + 缺席**（D34）：客户端提交里缺席、且对应段 `updatedAt` 已变化 ⇒ 服务端判删；段未变时缺席**不**判删（M1 保护）。服务端以 `deletedAt` 内部标记（物理不删），`deletedAt` **不是**客户端提交字段；**加回来就是加回来**——无墓碑对象、无确认水位、无墓碑压制（原 T3/T4 已移除）。
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

- [`../README.md`](../README.md) — 项目主页：架构原理（D34 合并规则）、快速开始、常用命令
- [`../12-空间与成员体系.md`](../12-空间与成员体系.md) — 空间 / 成员 / 角色 / 邀请码 / 配额
- [`../13-接入新客户端.md`](../13-接入新客户端.md) — 适配器插件化：新增客户端只需加一个文件
- [`../14-洛雪接入.md`](../14-洛雪接入.md) — `lx-x` 方言接入细节
- [`docs/README.md`](README.md) — 本文档（spec 导览）
