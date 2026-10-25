---
description: 处理已结束的实验：取证 → 对照门槛 → 判定 → PLAYBOOK 对策 → 登记 → 提交下一步
argument-hint: "[jobid | tag:E3 | 留空 = INBOX 中全部未处理项]"
allowed-tools: Bash(python3 tools/gpuq.py:*), Bash(python3 tools/digest_log.py:*), Bash(python3 tools/results_table.py:*), Bash(bash tools/plan_section.sh:*), Bash(bash tools/status.sh:*)
---
处理对象：$ARGUMENTS

严格按顺序执行，尽量少读文件：
1. **取证**：每个相关 job 运行 `python3 tools/digest_log.py <jobid>`；同一 tag 的多 seed / 多配置用 `python3 tools/results_table.py --tag <tag>`。digest 不足以定位失败原因时，派 `log-analyst` 子代理，**不要自己读原始日志**。
2. **定标**：Grep `research/EXPERIMENTS.md` 中该实验的行，取「假设 / 门槛」。行里没有门槛时，用 `bash tools/plan_section.sh <节号>` 读计划中对应门槛（只读那一节）。
3. **判定**：✅ 达标 / ⚠️ 边缘 / ❌ 失败，必须写出「数字 vs 门槛」。差异小于 seed 间标准差的，不算提升。
4. **对策**（⚠️ / ❌ 时）：Grep `research/PLAYBOOK.md` 匹配症状，给出主对策 + 备选，各附预计 GPU·h。没有匹配时按「数据 → 代码 bug → 超参 → 方法」的顺序定位根因，再提对策，并把新条目追加进 PLAYBOOK。
5. **记录**：更新 EXPERIMENTS.md 对应行（状态 / 关键结果 / 判定）；DECISIONS.md 追加一条（证据、决策、回滚条件）；INBOX.md 中对应项改为 `[x]`。
6. **下一步**：对策成本 < 4 GPU·h 且不改变论文主线时，直接写 jsonl 并 `python3 tools/gpuq.py batch` 提交（先登记）。否则把选项写进 STATE「待我决策」，不要提交。
7. 覆盖写 `research/STATE.md`（≤ 60 行）。

回复我：≤ 15 行（每个实验一行判定 + 对策 + 已提交的 job id）。
