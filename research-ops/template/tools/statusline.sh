#!/usr/bin/env bash
# Claude Code status line: model + GPU/queue/inbox summary, refreshed at most every 15 s.
# The status line is shown to you only; it costs no model tokens.
dir="$(cd "$(dirname "$0")/.." && pwd)"
model=$(python3 -c 'import json, sys
try:
    print(json.load(sys.stdin)["model"]["display_name"])
except Exception:
    print("claude")' 2>/dev/null)
cache="$dir/.gpuq/statusline.cache"
now=$(date +%s)
age=$((now - $(stat -c %Y "$cache" 2>/dev/null || echo 0)))
if [ "$age" -ge 15 ]; then
  mkdir -p "$dir/.gpuq"
  line=$(cd "$dir" && timeout 10 python3 tools/gpuq.py status --line 2>/dev/null)
  n=$(grep -c '^- \[ \]' "$dir/research/INBOX.md" 2>/dev/null || true)
  printf '%s · inbox %s' "${line:-gpuq ?}" "${n:-0}" > "$cache.tmp" && mv "$cache.tmp" "$cache"
fi
printf '[%s] %s' "${model:-claude}" "$(cat "$cache" 2>/dev/null)"
