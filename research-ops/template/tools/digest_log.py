#!/usr/bin/env python3
"""Summarise a training/eval log in ~20 lines so nobody has to read it raw.

    python3 tools/digest_log.py j0012              # gpuq job id
    python3 tools/digest_log.py runs/E1/train.log  # any log file
    python3 tools/digest_log.py LOG --keys loss,dice -n 30

Reports: progress (last step/epoch), per-metric first/best/last and trend,
NaN/Inf, OOM, the last exception, and a metrics.jsonl next to the log if one
exists. Works on plain text logs (key=value / key: value, tqdm postfixes)
and on JSON-lines logs.
"""
import argparse
import json
import math
import os
import re
import sys
import time
from pathlib import Path

NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?|nan|NaN|inf|-inf|Inf"
KV_RE = re.compile(r"(?<![\w./-])([A-Za-z][\w./-]{0,40}?)\s*[=:]\s*(" + NUM + r")(?![\w.])")
PROGRESS = ("global_step", "step", "iter", "iteration", "it", "epoch", "ep", "nfe")
LOWER_BETTER = ("loss", "err", "mae", "mse", "rmse", "fvd", "fid", "kid", "hd", "assd", "lpips",
                "nll", "bpd", "time", "straight", "curv")
HIGHER_BETTER = ("dice", "iou", "acc", "r2", "auc", "auroc", "psnr", "ssim", "precision", "recall",
                 "f1", "coverage", "density", "spearman", "pearson")
NOISE_KEYS = {"lr", "learning_rate", "pid", "rank", "local_rank", "world_size", "seed", "gpu", "gpus",
              "mem", "memory", "batch", "bs", "workers", "port", "eta", "s", "ms", "t"}
OOM_RE = re.compile(r"CUDA out of memory|OutOfMemoryError|CUBLAS_STATUS_ALLOC_FAILED", re.I)
ERR_RE = re.compile(r"(Error|Exception|Killed|Segmentation fault|core dumped|Aborted)\b")


def resolve(arg):
    if re.fullmatch(r"j\d+", arg):
        home = Path(os.environ.get("GPUQ_HOME") or Path(__file__).resolve().parent.parent / ".gpuq")
        return home / "logs" / f"{arg}.log"
    return Path(arg)


def read_text(path, head=1 << 20, tail=8 << 20):
    """Whole file if small, else head + tail (logs can be GBs)."""
    size = path.stat().st_size
    with open(path, "rb") as f:
        if size <= head + tail:
            return f.read().decode("utf-8", "replace"), False
        h = f.read(head)
        f.seek(size - tail)
        return (h + b"\n" + f.read()).decode("utf-8", "replace"), True


def split_lines(text):
    return [ln for ln in re.split(r"[\r\n]+", text) if ln.strip()]


def to_float(s):
    try:
        return float(s)
    except ValueError:
        return float("nan")


def _flatten(d, prefix=""):
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "/"))
        else:
            out[key] = v
    return out


def parse_line(line):
    line = line.strip()
    if line.startswith("{") and line.endswith("}"):
        try:
            d = json.loads(line)
            return {k: float(v) for k, v in _flatten(d).items()
                    if isinstance(v, (int, float)) and not isinstance(v, bool)}
        except ValueError:
            pass
    if line.startswith("### "):  # gpuq header lines (start time, command)
        return {}
    return {m.group(1): to_float(m.group(2)) for m in KV_RE.finditer(line)}


def direction(key):
    k = key.lower()
    if any(w in k for w in HIGHER_BETTER):
        return "max"
    if any(w in k for w in LOWER_BETTER):
        return "min"
    return None


def fmt(v):
    if v is None or math.isnan(v):
        return "nan"
    if math.isinf(v):
        return "inf" if v > 0 else "-inf"
    a = abs(v)
    if a != 0 and (a < 1e-3 or a >= 1e5):
        return f"{v:.3g}"
    return f"{v:.4g}"


def collect(lines, keys=None):
    series, progress, bad = {}, {}, []
    for n, line in enumerate(lines):
        for k, v in parse_line(line).items():
            kl = k.lower()
            if kl in PROGRESS:
                progress[kl] = v
                continue
            if kl in NOISE_KEYS or (keys and k not in keys):
                continue
            series.setdefault(k, []).append(v)
            if (math.isnan(v) or math.isinf(v)) and len(bad) < 3:
                bad.append((n, k))
    return series, progress, bad


