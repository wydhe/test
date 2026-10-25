# CLAUDE.md — 研究项目宪法（每轮都会加载：保持 ≤ 100 行，细节放到 research/ 里）

## 0. 项目
- 名称 / 目标：<PROJECT>，投稿 <VENUE>，截稿 <DEADLINE>。
- 计划书：`docs/plan.md`。**禁止整篇 Read**：先 `bash tools/plan_section.sh --toc`，再 `bash tools/plan_section.sh <节号|关键词>` 只读需要的节。
- 机器：4×A800-80G，<N> CPU，<RAM> 内存。环境：`conda activate <ENV>`。数据：`<DATA_DIR>`（只读）。
- 项目特定约定见第 7 节。

## 1. 记忆 = 文件（唯一真相源，会话可随时 /clear）
| 文件 | 内容 | 写法 |
|---|---|---|
| `research/STATE.md` | 阶段、在跑什么、关键数字、阻塞、待我决策、下一步 | 覆盖写，≤ 60 行 |
| `research/EXPERIMENTS.md` | 实验登记表，一行一个实验 | 提交前登记，结束时改该行 |
| `research/DECISIONS.md` | 决策日志：证据 → 决策 → 回滚条件 | 只追加 |
| `research/PLAYBOOK.md` | 失败模式 → 诊断 → 对策 | 新问题解决后追加 |
| `research/INBOX.md` | gpuq 自动写入的任务结束通知 | 处理后 `[ ]` → `[x]` |

- 会话开始时 hook 已注入 STATE、队列状态、未处理 INBOX：**不要再读这些文件**。EXPERIMENTS / PLAYBOOK 用 Grep 只取相关行。
- 一有实质变化（提交实验、拿到结果、做出决策）立刻写回文件，不要攒到会话结束。

## 2. GPU 任务一律走 gpuq（4 卡共享，按显存打包）
- 单个：`python3 tools/gpuq.py submit -n <名> -m <峰值显存GB/卡> [-g 卡数] [-x 独占] [-t E3] [-a 依赖] [--cpus N] -- <命令>`
- 多个：写 `research/queue/<日期>-<主题>.jsonl`（每行 `{"name","mem","cmd","tag","ngpu","exclusive","after","cpus"}`），`python3 tools/gpuq.py batch <文件>`。
- 显存不确定就多估 20%；OOM 会自动 ×1.3 重试。< 20G 的小任务不要 `-x`，让它们共享 GPU。
- 训练脚本用 `tools/rlog.py` 写 `runs/<实验ID>/{config.json, metrics.jsonl, result.json}`。
- 禁止：直接 `nohup python … &`、手动设 CUDA_VISIBLE_DEVICES 抢卡、kill 不是 gpuq 管理的进程（先问我）。
- 提交后**结束本回合**，不要 sleep / 轮询；结果会自动进 INBOX。

## 3. 省 token 硬规则
1. 看全局：`bash tools/status.sh`（约 40 行）。看单个任务：`python3 tools/digest_log.py <jobid|日志路径>`。
2. **禁止** `cat` / 大段 `tail` / 整篇 Read 原始日志。日志细节交给 `log-analyst` 子代理，它只返回摘要。
3. 多次运行汇总：`python3 tools/results_table.py --tag <E?>`，不要逐个读 result.json。
4. 读代码：先 Grep 定位，再带 offset/limit 读；同一文件不重复读。
5. 回复：结论 + 数字 + 下一步。不复述上下文，不贴大段代码或日志。
6. 一个会话只做一件事；完成里程碑后 `/handoff`，然后提醒我 `/clear`。

## 4. 实验规范
- ID：`<计划编号>-<配置>-<预算/变体>-s<seed>`，如 `E3-B2-b10-s0`；gpuq 的 `-t` 用计划编号（E0…E7）。
- 先在 EXPERIMENTS.md 登记「假设 + 通过门槛 + 预计 GPU·h」，再提交。
- 改代码先 `git commit`，EXPERIMENTS 记 commit；不在未提交的工作区上跑正式实验。
- 只在 VAL 上选模型和超参；TEST 每个最终配置只评一次，并记入 DECISIONS.md。
- 长任务先跑 ≤ 10 分钟的冒烟版（同命令、少步数），通过后再用 `-a` 依赖它提交全量。

## 5. 结果处理（/triage）
证据（digest / 表格）→ 对照门槛 → 判定 ✅ / ⚠️ / ❌ → 在 PLAYBOOK 匹配，给主对策 + 备选（附 GPU·h）→ 记录（EXPERIMENTS / DECISIONS / INBOX）→ 低成本且不改主线的对策直接提交，否则写进 STATE「待我决策」。
同一方向连续 2 次 ❌：停止加码，写方向复盘，建议 `/review-packet` 请外部评审。

## 6. 模型与外部评审
- 监控、登记、汇总：Sonnet（`/model sonnet`）或 log-analyst 子代理。方法设计、疑难调试、写作：Opus。
- 外部评审（Codex / GPT，ARIS 技能）只在里程碑或卡住时用，先 `/review-packet` 压缩材料。

## 7. 项目特定约定
<!-- /bootstrap 会把旧 CLAUDE.md 中仍然有效的约定压缩到这里（≤ 25 行） -->
