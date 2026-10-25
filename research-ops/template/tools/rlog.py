"""Minimal run logger so every experiment leaves the same three files behind.

    import sys; sys.path.insert(0, "tools")   # or add tools/ to PYTHONPATH
    from rlog import RunLogger
    log = RunLogger("runs/E3-B2-b10-s0", exp="E3", config=vars(args), seed=args.seed)
    log.log(step=100, split="val", dice=0.91, hd95=3.2)   # metrics.jsonl + one stdout line
    log.result(dice=0.912, hd95=3.1)                      # result.json (read by results_table.py)

Files: config.json (config + git commit + gpuq job id + host), metrics.jsonl,
result.json. The stdout line uses key=value so digest_log.py can parse it.
"""
import json
import os
import socket
import subprocess
import time
from pathlib import Path


def _git_commit():
    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5)
        if head.returncode != 0:
            return None
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, timeout=5).stdout.strip()
        return head.stdout.strip() + ("-dirty" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        return None


def _jsonable(v):
    try:
        json.dumps(v)
        return v
    except TypeError:
        return str(v)


class RunLogger:
    def __init__(self, run_dir, exp=None, config=None, seed=None, name=None):
        self.dir = Path(run_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.exp, self.seed, self.name = exp, seed, name or self.dir.name
        self.t0 = time.time()
        self.config = {k: _jsonable(v) for k, v in (config or {}).items()}
        meta = {"exp": exp, "name": self.name, "seed": seed, "config": self.config, "git": _git_commit(),
                "gpuq_job": os.environ.get("GPUQ_JOB_ID"), "host": socket.gethostname(),
                "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
                "start": time.strftime("%Y-%m-%d %H:%M:%S")}
        (self.dir / "config.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False))

    def log(self, step=None, **metrics):
        rec = {"t": round(time.time() - self.t0, 1), "step": step}
        rec.update({k: _jsonable(v) for k, v in metrics.items()})
        with open(self.dir / "metrics.jsonl", "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print("[metrics] " + " ".join(f"{k}={v:.5g}" if isinstance(v, float) else f"{k}={v}"
                                      for k, v in rec.items() if v is not None and k != "t"), flush=True)

    def result(self, **metrics):
        out = {"exp": self.exp, "name": self.name, "seed": self.seed, "config": self.config,
               "metrics": {k: _jsonable(v) for k, v in metrics.items()},
               "minutes": round((time.time() - self.t0) / 60, 1), "end": time.strftime("%Y-%m-%d %H:%M:%S")}
        tmp = self.dir / "result.json.tmp"
        tmp.write_text(json.dumps(out, indent=1, ensure_ascii=False))
        os.replace(tmp, self.dir / "result.json")
        return out
