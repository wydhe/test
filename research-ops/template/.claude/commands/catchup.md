---
description: 低额度一次性追进度：读快照 → 汇总结果 → 判定 → 更新记忆 → 排好接下来 24–36h 的 GPU 队列（只生成，不提交）
argument-hint: "[可选：重点或约束，如「只排 E0 和分割基线」]"
allowed-tools: Bash(bash tools/snapshot.sh:*), Bash(bash tools/plan_section.sh:*), Bash(python3 tools/gpuq.py:*), Bash(python3 tools/digest_log.py:*), Bash(python3 tools/results_table.py:*)
---
重点：$ARGUMENTS

目标：用尽量少的 token 完成「看结果 → 判断 → 排下一步」。
硬规则：**信息只来自 `research/snapshot.md`**。不运行 inventory，不读原始日志，不浏览代码目录。只有在拼任务命令时确实缺参数，才对单个脚本 Grep `add_argument`（每个脚本最多一次）。整个过程的工具调用不超过 15 次。

1. **快照**：`research/snapshot.md` 不存在或不是今天生成的，就先运行 `bash tools/snapshot.sh`。然后一次读完它。
2. **结果汇总**：把快照里的实验对应到计划编号（先看 EXPERIMENTS.md 已有的行，用 Grep 取门槛；需要时 `bash tools/plan_section.sh <节号>` 只读一节）。在 EXPERIMENTS.md 里更新或新增行：状态、关键数字、判定 ✅/⚠️/❌（写明数字 vs 门槛）。对应关系拿不准就标 `?`，不要猜。
3. **坏结果**：对 ⚠️/❌ 的实验，Grep PLAYBOOK 匹配症状，给出主对策和备选，各附 GPU·h。
4. **记忆**：覆盖写 STATE.md（≤ 60 行）；本次的关键判断合并写成 DECISIONS.md 的一条。
5. **排队**：规划从现在起 24–36 小时的 4 卡任务，保证我离开期间 GPU 一直有活：
   - 命令以快照第 5 节「最近执行过的实验命令」为模板，只改参数；只用已经存在的脚本，不写新代码；
   - **去掉** `nohup`、结尾的 `&`、输出重定向 `> xxx.log`，以及 `CUDA_VISIBLE_DEVICES=…`（gpuq 会负责后台运行、日志和分卡；带这些的任务会被拒绝）；
   - 关键路径优先；新命令或新配置先排一个 ≤ 10 分钟的冒烟版，全量版用 `after` 依赖它；
   - `mem` 参考快照第 2 节里同类进程的实际显存再加 20%；小任务共享卡，大训练用 `exclusive`；
   - 仍在运行的旧进程不用管，gpuq 会自动避开它们占用的显存；
   - 写入 `research/queue/<日期>-catchup.jsonl`，并在 EXPERIMENTS.md 登记为 planned。
6. **回复我（≤ 25 行）**：
   - 结果总结：按计划编号列出好 / 坏 / 不确定各几条，以及最重要的 3 个发现；
   - 任务表：名称、卡数×显存、预计时长、依赖、目的；总 GPU·h，以及大约能让 GPU 忙到几点；
   - 需要我决定的事（≤ 3 条）；
   - 提交命令：`python3 tools/gpuq.py batch research/queue/<文件>`（我自己在终端运行）。

不要替我提交任务，也不要在回复之后继续做额外分析。
