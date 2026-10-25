#!/usr/bin/env bash
# SessionStart hook (startup / resume / clear / compact): print the project memory so every
# fresh or compacted session starts oriented. Claude Code adds this output to the context,
# so keep it short: STATE.md (<= 80 lines) + brief queue status + unprocessed INBOX items.
cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/..}" || exit 0
echo "<research-ops-context> 由 SessionStart hook 自动注入；这些文件不必再读一遍。"
if [ -f research/STATE.md ]; then
  echo "=== research/STATE.md ==="
  head -n 80 research/STATE.md
fi
echo "=== GPU / gpuq ==="
timeout 20 python3 tools/gpuq.py status --brief --limit 8 2>&1 | head -n 30
if [ -f research/INBOX.md ]; then
  n=$(grep -c '^- \[ \]' research/INBOX.md || true)
  if [ "${n:-0}" -gt 0 ]; then
    echo "=== INBOX: $n 条未处理（用 /triage 处理）==="
    grep '^- \[ \]' research/INBOX.md | tail -n 10
  fi
fi
echo "</research-ops-context>"
exit 0
