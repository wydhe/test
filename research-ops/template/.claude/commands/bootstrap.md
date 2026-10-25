---
description: 第一次接管已经在跑的项目：盘点 → 对应到计划 → 建立 STATE / EXPERIMENTS（只需运行一次）
allowed-tools: Bash(bash tools/inventory.sh:*), Bash(bash tools/plan_section.sh:*), Bash(python3 tools/digest_log.py:*), Bash(python3 tools/gpuq.py:*), Bash(python3 tools/results_table.py:*)
---
目标：用最少的 token 弄清「项目进行到哪一步、哪些结果好或坏、下一步做什么」。

1. 运行 `bash tools/inventory.sh 168`（最近 7 天）。
2. 运行 `bash tools/plan_section.sh --toc`，然后只读这几节：结论 / 贡献、实验矩阵、时间线与门槛、风险与对策。
3. 对 inventory 里正在运行或最近更新的实验：日志不超过 8 个时，逐个运行 `python3 tools/digest_log.py <日志>`；更多时交给 `log-analyst` 子代理批量处理，让它返回一张表（路径、对应实验的猜测、进度、关键指标、异常）。已有 result 文件时用 results_table.py 汇总。
4. 填写 `research/EXPERIMENTS.md`：每个已有实验一行，对应到计划编号（不确定写 `?`），写明状态（running / done / failed / unknown）、关键结果、是否达到计划门槛。
5. 写 `research/STATE.md`：现在处于计划的第几周、哪个阶段；已过和未过的门槛；在跑什么（pid、预计结束时间）；最重要的 3 个风险；下一步 5 条。
6. 若存在 `CLAUDE.research-ops.md`（安装时保留了旧 CLAUDE.md）：把旧文件中仍然有效的约定压缩进新版第 7 节（≤ 25 行），新版替换旧版。
7. 正在跑的旧进程**不要动**（不 kill、不迁移）；从现在起新任务一律走 gpuq。
8. 计划里发现的矛盾或风险（例如在 TEST 上做调参性质的评估）写进 STATE「待我决策」。
9. 最后列出 ≤ 5 个需要我确认的问题（例如无法对应到计划的实验）。
