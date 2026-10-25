#!/usr/bin/env bash
# Install the research-ops kit into a project without overwriting anything.
#   bash research-ops/install.sh /path/to/project
# Existing CLAUDE.md / .claude/settings.json are kept; the kit's versions are saved next to
# them as *.research-ops.* for merging (the /bootstrap command merges CLAUDE.md for you).
set -euo pipefail
src="$(cd "$(dirname "$0")/template" && pwd)"
dst="$(cd "${1:?usage: install.sh <project-dir>}" && pwd)"
copied=0
kept=0
while IFS= read -r -d '' f; do
  rel="${f#"$src"/}"
  target="$dst/$rel"
  mkdir -p "$(dirname "$target")"
  if [ ! -e "$target" ]; then
    cp "$f" "$target"
    copied=$((copied + 1))
    continue
  fi
  kept=$((kept + 1))
  case "$rel" in
    CLAUDE.md | .claude/settings.json)
      alt="${target%.*}.research-ops.${target##*.}"
      cp "$f" "$alt"
      echo "kept your $rel; kit version saved as ${alt#"$dst"/} (merge it)" ;;
    *) echo "kept existing $rel" ;;
  esac
done < <(find "$src" -type f -not -path '*/__pycache__/*' -not -name '*.pyc' -print0)

# hooks and the status line need absolute paths
for s in "$dst/.claude/settings.json" "$dst/.claude/settings.research-ops.json"; do
  if [ -f "$s" ]; then
    sed -i "s#__PROJECT_DIR__#$dst#g" "$s"
  fi
done
chmod +x "$dst"/tools/*.sh "$dst"/tools/*.py
touch "$dst/.gitignore"
for pat in ".gpuq/" "runs/" "research/triage_logs/" "*.pt" "*.pth" "*.ckpt" "*.safetensors"; do
  grep -qxF "$pat" "$dst/.gitignore" || echo "$pat" >> "$dst/.gitignore"
done
(cd "$dst" && python3 tools/gpuq.py init > /dev/null)

cat <<EOF
research-ops installed into $dst ($copied files copied, $kept existing files kept)
next:
  1. cp <your plan>.md $dst/docs/plan.md
  2. tmux new -d -s gpuq 'cd $dst && python3 tools/gpuq.py daemon >> .gpuq/daemon.log 2>&1'
  3. cd $dst && python3 tools/gpuq.py doctor      # check GPUs + memory accounting
  4. edit CLAUDE.md section 0 (project, env, data dir), then: claude  ->  /bootstrap
EOF
