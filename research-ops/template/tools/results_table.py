#!/usr/bin/env python3
"""Aggregate many result.json files into one markdown table (mean ± std over seeds).

    python3 tools/results_table.py --tag E3 --metrics dice,hd95
    python3 tools/results_table.py --grep b10 --group-by cfg.config,cfg.budget --sort dice

result.json (written by tools/rlog.py) looks like
    {"exp": "E3", "name": "B2-b10-s0", "seed": 0, "config": {...}, "metrics": {"dice": 0.91}}
Files without a "metrics" key use their numeric top-level fields instead.
Default grouping: run name with a trailing seed suffix (-s0, _seed1) removed.
"""
import argparse
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from digest_log import direction  # noqa: E402

SEED_RE = re.compile(r"[-_]s(?:eed)?\d+$")


def _get(d, dotted):
    for part in dotted.split("."):
        if not isinstance(d, dict) or part not in d:
            return None
        d = d[part]
    return d


def load(root, pattern="**/result.json", tag=None, grep=None):
    rows = []
    for p in sorted(Path(root).glob(pattern)):
        try:
            d = json.loads(p.read_text())
        except (ValueError, OSError):
            continue
        name = d.get("name") or p.parent.name
        if tag and d.get("exp") != tag and tag not in (d.get("tags") or []):
            continue
        if grep and grep not in name:
            continue
        metrics = d.get("metrics")
        if not isinstance(metrics, dict):
            metrics = d
        metrics = {k: float(v) for k, v in metrics.items()
                   if isinstance(v, (int, float)) and not isinstance(v, bool)}
        rows.append({"name": name, "seed": d.get("seed"), "cfg": d.get("config") or {}, "exp": d.get("exp"),
                     "metrics": metrics, "path": str(p)})
    return rows


def aggregate(rows, group_by=None, metrics=None):
    if not metrics:
        counts = {}
        for r in rows:
            for k in r["metrics"]:
                counts[k] = counts.get(k, 0) + 1
        metrics = sorted(counts, key=lambda k: -counts[k])[:6]
    groups = {}
    for r in rows:
        key = " / ".join(str(_get(r, k)) for k in group_by) if group_by else SEED_RE.sub("", r["name"])
        g = groups.setdefault(key, {"key": key, "n": 0, "vals": {m: [] for m in metrics}})
        g["n"] += 1
        for m in metrics:
            if m in r["metrics"]:
                g["vals"][m].append(r["metrics"][m])
    for g in groups.values():
        g["stats"] = {}
        for m, vals in g["vals"].items():
            if vals:
                mean = sum(vals) / len(vals)
                std = math.sqrt(sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)) if len(vals) > 1 else None
                g["stats"][m] = (mean, std, len(vals))
    return list(groups.values())


def _fmt(mean, std):
    nd = 3 if abs(mean) < 10 else 1
    return f"{mean:.{nd}f}" + (f" ± {std:.{nd}f}" if std is not None else "")


def render(groups, metrics, sort=None, csv=False):
    metrics = metrics or sorted({m for g in groups for m in g["stats"]})
    if sort:
        lower = direction(sort) == "min"
        missing = math.inf if lower else -math.inf
        groups = sorted(groups, key=lambda g: g["stats"].get(sort, (missing,))[0], reverse=not lower)
    if csv:
        lines = ["group,n," + ",".join(f"{m}_mean,{m}_std" for m in metrics)]
        for g in groups:
            cells = []
            for m in metrics:
                mean, std, _ = g["stats"].get(m, (None, None, 0))
                cells += ["" if mean is None else f"{mean:.6g}", "" if std is None else f"{std:.6g}"]
            lines.append(f"{g['key']},{g['n']}," + ",".join(cells))
        return "\n".join(lines)
    lines = ["| group | n | " + " | ".join(metrics) + " |", "|---|---|" + "---|" * len(metrics)]
    for g in groups:
        cells = [_fmt(*g["stats"][m][:2]) if m in g["stats"] else "-" for m in metrics]
        lines.append(f"| {g['key']} | {g['n']} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default="runs")
    p.add_argument("--glob", default="**/result.json")
    p.add_argument("--tag", help="keep results whose exp (or tags) equals this, e.g. E3")
    p.add_argument("--grep", help="keep runs whose name contains this text")
    p.add_argument("--group-by", help="comma list of fields, e.g. cfg.config,cfg.budget,exp")
    p.add_argument("--metrics", help="comma list; default = the 6 most common")
    p.add_argument("--sort", help="metric to sort by (direction guessed from its name)")
    p.add_argument("--csv", action="store_true")
    a = p.parse_args()
    rows = load(a.root, a.glob, a.tag, a.grep)
    if not rows:
        sys.exit(f"no result files under {a.root}/{a.glob}")
    metrics = a.metrics.split(",") if a.metrics else None
    groups = aggregate(rows, a.group_by.split(",") if a.group_by else None, metrics)
    print(render(groups, metrics, a.sort, a.csv))
    print(f"\n{len(rows)} runs in {len(groups)} groups")


if __name__ == "__main__":
    main()
