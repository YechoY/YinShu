"""M1 合并引擎 PoC —— 纯逻辑实现，证明"不丢数据"。

对应方案 docs/05 合并引擎（基线差分 / 墓碑 / 删除优先 / 安全阀 / 幂等 / 墓碑压制 I1-I7）。
本包不依赖网络、不碰真实数据，仅用合成数据跑 26 条边界用例。

子模块：
  canonical  canonical-v1 数据模型（身份键 / 曲目池 / 歌单 / 墓碑）+ 确定性哈希
  engine     合并引擎（SyncSpace + MergeEngine，PUT/GET 语义）
"""
