---
description: 一屏查看 GPU / 队列 / INBOX，并给出 ≤5 行判断
allowed-tools: Bash(bash tools/status.sh:*), Bash(python3 tools/gpuq.py:*), Bash(python3 tools/digest_log.py:*)
---
!`bash tools/status.sh`

只根据上面的输出回答，不要读其他文件：
1. ≤ 5 行：整体进展；异常（失败、NaN、OOM、daemon DOWN、长时间没有新指标）；GPU 是否闲置。
2. 若有 IDLE GPU：从 STATE「下一步」中挑能立刻跑的任务，给出 gpuq 提交命令，等我确认。
3. 若有失败或未处理的 INBOX：只提示我运行 `/triage`，不要自己展开分析。
