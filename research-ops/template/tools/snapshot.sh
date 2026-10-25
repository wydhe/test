#!/usr/bin/env bash
# One compact snapshot of the project for Claude: read research/snapshot.md once instead of
# exploring processes, logs, outputs and code. Costs no tokens to generate.
#   bash tools/snapshot.sh [hours=168]      -> research/snapshot.md (a few hundred lines)
hours=${1:-168}
cd "$(dirname "$0")/.." || exit 1
mkdir -p research
out=research/snapshot.md
{
  echo "# 项目快照 $(date '+%F %T')（bash tools/snapshot.sh $hours 生成，时间窗口 ${hours}h）"
  echo
  echo "## 1. GPU 与 gpuq 队列"
  echo '```'
  python3 tools/gpuq.py status 2>&1 | head -n 40
  echo '```'
  echo
  echo "## 2. 正在运行的进程与会话"
  echo '```'
  bash tools/inventory.sh "$hours" . 2>&1 | sed -n '1,/^== gpuq/p' | grep -v '^== gpuq' | head -n 60
  echo '```'
  python3 - "$hours" <<'EOF'
import glob, json, os, re, sys, time
sys.path.insert(0, "tools")
from digest_log import digest, _flatten

hours = float(sys.argv[1])
cut = time.time() - hours * 3600
proj = os.path.realpath(".")
SKIP = {".git", ".gpuq", "__pycache__", "node_modules", "data", "datasets", "third_party", "wandb",
        ".cache", ".research-ops-backup", "research", "docs", "tools", ".claude"}
RES_WORDS = ("result", "metric", "eval", "score", "summary")
logs, results, scanned = [], [], 0
for d, dirs, files in os.walk("."):
    dirs[:] = [x for x in dirs if x not in SKIP]
    scanned += len(files)
    if scanned > 300000:
        break
    for f in files:
        p = os.path.join(d, f)[2:]
        try:
            st = os.stat(p)
        except OSError:
            continue
        if st.st_mtime < cut:
            continue
        low = f.lower()
        if (low.endswith((".log", ".out", ".err")) or (low.endswith(".txt") and re.search(r"log|train|eval|out", low))) \
                and st.st_size > 100:
            logs.append((st.st_mtime, p))
        elif low.endswith((".json", ".csv")) and any(w in low for w in RES_WORDS):
            results.append((st.st_mtime, p, st.st_size))
logs += [(os.path.getmtime(p), p) for p in glob.glob(".gpuq/logs/j*.log") if os.path.getmtime(p) >= cut]

ts = lambda t: time.strftime("%m-%d %H:%M", time.localtime(t))
logs.sort(reverse=True)
print(f"\n## 3. 最近日志摘要（最新 {min(20, len(logs))} / 共 {len(logs)} 个）")
for t, p in logs[:20]:
    print(f"\n### {p}（{ts(t)}）\n```")
    print(digest(p, max_lines=10))
    print("```")

results.sort(reverse=True)
print(f"\n## 4. 结果文件（最新 {min(30, len(results))} / 共 {len(results)} 个）\n```")
for t, p, size in results[:30]:
    line = ""
    try:
        if p.endswith(".json") and size < 200000:
            d = json.load(open(p))
            flat = _flatten(d) if isinstance(d, dict) else {}
            nums = [f"{k}={v:.4g}" if isinstance(v, float) else f"{k}={v}" for k, v in flat.items()
                    if isinstance(v, (int, float)) and not isinstance(v, bool)]
            line = " ".join(nums[:14])
        elif p.endswith(".csv"):
            rows = open(p, errors="replace").read().splitlines()
            line = " | ".join(r[:160] for r in ([rows[0]] + rows[-2:] if len(rows) > 3 else rows))
    except (OSError, ValueError):
        line = "(无法解析)"
    print(f"{ts(t)} {p}: {line[:400]}")
print("```")

print("\n## 4b. 输出目录概览（最近更新的子目录）\n```")
for root in ("outputs", "runs"):
    if not os.path.isdir(root):
        continue
    subs = []
    for e in os.scandir(root):
        if e.is_dir():
            try:
                subs.append((e.stat().st_mtime, e.path, len(os.listdir(e.path))))
            except OSError:
                pass
    for t, p, n in sorted(subs, reverse=True)[:40]:
        print(f"{ts(t)} {p}/  ({n} 项)")
print("```")

# Commands Claude ran before: the best templates for new jobs.
norm = lambda s: re.sub(r"[^A-Za-z0-9]", "-", s)
key = norm(proj)
home = os.path.expanduser("~")
dirs = [d for d in glob.glob(os.path.join(home, ".claude", "projects", "*")) if norm(os.path.basename(d)).startswith(key)]
files = sorted((f for d in dirs for f in glob.glob(os.path.join(d, "*.jsonl"))), key=os.path.getmtime, reverse=True)[:40]
RUN = re.compile(r"\b(python3?|torchrun|accelerate|deepspeed)\b|\b(bash|sh)\s+\S*scripts/", re.A)
NOISE = re.compile(r"^\s*(python3? tools/|pip |conda (install|list)|ls |cat |grep |nvidia-smi|which |tail |head )|python3? -c ", re.A)
cmds = {}
for f in files:
    try:
        for line in open(f, encoding="utf-8", errors="replace"):
            if '"Bash"' not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            content = (rec.get("message") or {}).get("content")
            for c in content if isinstance(content, list) else []:
                if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") == "Bash":
                    cmd = (c.get("input") or {}).get("command", "")
                    if RUN.search(cmd) and not NOISE.search(cmd):
                        cmds[cmd.strip()] = rec.get("timestamp", "")
    except OSError:
        continue
recent = sorted(cmds.items(), key=lambda kv: kv[1])[-25:]
print(f"\n## 5. Claude 最近执行过的实验命令（本地历史，{len(files)} 个会话；新任务以此为模板）")
for cmd, t in recent:
    one = re.sub(r"\s*\n\s*", " ⏎ ", cmd)
    print(f"- [{t[5:16].replace('T', ' ')}] `{one[:450]}`")
if not recent:
    print("（没有找到）")

print("\n## 6. 脚本与参数（add_argument 摘要）\n```")
cands = sorted(glob.glob("scripts/**/*.py", recursive=True)) + sorted(glob.glob("src/**/*.py", recursive=True))
shown = 0
for p in cands:
    try:
        src = open(p, errors="replace").read()
    except OSError:
        continue
    if "add_argument" not in src:
        continue
    args = []
    for m in re.finditer(r"add_argument\(\s*['\"](--?[\w-]+)['\"]([^)]*)\)", src):
        dm = re.search(r"default\s*=\s*([^,]+)", m.group(2))
        args.append(m.group(1) + (f"={dm.group(1).strip()[:25]}" if dm else ""))
    print(f"{p}: {' '.join(args)[:600]}")
    shown += 1
    if shown >= 30:
        break
shell = sorted(glob.glob("scripts/**/*.sh", recursive=True))[:30]
if shell:
    print("shell 脚本:", " ".join(shell))
print("```")
EOF
  echo
  echo "## 7. 计划书目录（按节读：bash tools/plan_section.sh <节号>）"
  echo '```'
  bash tools/plan_section.sh --toc 2>&1 | head -n 70
  echo '```'
} > "$out"
echo "wrote $out: $(wc -l < "$out") lines, $(wc -c < "$out") bytes"
