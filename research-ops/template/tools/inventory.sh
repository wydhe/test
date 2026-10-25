#!/usr/bin/env bash
# One-time snapshot of an already-running project (used by /bootstrap): what runs on the GPUs,
# which tmux/screen sessions exist, which logs / results / checkpoints / code changed recently.
# Output is capped (~150 lines) so it is cheap to hand to Claude.
#   bash tools/inventory.sh [hours=72] [root=.]
hours=${1:-72}
root=${2:-.}
cd "$(dirname "$0")/.." || exit 1
echo "== $(hostname) | $(date '+%F %T') | window ${hours}h | root $(cd "$root" && pwd)"
echo "cpus $(nproc) | $(free -g 2>/dev/null | awk '/Mem:/ {print "ram " $2 "G, avail " $7 "G"}') | disk $(df -h "$root" | awk 'NR==2 {print $4 " free of " $2}')"

echo "== GPU"
nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu --format=csv,noheader 2>/dev/null \
  || echo "nvidia-smi unavailable"

echo "== GPU processes"
declare -A idx
while IFS=', ' read -r i u; do [ -n "$u" ] && idx[$u]=$i; done \
  < <(nvidia-smi --query-gpu=index,uuid --format=csv,noheader 2>/dev/null)
nvidia-smi --query-compute-apps=pid,gpu_uuid,used_memory --format=csv,noheader,nounits 2>/dev/null |
while IFS=', ' read -r pid uuid mem; do
  [ -z "$pid" ] && continue
  et=$(ps -o etime= -p "$pid" 2>/dev/null | tr -d ' ')
  cwd=$(readlink "/proc/$pid/cwd" 2>/dev/null)
  cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | cut -c1-220)
  echo "GPU${idx[$uuid]:-?} pid=$pid ${mem}MiB up=$et cwd=${cwd:-?}"
  echo "    ${cmd:-<not visible: other PID namespace?>}"
done

echo "== other python/torchrun processes (CPU side, max 15)"
ps -eo pid,etime,args --sort=-etime 2>/dev/null | grep -E 'python|torchrun|accelerate|deepspeed' \
  | grep -vE 'grep|inventory.sh|gpuq.py daemon|claude' | cut -c1-200 | head -n 15

echo "== tmux / screen"
tmux ls 2>/dev/null || echo "(no tmux sessions)"
screen -ls 2>/dev/null | grep -E '^\s+[0-9]+\.' || echo "(no screen sessions)"

echo "== gpuq"
python3 tools/gpuq.py status --brief 2>/dev/null | head -n 12 || echo "(gpuq not initialised)"

python3 - "$root" "$hours" <<'EOF'
import os, sys, time
root, hours = sys.argv[1], float(sys.argv[2])
cut = time.time() - hours * 3600
SKIP = {".git", ".gpuq", "__pycache__", "node_modules", ".ipynb_checkpoints", "data", "datasets", ".cache"}
LOGS = (".log", ".out", ".err", ".txt", ".jsonl", ".json", ".csv", ".yaml", ".yml", ".npz")
CKPT = (".pt", ".pth", ".ckpt", ".safetensors", ".bin")
recent, code, ckpt = [], [], {}
for d, dirs, files in os.walk(root):
    dirs[:] = [x for x in dirs if x not in SKIP and not x.startswith("wandb")]
    for f in files:
        p = os.path.join(d, f)
        try:
            st = os.stat(p)
        except OSError:
            continue
        if f.endswith(CKPT):
            c = ckpt.setdefault(d, [0, 0, 0.0])
            c[0] += 1; c[1] += st.st_size; c[2] = max(c[2], st.st_mtime)
        elif st.st_mtime >= cut and f.endswith(LOGS):
            recent.append((st.st_mtime, st.st_size, p))
        elif st.st_mtime >= cut and f.endswith((".py", ".sh")):
            code.append((st.st_mtime, p))
fmt = lambda t: time.strftime("%m-%d %H:%M", time.localtime(t))
print(f"== logs / results changed in {hours:g}h (newest first, {min(40, len(recent))} of {len(recent)})")
for t, s, p in sorted(recent, reverse=True)[:40]:
    print(f"{fmt(t)} {s / 1e6:8.2f}MB {p}")
print(f"== checkpoint directories (newest first, {min(20, len(ckpt))} of {len(ckpt)})")
for d, (n, s, t) in sorted(ckpt.items(), key=lambda kv: -kv[1][2])[:20]:
    print(f"{fmt(t)} {n:4d} ckpt {s / 1e9:7.1f}GB {d}")
print(f"== code changed in {hours:g}h ({min(15, len(code))} of {len(code)})")
for t, p in sorted(code, reverse=True)[:15]:
    print(f"{fmt(t)} {p}")
EOF

echo "== git"
git log --oneline -8 2>/dev/null || echo "(not a git repo)"
git status --short 2>/dev/null | head -n 15