def last_exception(lines):
    idx = max((i for i, ln in enumerate(lines) if "Traceback (most recent call last)" in ln), default=None)
    if idx is not None:
        block = lines[idx:idx + 200]
        frames = [ln.strip() for ln in block if ln.strip().startswith("File ")]
        final = next((ln.strip() for ln in reversed(block) if ERR_RE.search(ln)), block[-1].strip())
        return frames[-2:], final
    for ln in reversed(lines[-400:]):
        if ERR_RE.search(ln) and not ln.startswith("### "):
            return [], ln.strip()
    return [], None


def digest(path, max_lines=25, keys=None):
    path = resolve(str(path))
    if not path.exists():
        return f"digest: {path} not found"
    text, partial = read_text(path)
    lines = split_lines(text)
    st = path.stat()
    out = [f"{path}  {st.st_size / 1e6:.1f}MB  modified {int((time.time() - st.st_mtime) / 60)}m ago"
           f"  lines~{len(lines)}{'  (head+tail only)' if partial else ''}"]

    mj = path.parent / "metrics.jsonl"
    if mj.exists() and mj != path:
        out.append(f"(also read {mj})")
        lines = lines + split_lines(mj.read_text(errors="replace"))

    series, progress, bad = collect(lines, keys)
    if progress:
        out.append("progress: " + " ".join(f"{k}={fmt(v)}" for k, v in progress.items()))
    if OOM_RE.search(text[-200000:]):
        out.append("!! CUDA OOM detected")
    if bad:
        out.append("!! NaN/Inf: " + ", ".join(f"{k} (line~{n})" for n, k in bad))

    # Metrics with a known direction first, then the most frequently logged ones.
    ranked = sorted(series.items(), key=lambda kv: (direction(kv[0]) is None, -len(kv[1])))
    budget = max(3, max_lines - len(out) - 4)
    if ranked:
        out.append(f"{'metric':<28}{'n':>6}{'first':>10}{'best':>10}{'last':>10}  trend")
    for k, vals in ranked[:budget]:
        finite = [v for v in vals if not (math.isnan(v) or math.isinf(v))]
        d = direction(k)
        best = (min(finite) if d == "min" else max(finite)) if (d and finite) else None
        first, last = vals[0], vals[-1]
        trend = ""
        if len(finite) > 1 and first and math.isfinite(first) and math.isfinite(last):
            trend = f"{(last - first) / abs(first) * 100:+.0f}%"
            if best is not None and abs(last - best) > 0.02 * max(abs(best), 1e-8):
                trend += " (last worse than best)"
        out.append(f"{k[:28]:<28}{len(vals):>6}{fmt(first):>10}{fmt(best) if best is not None else '-':>10}"
                   f"{fmt(last):>10}  {trend}")
    if len(ranked) > budget:
        out.append(f"... {len(ranked) - budget} more metrics (use --keys)")

    frames, final = last_exception(lines)
    if final:
        out.append("last error: " + final[:200])
        out += ["  at " + f[:160] for f in frames]
    body = [ln for ln in lines if not ln.startswith("### ")]
    out.append("tail: " + " | ".join(ln.strip()[:90] for ln in body[-3:]))
    return "\n".join(out)


def last_metrics(path, nkeys=4):
    """One short string with the most recent metrics, for status tables."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 32768))
            lines = split_lines(f.read().decode("utf-8", "replace"))
    except OSError:
        return ""
    found, prog = {}, {}
    for line in reversed(lines[-200:]):
        for k, v in parse_line(line).items():
            kl = k.lower()
            if kl in PROGRESS:
                prog.setdefault(kl, v)
            elif kl not in NOISE_KEYS and k not in found:
                found[k] = v
        if len(found) >= nkeys and prog:
            break
    keys = sorted(found, key=lambda k: direction(k) is None)[:nkeys]
    parts = [f"{k}={fmt(v)}" for k, v in list(prog.items())[:1]] + [f"{k}={fmt(found[k])}" for k in keys]
    return " ".join(parts)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("logs", nargs="+", help="log file(s) or gpuq job id(s)")
    p.add_argument("-n", "--max-lines", type=int, default=25)
    p.add_argument("--keys", help="comma list of metric names to keep")
    a = p.parse_args()
    keys = set(a.keys.split(",")) if a.keys else None
    for i, lg in enumerate(a.logs):
        if i:
            print("-" * 60)
        print(digest(lg, a.max_lines, keys))


if __name__ == "__main__":
    sys.exit(main())
