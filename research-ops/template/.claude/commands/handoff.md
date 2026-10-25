---
description: 会话收尾：把状态写回文件，之后可以安全地 /clear
allowed-tools: Bash(git status:*), Bash(git add:*), Bash(git commit:*), Bash(git diff --stat:*)
---
1. 覆盖写 `research/STATE.md`（≤ 60 行，按文件里的模板）：阶段 / 周次、在跑任务（gpuq id 或旧进程 pid）、最新关键数字（带实验 ID）、门槛状态、阻塞、待我决策、下一步（最多 5 条，每条可以直接执行）。
2. 确认本会话产生的实验和决策已写进 EXPERIMENTS.md / DECISIONS.md；新的失败经验已追加到 PLAYBOOK.md。
3. INBOX.md：删除已标记 `[x]` 的行（信息已在 EXPERIMENTS 里）。
4. 本会话改过代码时：`git status` 确认 runs/、数据、权重没有被纳入，然后只提交代码和 research/：`git add <具体文件> && git commit -m "<简述>"`。
5. 回复我 ≤ 3 行，最后一行写「可以 /clear 了」。
