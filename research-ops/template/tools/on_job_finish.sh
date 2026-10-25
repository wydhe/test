#!/usr/bin/env bash
# gpuq on_finish hook (set in .gpuq/config.json). Runs in the project root with
# GPUQ_JOB_ID, GPUQ_STATUS, GPUQ_JOB_NAME, GPUQ_TAG, GPUQ_JOB_FILE in the environment.
#   1) appends one line to research/INBOX.md - Claude's asynchronous memory of finished jobs
#   2) optional push notification: NOTIFY_CMD in .gpuq/hook.env, the message is in $MSG, e.g.
#        NOTIFY_CMD='curl -s -d "$MSG" https://ntfy.sh/<your-topic>'
#   3) optional unattended triage once every job of a tag has finished: GPUQ_AUTO_TRIAGE=1
set -u
[ -f .gpuq/hook.env ] && . .gpuq/hook.env
mkdir -p research
inbox=research/INBOX.md
[ -f "$inbox" ] || printf '# INBOX（gpuq 自动写入；/triage 处理后改为 [x]）\n\n' > "$inbox"

info=$(python3 - "$GPUQ_JOB_FILE" <<'EOF'
import json, sys
j = json.load(open(sys.argv[1]))
hours = ((j.get("ended") or 0) - (j.get("started") or j.get("ended") or 0)) / 3600
err = f" | {j['err'][:120]}" if j.get("err") and j["status"] != "done" else ""
print(f"{j['name']} [{j.get('tag') or '-'}] {j['status']} exit={j.get('exit')} {hours:.1f}h{err}")
EOF
)
line="- [ ] $(date '+%m-%d %H:%M') ${GPUQ_JOB_ID} ${info}"
echo "$line" >> "$inbox"

if [ -n "${NOTIFY_CMD:-}" ]; then
  MSG="$line" bash -c "$NOTIFY_CMD" >/dev/null 2>&1 || true
fi

if [ "${GPUQ_AUTO_TRIAGE:-0}" = 1 ] && [ -n "${GPUQ_TAG}" ] && command -v claude >/dev/null; then
  left=$(python3 tools/gpuq.py ls --tag "$GPUQ_TAG" --status queued,running --count)
  mkdir -p research/triage_logs
  # mkdir is atomic: only the first hook of a finished tag starts a triage run
  if [ "$left" = 0 ] && mkdir "research/triage_logs/.lock-$GPUQ_TAG-$(date +%m%d%H)" 2>/dev/null; then
    out="research/triage_logs/$(date +%m%d-%H%M)-$GPUQ_TAG.md"
    nohup timeout 1800 claude -p "按 .claude/commands/triage.md 的流程处理 tag:$GPUQ_TAG。无人值守模式：只做登记、诊断和记录；只允许提交成本 <4 GPU·h 且不改变论文主线的修复任务，其余写进 research/STATE.md 的「待我决策」。" \
      --model "${GPUQ_TRIAGE_MODEL:-sonnet}" --max-turns 40 \
      --allowedTools "Read" "Grep" "Glob" "Edit" "Write" \
        "Bash(python3 tools/gpuq.py:*)" "Bash(python3 tools/digest_log.py:*)" \
        "Bash(python3 tools/results_table.py:*)" "Bash(bash tools/status.sh:*)" \
        "Bash(bash tools/plan_section.sh:*)" \
      > "$out" 2>&1 < /dev/null &
  fi
fi
exit 0
