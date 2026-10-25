---
description: 为外部评审（Codex / ChatGPT / ARIS）生成 ≤2500 字的压缩材料包；有 codex MCP 时可直接发起评审
argument-hint: "[评审焦点，如 P0 go/no-go、方法新颖性、主表是否站得住]"
allowed-tools: Bash(python3 tools/results_table.py:*), Bash(bash tools/plan_section.sh:*)
---
焦点：$ARGUMENTS

1. 写 `research/review/packet-<日期>.md`（≤ 2500 字），内容：
   - 一段话：论文主张与贡献（以计划书结论节为基础，按实际进展修正）；
   - 关键结果表：只保留和焦点相关的行，带实验 ID 和 seed 数（results_table.py 输出）；
   - 失败或反常的结果，以及已经试过的对策（来自 DECISIONS / PLAYBOOK）；
   - 3–6 个**具体**问题，例如「B6 只比 B2 高 0.3 Dice，能否支撑贡献 1？还缺哪个对照？」；
   - 不放代码、原始日志、患者数据或服务器路径。
2. 评审方式：
   - 已装 ARIS 且 `codex` MCP 可用：调用 ARIS 的单次评审技能（`/research-review` 或 `/aris:research-review`），把 packet 作为评审材料。默认只发 packet，不让评审方读整个仓库。
   - 否则：告诉我 packet 的路径，由我粘贴到 ChatGPT。
3. 拿到评审意见后：逐条分为接受 / 部分接受 / 拒绝（拒绝要写理由）。接受的条目转成 EXPERIMENTS 的 planned 行或 STATE 的下一步，并写一条 DECISIONS。
