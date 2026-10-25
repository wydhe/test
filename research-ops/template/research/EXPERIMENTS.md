# 实验登记表
状态：planned / smoke / running / done / failed / dropped　判定：✅ 达标 / ⚠️ 边缘 / ❌ 失败 / – 未评
规则：先登记再提交；结果出来只改这一行（细节写进 DECISIONS.md）。Claude 用 Grep 取相关行，不整篇读取。

| ID | 计划项 | 假设 / 通过门槛 | 配置 · commit | gpuq | GPU·h | 状态 | 关键结果 | 判定 |
|---|---|---|---|---|---|---|---|---|
| E3-B0-b10-s0 | 5.3 B0（示例行，可删） | 下界基线；Dice ≥ 0.90 | cfg/seg.yaml · a1b2c3d | j0012 | 2 | done | Dice 0.912 | ✅ |
