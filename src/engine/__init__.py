"""音枢合并引擎 —— 纯逻辑实现，证明"不丢数据"。

对应 D34 简化合并规则（见根 README 架构原理）：删除 = 段 updatedAt 变了 + 缺席；
段没变 + 缺席不动（M1 保护）；在场的并集；加回来就是加回来。无墓碑、无安全阀、无确认卡。
本包不依赖网络、不碰真实数据，仅用合成数据跑引擎用例。

子模块：
  canonical  canonical-v1 数据模型（身份键 / 曲目池 / 歌单 / deleted_at 标记）+ 确定性哈希
  engine     合并引擎（SyncSpace + MergeEngine，PUT/GET 语义）
"""
