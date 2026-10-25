---
description: 每周复盘：进度 vs 计划时间线、GPU 利用率、风险、调整下周计划
allowed-tools: Bash(python3 tools/gpuq.py:*), Bash(bash tools/plan_section.sh:*), Bash(python3 tools/results_table.py:*)
---
1. 运行 `python3 tools/gpuq.py report --days 7`：各实验组的 GPU·h，以及失败浪费的 GPU·h。
2. 对照计划时间线（用 `--toc` 找到时间线那一节）：本周应该完成什么、实际完成了什么、偏差原因。
3. 写 `research/weekly/<日期>.md`（≤ 40 行）：完成与未完成、关键结果、GPU 利用率（目标：卡时 ≥ 85%）、失败浪费、风险 Top 3、下周计划（含要提交的任务）。
4. 时间线整体落后超过 1 周时：提出削减方案（哪些消融或扩展可以砍，按对论文贡献的影响排序），写进 STATE「待我决策」。
5. 更新 STATE.md。回复 ≤ 8 行。
