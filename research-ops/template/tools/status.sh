#!/usr/bin/env bash
# One-screen project status (~40 lines): GPUs, gpuq queue, unprocessed INBOX items.
# Claude should run this instead of reading logs; humans can run it in a tmux pane for free.
cd "$(dirname "$0")/.." || exit 1
python3 tools/gpuq.py status "$@"
inbox=research/INBOX.md
if [ -f "$inbox" ]; then
  n=$(grep -c '^- \[ \]' "$inbox" || true)
  if [ "${n:-0}" -gt 0 ]; then
    echo "INBOX: $n unprocessed (run /triage)"
    grep '^- \[ \]' "$inbox" | tail -n 8
  fi
fi
