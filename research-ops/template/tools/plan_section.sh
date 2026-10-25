#!/usr/bin/env bash
# Print one section of the research plan instead of the whole file (saves ~10k+ tokens per read).
#   bash tools/plan_section.sh --toc        # headings with line numbers
#   bash tools/plan_section.sh 9.3          # section "9.3 ..." incl. its subsections
#   bash tools/plan_section.sh 消融          # first heading containing the text
# PLAN defaults to docs/plan.md.
PLAN="${PLAN:-$(dirname "$0")/../docs/plan.md}"
[ -f "$PLAN" ] || { echo "plan not found: $PLAN (set PLAN=...)"; exit 1; }
[ $# -ge 1 ] || { echo "usage: $0 --toc | <section-number> | <heading text>"; exit 1; }
if [ "$1" = "--toc" ]; then
  grep -n '^#\{1,4\} ' "$PLAN"
  exit 0
fi
awk -v sec="$1" '
  /^#+ / {
    match($0, /^#+/); lvl = RLENGTH
    title = substr($0, lvl + 2)
    if (printing && lvl <= start) exit
    if (!printing) {
      numeric = (sec ~ /^[0-9.]+$/)
      hit = numeric ? (index(title, sec " ") == 1 || index(title, sec ". ") == 1) : (index(title, sec) > 0)
      if (hit) { printing = 1; start = lvl }
    }
  }
  printing { print }
' "$PLAN"
