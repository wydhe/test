---
description: 规划接下来 24–48h 的 GPU 任务，让 4 张卡保持满载（生成 gpuq batch，确认后提交）
argument-hint: "[重点，如 E3 基线；留空 = 按计划时间线]"
allowed-tools: Bash(python3 tools/gpuq.py:*), Bash(bash tools/plan_section.sh:*)
---
重点：$ARGUMENTS

1. **现状**：使用会话开头注入的 STATE 和队列信息；需要时运行 `python3 tools/gpuq.py ls --status queued,running`。
2. **候选任务**：从 STATE「下一步」、EXPERIMENTS 中 `planned` 的行、计划时间线（`bash tools/plan_section.sh --toc` 找到时间线那一节）中，列出不依赖未完成结果的任务。
3. **排布原则**：
   - 关键路径（后续实验依赖的任务）优先：高 `prio`，需要时 `-g 2 -x` 独占。
   - 小显存任务（分割、回归、评估、采样分片）填空隙，每卡可以并发多个；用 `--cpus` 防止 CPU 或 dataloader 成为瓶颈。
   - 超过 12h 的任务先排一个 ≤ 10 分钟的冒烟版，全量版用 `-a` 依赖它。
   - 每个任务都要写 `mem` 估计，依据是同类任务的实测峰值（gpuq doctor 或 digest 中的显存记录）。
   - 留出 1 个空位，给 triage 产生的修复任务。
4. **产出**：写 `research/queue/<日期>-<主题>.jsonl`，并在 EXPERIMENTS.md 登记新行（假设、门槛、GPU·h）。
5. **给我看**：一张表（任务、卡数×显存、预计时长、依赖、为什么现在跑），外加总 GPU·h 和预计 4 卡占用率。我确认后再执行 `python3 tools/gpuq.py batch <文件>`。
