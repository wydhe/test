#!/usr/bin/env python3
"""gpuq - a small memory-aware GPU job queue for one multi-GPU Linux server.

Every job declares the GPU memory it needs (--mem, GB per GPU). The daemon
packs jobs onto GPUs using the real free memory reported by nvidia-smi, minus
memory that already-launched jobs have reserved but not allocated yet. Several
small jobs can therefore share one GPU, while big training runs can claim
whole GPUs (--exclusive) and are protected from starvation by draining.

State lives in $GPUQ_HOME (default: <project>/.gpuq), one JSON file per job.
Jobs run detached in their own session: restarting the daemon never kills a
running job, it is re-adopted on the next pass.

Commands (see `gpuq.py <cmd> -h`):
  init | daemon | once              set up / run the scheduler loop / one pass
  submit -n NAME -m GB [...] -- CMD
  batch FILE.jsonl                  many jobs, one JSON object per line
  ls | show ID | cancel ID... | retry ID...
  status | report | doctor | hold GPU... | release GPU...
"""
import argparse
import fcntl
import json
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
HOME = Path(os.environ.get("GPUQ_HOME") or TOOLS_DIR.parent / ".gpuq")
JOBS = HOME / "jobs"
LOGS = HOME / "logs"

DEFAULT_CONFIG = {
    "gpus": None,             # list of GPU indices to manage; null = all
    "safety_gb": 3.0,         # headroom left free on every GPU
    "max_jobs_per_gpu": 6,
    "cpu_slots": None,        # total CPU slots shared by jobs; null = os.cpu_count()
    "default_cpus": 4,        # CPU slots a job takes unless --cpus is given
    "poll_s": 20,
    "accounting": "pid",      # "pid": per-process memory from nvidia-smi; "warmup": time based
    "warmup_s": 300,          # warmup mode: a new job counts as fully allocated for this long
    "oom_mem_factor": 1.3,    # an OOM-killed job is retried with mem * factor
    "default_retries": 2,     # OOM retries; other failures are never retried automatically
    "on_finish": "",          # shell command run (in project_dir) whenever a job ends
    "project_dir": None,      # null = parent of tools/
}

FINAL = ("done", "failed", "cancelled", "dep_failed")
OOM_RE = re.compile(r"CUDA out of memory|OutOfMemoryError|CUBLAS_STATUS_ALLOC_FAILED|"
                    r"CUDNN_STATUS_ALLOC_FAILED|cudaErrorMemoryAllocation", re.I)
ERR_RE = re.compile(r"Error|Exception|Traceback|Killed|Segmentation fault|core dumped|Aborted")


# ---------------------------------------------------------------- storage

def now():
    return time.time()


def ts(t=None):
    return time.strftime("%m-%d %H:%M", time.localtime(t or now()))


def dur(seconds):
    seconds = int(max(0, seconds))
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"
    return f"{seconds // 86400}d{seconds % 86400 // 3600:02d}h"


def write_json(path, obj):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False))
    os.replace(tmp, path)


def ensure_home():
    JOBS.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    cfg_path = HOME / "config.json"
    if not cfg_path.exists():
        cfg = dict(DEFAULT_CONFIG)
        if (TOOLS_DIR / "on_job_finish.sh").exists():
            cfg["on_finish"] = "bash tools/on_job_finish.sh"
        write_json(cfg_path, cfg)


def load_config():
    ensure_home()
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(json.loads((HOME / "config.json").read_text()))
    return cfg


def project_dir(cfg):
    return Path(cfg.get("project_dir") or TOOLS_DIR.parent)


class Lock:
    """Exclusive lock around every read-modify-write of the queue."""

    def __enter__(self):
        ensure_home()
        self.f = open(HOME / "lock", "w")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def id_num(jid):
    return int(jid[1:])


def job_path(jid):
    return JOBS / f"{jid}.json"


def load_job(jid):
    p = job_path(jid)
    if not p.exists():
        sys.exit(f"gpuq: no such job {jid}")
    return json.loads(p.read_text())


def save_job(job):
    write_json(job_path(job["id"]), job)


def all_jobs():
    jobs = []
    for p in JOBS.glob("j*.json"):
        try:
            jobs.append(json.loads(p.read_text()))
        except (ValueError, OSError):
            continue
    return sorted(jobs, key=lambda j: id_num(j["id"]))


def load_held():
    p = HOME / "held.json"
    return json.loads(p.read_text()) if p.exists() else []


def heartbeat_age():
    p = HOME / "daemon.json"
    if not p.exists():
        return None
    try:
        return now() - json.loads(p.read_text())["t"]
    except (ValueError, KeyError, OSError):
        return None


def read_tail(path, nbytes):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - nbytes))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def last_error(text):
    lines = [ln.strip() for ln in re.split(r"[\r\n]+", text) if ln.strip()]
    lines = [ln for ln in lines if not ln.startswith("### ")]
    for ln in reversed(lines):
        if ERR_RE.search(ln):
            return ln[:240]
    return lines[-1][:240] if lines else ""


# ---------------------------------------------------------------- GPUs and processes

def _num(x, default=0.0):
    try:
        return float(x)
    except ValueError:
        return default


def query_gpus(cfg):
    """Return ({index: gpu info in GB}, [(pid, index, used_gb), ...])."""
    fake = os.environ.get("GPUQ_FAKE_GPUS")  # testing only, e.g. "4x80"
    if fake:
        n, gb = fake.lower().split("x")
        used = {}
        for item in filter(None, os.environ.get("GPUQ_FAKE_USED", "").split(",")):
            i, u = item.split(":")
            used[int(i)] = float(u)
        gpus = {i: {"name": "FAKE", "total": float(gb), "used": used.get(i, 0.0), "util": 0.0}
                for i in range(int(n))}
        apps = []
    else:
        def smi(query):
            return subprocess.run(["nvidia-smi", query, "--format=csv,noheader,nounits"],
                                  capture_output=True, text=True, timeout=30, check=True).stdout
        try:
            out = smi("--query-gpu=index,uuid,name,memory.total,memory.used,utilization.gpu")
        except (OSError, subprocess.SubprocessError) as e:
            raise RuntimeError(f"nvidia-smi failed: {e}")
        gpus, uuid2idx = {}, {}
        for line in out.strip().splitlines():
            idx, uuid, name, total, used, util = [x.strip() for x in line.split(",")]
            gpus[int(idx)] = {"name": name, "total": _num(total) / 1024,
                              "used": _num(used) / 1024, "util": _num(util)}
            uuid2idx[uuid] = int(idx)
        apps = []
        try:
            for line in smi("--query-compute-apps=pid,gpu_uuid,used_memory").strip().splitlines():
                pid, uuid, mem = [x.strip() for x in line.split(",")]
                if uuid in uuid2idx:
                    apps.append((int(pid), uuid2idx[uuid], _num(mem) / 1024))
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    if cfg.get("gpus") is not None:
        gpus = {i: g for i, g in gpus.items() if i in cfg["gpus"]}
    return gpus, apps


def proc_stat(pid):
    """(state, pgrp, start_ticks) from /proc, or None if the process is gone."""
    try:
        data = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    rest = data[data.rfind(")") + 2:].split()
    return rest[0], int(rest[2]), int(rest[19])


def job_alive(job):
    if not job.get("pid"):
        return False
    st = proc_stat(job["pid"])
    return st is not None and st[0] != "Z" and st[2] == job.get("start_ticks")


def reap_children():
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def gpu_view(cfg, jobs, gpus, apps):
    """Per-GPU availability after subtracting what running jobs reserved but haven't allocated."""
    running = [j for j in jobs if j["status"] == "running"]
    by_pgid = {j["pgid"]: j for j in running if j.get("pgid")}
    actual, ext, invisible = {}, {i: 0 for i in gpus}, 0
    for pid, gi, gb in apps:
        if gi not in gpus:
            continue
        st = proc_stat(pid)
        if st is None:
            invisible += 1
            ext[gi] += 1
            continue
        j = by_pgid.get(st[1])
        if j:
            actual[(j["id"], gi)] = actual.get((j["id"], gi), 0.0) + gb
        else:
            ext[gi] += 1
    held, t = set(load_held()), now()
    view = {}
    for i, g in gpus.items():
        mine = [j for j in running if i in j["gpus"]]
        pending = reclaim = 0.0
        for j in mine:
            got = actual.get((j["id"], i), 0.0)
            if cfg["accounting"] == "pid":
                pending += max(0.0, j["mem"] - got)
            elif t - (j.get("started") or 0) < cfg["warmup_s"]:
                pending += j["mem"]
            reclaim += max(j["mem"], got)
        view[i] = dict(g, pending=pending, reclaim=reclaim, ext=ext[i], held=i in held,
                       avail=g["total"] - g["used"] - cfg["safety_gb"] - pending,
                       jobs=[j["id"] for j in mine],
                       exclusive=any(j.get("exclusive") for j in mine))
    return view, actual, invisible


# ---------------------------------------------------------------- scheduling

def deps_state(job, by_id):
    """True when all dependencies are done, False while waiting, 'failed' if one failed."""
    for d in job.get("after", []):
        dj = by_id.get(d)
        if dj is None or dj["status"] in ("failed", "cancelled", "dep_failed"):
            return "failed"
        if dj["status"] != "done":
            return False
    return True


def usable(cfg, job, i, v):
    if v["held"] or v["exclusive"]:
        return False
    if job.get("only") and i not in job["only"]:
        return False
    if job.get("exclusive") and v["jobs"]:
        return False
    return len(v["jobs"]) < cfg["max_jobs_per_gpu"]


def place(cfg, job, view, drained):
    cands = [i for i, v in view.items()
             if i not in drained and usable(cfg, job, i, v) and v["avail"] >= job["mem"]]
    if len(cands) < job["ngpu"]:
        return None
    # Spread over the least loaded GPUs (compute is shared), most free memory first.
    cands.sort(key=lambda i: (len(view[i]["jobs"]) + view[i]["ext"], -view[i]["avail"]))
    return sorted(cands[:job["ngpu"]])


def drain_targets(cfg, job, view, drained):
    """GPUs to keep free for a blocked big job so small jobs cannot starve it."""
    cands = [i for i, v in view.items()
             if i not in drained and not v["held"]
             and (not job.get("only") or i in job["only"])
             and v["avail"] + v["reclaim"] >= job["mem"]]
    if len(cands) < job["ngpu"]:
        return set()
    cands.sort(key=lambda i: (len(view[i]["jobs"]), -(view[i]["avail"] + view[i]["reclaim"])))
    return set(cands[:job["ngpu"]])


def launch(cfg, job, gpus):
    log, exitf = LOGS / f"{job['id']}.log", LOGS / f"{job['id']}.exit"
    if exitf.exists():
        exitf.unlink()
    env = os.environ.copy()
    env["OMP_NUM_THREADS"] = str(max(1, int(job.get("cpus") or 1)))
    env.update(job.get("env") or {})
    env.update(CUDA_DEVICE_ORDER="PCI_BUS_ID", CUDA_VISIBLE_DEVICES=",".join(map(str, gpus)),
               GPUQ_JOB_ID=job["id"], GPUQ_JOB_NAME=job["name"], GPUQ_TAG=job.get("tag", ""),
               PYTHONUNBUFFERED="1")
    # The subshell keeps `exit` inside the user command from skipping the exit-code file.
    script = f"(\n{job['cmd']}\n)\nec=$?\necho $ec > {shlex.quote(str(exitf))}\nexit $ec\n"
    with open(log, "ab") as lf:
        lf.write(f"### gpuq {job['id']} {job['name']} start {ts()} host={socket.gethostname()} "
                 f"gpus={gpus} mem={job['mem']}G attempt={job['attempt']}\n"
                 f"### cmd: {job['cmd']}\n".encode())
        lf.flush()
        try:
            p = subprocess.Popen(["bash", "-c", script], cwd=job["cwd"], env=env,
                                 stdin=subprocess.DEVNULL, stdout=lf, stderr=subprocess.STDOUT,
                                 start_new_session=True)
        except OSError as e:
            job.update(status="failed", ended=now(), err=f"launch failed: {e}", log=str(log))
            save_job(job)
            run_hook(cfg, job)
            return False
    st = proc_stat(p.pid)
    job.update(status="running", gpus=gpus, pid=p.pid, pgid=p.pid, started=now(), log=str(log),
               start_ticks=st[2] if st else None)
    save_job(job)
    return True


def run_hook(cfg, job):
    cmd = cfg.get("on_finish")
    if not cmd:
        return
    env = os.environ.copy()
    env.update(GPUQ_JOB_ID=job["id"], GPUQ_STATUS=job["status"], GPUQ_JOB_NAME=job["name"],
               GPUQ_TAG=job.get("tag", ""), GPUQ_JOB_FILE=str(job_path(job["id"])),
               GPUQ_HOME=str(HOME))
    with open(LOGS / "on_finish.log", "ab") as lf:
        subprocess.Popen(["bash", "-c", cmd], cwd=str(project_dir(cfg)), env=env,
                         stdin=subprocess.DEVNULL, stdout=lf, stderr=subprocess.STDOUT,
                         start_new_session=True)


def finish(cfg, job, ec, mem_cap):
    job["ended"], job["exit"] = now(), ec
    tail = read_tail(job.get("log", ""), 65536)
    if job.get("cancel_req"):
        job["status"] = "cancelled"
    elif ec == 0:
        job["status"] = "done"
    else:
        oom = bool(OOM_RE.search(tail))
        err = last_error(tail) or (f"exit {ec}" if ec is not None else "process vanished")
        if oom and job["attempt"] < job["retries"]:
            old = job["mem"]
            job["mem"] = round(min(old * cfg["oom_mem_factor"], mem_cap or old * 2), 1)
            job.setdefault("history", []).append({"t": now(), "event": "oom", "mem": old})
            job.update(status="queued", attempt=job["attempt"] + 1, pid=None, pgid=None, gpus=[],
                       err=f"OOM at {old}G, retrying with {job['mem']}G")
            save_job(job)
            return
        job["status"], job["err"] = "failed", ("OOM: " if oom else "") + err
    save_job(job)
    run_hook(cfg, job)


def one_pass(verbose=False):
    cfg = load_config()
    with Lock():
        gpus, apps = query_gpus(cfg)
        mem_cap = max((g["total"] for g in gpus.values()), default=0) - cfg["safety_gb"]
        jobs = all_jobs()

        reap_children()
        finished = []
        for job in jobs:
            if job["status"] == "running" and not job_alive(job):
                exitf = LOGS / f"{job['id']}.exit"
                try:
                    ec = int(exitf.read_text().strip())
                except (OSError, ValueError):
                    ec = None
                finish(cfg, job, ec, mem_cap)
                finished.append((job, "oom-retry" if job["status"] == "queued" else job["status"]))

        view, _, _ = gpu_view(cfg, jobs, gpus, apps)
        by_id = {j["id"]: j for j in jobs}
        queued = sorted((j for j in jobs if j["status"] == "queued"),
                        key=lambda j: (-j.get("prio", 0), id_num(j["id"])))
        cpu_slots = cfg.get("cpu_slots") or os.cpu_count() or 1
        cpu_used = sum(j.get("cpus", 0) for j in jobs if j["status"] == "running")
        drained, drained_for, started = set(), None, []
        for job in queued:
            dep = deps_state(job, by_id)
            if dep == "failed":
                job.update(status="dep_failed", ended=now(), err="a dependency failed or was cancelled")
                save_job(job)
                run_hook(cfg, job)
                finished.append((job, "dep_failed"))
                continue
            if not dep or (cpu_used and cpu_used + job.get("cpus", 0) > cpu_slots):
                continue
            picked = place(cfg, job, view, drained)
            if picked is None:
                big = job.get("exclusive") or job["ngpu"] > 1 or job["mem"] >= 0.5 * (mem_cap or 1)
                if big and drained_for is None:
                    drained = drain_targets(cfg, job, view, drained)
                    drained_for = job["id"] if drained else None
                continue
            if launch(cfg, job, picked):
                cpu_used += job.get("cpus", 0)
                for i in picked:
                    view[i]["avail"] -= job["mem"]
                    view[i]["reclaim"] += job["mem"]
                    view[i]["jobs"].append(job["id"])
                    view[i]["exclusive"] = view[i]["exclusive"] or bool(job.get("exclusive"))
                started.append(job)
        write_json(HOME / "sched.json", {"t": now(), "drained": sorted(drained), "for": drained_for})

    if verbose:
        for j, status in finished:
            print(ts(), "end  ", j["id"], j["name"], status, j.get("err") or "", flush=True)
        for j in started:
            print(ts(), "start", j["id"], j["name"], "gpus", j["gpus"], f"mem {j['mem']}G", flush=True)
    return finished, started


# ---------------------------------------------------------------- commands

BACKGROUND_RE = re.compile(r"\bnohup\b|\bsetsid\b|\bdisown\b|(?<![&>|])&(?![&>])")


def new_job(cfg, cmd, name, mem, ngpu=1, exclusive=False, prio=0, tag="", note="", after=None,
            retries=None, cwd=None, env=None, cpus=None, only=None):
    if float(mem) <= 0 or int(ngpu) < 1:
        raise ValueError("mem must be > 0 and ngpu >= 1")
    # A backgrounded command "finishes" at once and its GPU use becomes invisible to the queue.
    if BACKGROUND_RE.search(cmd):
        raise ValueError(f"{name}: remove nohup / setsid / '&' - gpuq already runs jobs detached and logs them")
    if re.search(r"\bCUDA_VISIBLE_DEVICES\s*=", cmd):
        raise ValueError(f"{name}: remove CUDA_VISIBLE_DEVICES - gpuq assigns GPUs itself")
    return {
        "id": None, "name": name, "cmd": cmd, "cwd": str(Path(cwd or os.getcwd()).resolve()),
        "mem": float(mem), "ngpu": int(ngpu), "exclusive": bool(exclusive), "prio": int(prio),
        "tag": tag or "", "note": note or "", "after": list(after or []),
        "retries": cfg["default_retries"] if retries is None else int(retries), "attempt": 0,
        "cpus": cfg["default_cpus"] if cpus is None else int(cpus), "only": list(only or []),
        "env": dict(env or {}), "status": "queued", "submitted": now(), "gpus": [], "pid": None,
    }


def resolve_after(items, jobs):
    """Accept job ids, job names (latest job with that name) and tag:<TAG> (all jobs of a tag)."""
    out = []
    for it in items:
        it = str(it).strip()
        if not it:
            continue
        if re.fullmatch(r"j\d+", it):
            if not any(j["id"] == it for j in jobs):
                raise ValueError(f"unknown dependency {it}")
            out.append(it)
        elif it.startswith("tag:"):
            ids = [j["id"] for j in jobs if j.get("tag") == it[4:]]
            if not ids:
                raise ValueError(f"no jobs with {it}")
            out += ids
        else:
            named = [j for j in jobs if j["name"] == it]
            if not named:
                raise ValueError(f"unknown dependency {it}")
            out.append(named[-1]["id"])
    return out


def add_jobs(specs):
    """Save new jobs atomically; 'after' may name jobs submitted earlier in the same list."""
    with Lock():
        jobs = all_jobs()
        n = max((id_num(j["id"]) for j in jobs), default=0)
        for job in specs:
            job["after"] = resolve_after(job["after"], jobs)
            n += 1
            job["id"] = f"j{n:04d}"
            jobs.append(job)
        for job in specs:
            save_job(job)
    return specs


def warn_if_no_daemon(cfg):
    age = heartbeat_age()
    if age is None or age > 3 * cfg["poll_s"] + 30:
        print("gpuq: WARNING daemon is not running; start it with:\n"
              "  tmux new -d -s gpuq 'python3 tools/gpuq.py daemon >> .gpuq/daemon.log 2>&1'",
              file=sys.stderr)


def cmd_submit(a):
    cfg = load_config()
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if not cmd:
        sys.exit("gpuq: missing command after --")
    cmd = cmd[0] if len(cmd) == 1 else " ".join(shlex.quote(c) for c in cmd)
    env = dict(kv.split("=", 1) for kv in a.env)
    only = [int(x) for x in a.only.split(",")] if a.only else None
    try:
        job = new_job(cfg, cmd, a.name, a.mem, a.ngpu, a.exclusive, a.prio, a.tag, a.note,
                      a.after.split(",") if a.after else [], a.retries, a.cwd, env, a.cpus, only)
        (job,) = add_jobs([job])
    except ValueError as e:
        sys.exit(f"gpuq: {e}")
    print(job["id"])
    warn_if_no_daemon(cfg)


BATCH_KEYS = {"cmd", "name", "mem", "ngpu", "exclusive", "prio", "tag", "note", "after",
              "retries", "cwd", "env", "cpus", "only"}


def cmd_batch(a):
    cfg = load_config()
    specs = []
    lines = sys.stdin.read().splitlines() if a.file == "-" else Path(a.file).read_text().splitlines()
    for k, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            d = json.loads(line)
            unknown = set(d) - BATCH_KEYS
            if unknown:
                raise ValueError(f"unknown keys {sorted(unknown)}")
            after = d.get("after") or []
            d["after"] = after.split(",") if isinstance(after, str) else after
            specs.append(new_job(cfg, **d))
        except (ValueError, TypeError) as e:
            sys.exit(f"gpuq: line {k}: {e}")
    try:
        added = add_jobs(specs)
    except ValueError as e:
        sys.exit(f"gpuq: {e} (nothing submitted)")
    print(" ".join(j["id"] for j in added))
    warn_if_no_daemon(cfg)


def recent(job, hours):
    return job["status"] not in FINAL or (now() - (job.get("ended") or job["submitted"])) < hours * 3600


def job_row(j):
    t = now()
    if j["status"] == "running":
        when = dur(t - j["started"])
    elif j["status"] in FINAL and j.get("started") and j.get("ended"):
        when = dur(j["ended"] - j["started"])
    else:
        when = "-" + dur(t - j["submitted"])
    gpu = ",".join(map(str, j.get("gpus") or [])) or "-"
    extra = j.get("err") if j["status"] in ("failed", "dep_failed") else j.get("note", "")
    return (f"{j['id']} {j['name'][:28]:<28} {j.get('tag', '')[:8]:<8} {j['status'][:7]:<7} "
            f"g{gpu:<5} {j['mem']:>5.1f}G {when:>7}  {(extra or '')[:70]}")


def cmd_ls(a):
    jobs = all_jobs()
    if a.tag:
        jobs = [j for j in jobs if j.get("tag") == a.tag]
    if a.status:
        jobs = [j for j in jobs if j["status"] in a.status.split(",")]
    elif not a.all:
        jobs = [j for j in jobs if recent(j, 24)]
    if a.count:
        print(len(jobs))
        return
    for j in jobs[-a.limit:]:
        print(job_row(j))


def cmd_show(a):
    job = load_job(a.id)
    for k in ("id", "name", "tag", "status", "cmd", "cwd", "mem", "ngpu", "exclusive", "prio",
              "cpus", "after", "attempt", "retries", "gpus", "exit", "err", "note", "log"):
        if job.get(k) not in (None, "", [], {}):
            print(f"{k:>9}: {job[k]}")
    for k in ("submitted", "started", "ended"):
        if job.get(k):
            print(f"{k:>9}: {ts(job[k])}")
    if job.get("log") and not a.no_digest:
        sys.path.insert(0, str(TOOLS_DIR))
        try:
            from digest_log import digest
            print("--- digest ---")
            print(digest(job["log"], max_lines=20))
        except ImportError:
            pass


def cmd_cancel(a):
    with Lock():
        jobs = all_jobs()
        targets = [j for j in jobs if j["id"] in a.ids or (a.tag and j.get("tag") == a.tag)]
        for job in targets:
            if job["status"] == "queued":
                job.update(status="cancelled", ended=now())
            elif job["status"] == "running":
                job["cancel_req"] = True
                try:
                    os.killpg(job["pgid"], signal.SIGKILL if a.kill else signal.SIGTERM)
                except (ProcessLookupError, PermissionError, TypeError):
                    pass
            else:
                continue
            save_job(job)
            print("cancel", job["id"], job["name"])


def cmd_retry(a):
    with Lock():
        for jid in a.ids:
            job = load_job(jid)
            if job["status"] not in ("failed", "cancelled", "dep_failed"):
                print(f"skip {jid}: status {job['status']}")
                continue
            job.setdefault("history", []).append({"t": now(), "event": "retry", "was": job["status"]})
            for k in ("pid", "pgid", "start_ticks", "started", "ended", "exit", "err", "cancel_req"):
                job.pop(k, None)
            job.update(status="queued", attempt=0, gpus=[], submitted=now())
            if a.mem:
                job["mem"] = a.mem
            save_job(job)
            print("requeued", jid)


def gpu_lines(view, jobs_by_id):
    out = []
    for i, v in sorted(view.items()):
        names = ",".join(jobs_by_id[x]["name"][:14] for x in v["jobs"] if x in jobs_by_id)
        flags = (f" +{v['ext']}ext" if v["ext"] else "") + (" HELD" if v["held"] else "") + \
                (" EXCL" if v["exclusive"] else "")
        out.append(f"GPU{i} {v['used']:>4.0f}/{v['total']:.0f}G util{v['util']:>4.0f}% "
                   f"free~{max(v['avail'], 0):>3.0f}G {len(v['jobs'])}job{flags}  {names[:60]}")
    return out


def cmd_status(a):
    cfg = load_config()
    jobs = all_jobs()
    by_id = {j["id"]: j for j in jobs}
    gpu_err = None
    try:
        gpus, apps = query_gpus(cfg)
        view, _, _ = gpu_view(cfg, jobs, gpus, apps)
    except RuntimeError as e:
        view, gpu_err = {}, str(e)
    age = heartbeat_age()
    up = age is not None and age < 3 * cfg["poll_s"] + 30
    running = [j for j in jobs if j["status"] == "running"]
    queued = [j for j in jobs if j["status"] == "queued"]
    ready = [j for j in queued if deps_state(j, by_id) is True]
    day = [j for j in jobs if j["status"] in FINAL and now() - (j.get("ended") or 0) < 86400]
    failed = [j for j in day if j["status"] in ("failed", "dep_failed")]
    busy = sum(1 for v in view.values() if v["jobs"] or v["ext"] or v["util"] > 30)

    if a.line:
        print(f"GPU {busy}/{len(view)} busy · run {len(running)} · q {len(queued)} · "
              f"fail24h {len(failed)} · gpuq {'ok' if up else 'DOWN'}")
        return

    print(f"== {ts()} | daemon {'up' if up else 'DOWN (start: tmux new -d -s gpuq python3 tools/gpuq.py daemon)'}")
    if gpu_err:
        print("GPU: unavailable:", gpu_err)
    print("\n".join(gpu_lines(view, by_id)))
    print(f"queue: running {len(running)} | queued {len(queued)} (ready {len(ready)}) | "
          f"done24h {sum(1 for j in day if j['status'] == 'done')} | failed24h {len(failed)}")

    idle = [i for i, v in view.items() if not v["jobs"] and not v["ext"] and v["util"] < 10 and not v["held"]]
    if idle and not ready:
        print(f"IDLE: GPU{','.join(map(str, idle))} idle and no runnable job queued -> schedule more work")
    cap = max((v["total"] for v in view.values()), default=0) - cfg["safety_gb"]
    for j in ready:
        if view and j["mem"] > cap:
            print(f"WARN: {j['id']} {j['name']} needs {j['mem']}G > per-GPU capacity {cap:.0f}G")
    sched = HOME / "sched.json"
    if sched.exists():
        s = json.loads(sched.read_text())
        if s.get("drained"):
            print(f"DRAIN: GPU{','.join(map(str, s['drained']))} kept free for {s.get('for')}")

    if running:
        sys.path.insert(0, str(TOOLS_DIR))
        try:
            from digest_log import last_metrics
        except ImportError:
            def last_metrics(_path):
                return ""
        print("RUNNING")
        for j in running[: a.limit]:
            print(f" {j['id']} {j['name'][:26]:<26} {j.get('tag', '')[:8]:<8} g{','.join(map(str, j['gpus']))} "
                  f"{dur(now() - j['started']):>6}  {last_metrics(j.get('log', ''))[:70]}")
        if len(running) > a.limit:
            print(f" ... +{len(running) - a.limit} more (gpuq.py ls --status running)")
    if failed:
        print("FAILED (24h)")
        for j in failed[-a.limit:]:
            print(f" {j['id']} {j['name'][:26]:<26} {j.get('tag', '')[:8]:<8} {(j.get('err') or '')[:80]}")
    if queued and not a.brief:
        print(f"QUEUED (next {min(5, len(queued))} of {len(queued)})")
        for j in sorted(queued, key=lambda j: (-j.get("prio", 0), id_num(j["id"])))[:5]:
            wait = "" if j in ready else " waits:" + ",".join(j.get("after", []))[:30]
            print(f" {j['id']} {j['name'][:26]:<26} {j.get('tag', '')[:8]:<8} {j['mem']:.0f}G x{j['ngpu']}"
                  f"{' excl' if j.get('exclusive') else ''} prio{j.get('prio', 0)}{wait}")


def cmd_report(a):
    cfg = load_config()
    t1 = now()
    t0 = t1 - a.days * 86400
    rows = {}
    for j in all_jobs():
        if not j.get("started"):
            continue
        s, e = max(j["started"], t0), min(j.get("ended") or t1, t1)
        if e <= s:
            continue
        r = rows.setdefault(j.get("tag") or "-", {"gpuh": 0.0, "waste": 0.0, "done": 0, "failed": 0, "other": 0})
        h = (e - s) / 3600 * j["ngpu"]
        r["gpuh"] += h
        if j["status"] == "done":
            r["done"] += 1
        elif j["status"] in ("failed", "dep_failed"):
            r["failed"] += 1
            r["waste"] += h
        else:
            r["other"] += 1
    try:
        ngpu = len(query_gpus(cfg)[0])
    except RuntimeError:
        ngpu = len(cfg.get("gpus") or [])
    cap = ngpu * 24 * a.days
    total = sum(r["gpuh"] for r in rows.values())
    print(f"last {a.days:g}d: job-GPU-hours {total:.0f} / capacity {cap:.0f} GPU-h"
          f" (packing lets several jobs share a GPU, so this can exceed 100%)")
    print(f"{'tag':<10}{'GPU-h':>8}{'failed-GPU-h':>14}{'done':>6}{'failed':>8}{'other':>7}")
    for tag, r in sorted(rows.items(), key=lambda kv: -kv[1]["gpuh"]):
        print(f"{tag:<10}{r['gpuh']:>8.1f}{r['waste']:>14.1f}{r['done']:>6}{r['failed']:>8}{r['other']:>7}")


def cmd_doctor(a):
    cfg = load_config()
    print("home:", HOME, "| project:", project_dir(cfg))
    print("config:", json.dumps(cfg, ensure_ascii=False))
    print("cpu_slots:", cfg.get("cpu_slots") or os.cpu_count())
    age = heartbeat_age()
    print("daemon:", "never ran" if age is None else f"last heartbeat {age:.0f}s ago")
    try:
        gpus, apps = query_gpus(cfg)
    except RuntimeError as e:
        print("GPU: FAIL", e)
        return
    print(f"GPUs: {len(gpus)} ->", ", ".join(f"{i}:{g['name']} {g['total']:.0f}G" for i, g in gpus.items()))
    jobs = all_jobs()
    view, actual, invisible = gpu_view(cfg, jobs, gpus, apps)
    print(f"compute processes: {len(apps)} (not visible in /proc: {invisible})")
    if invisible:
        print("WARN: some GPU processes are in another PID namespace (container?). Per-job memory can't be"
              " measured; set \"accounting\": \"warmup\" in .gpuq/config.json")
    for (jid, gi), gb in sorted(actual.items()):
        print(f" measured {jid} on GPU{gi}: {gb:.1f}G")
    print("\n".join(gpu_lines(view, {j["id"]: j for j in jobs})))


def cmd_hold(a):
    with Lock():
        held = set(load_held())
        if a.release:
            held = set() if "all" in a.gpus else held - {int(g) for g in a.gpus}
        else:
            held |= {int(g) for g in a.gpus}
        write_json(HOME / "held.json", sorted(held))
    print("held GPUs:", sorted(held) or "none")


def cmd_daemon(a):
    load_config()
    lockf = open(HOME / "daemon.lock", "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit("gpuq: another daemon is already running")
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    signal.signal(signal.SIGINT, lambda *_: stop.append(1))
    print(ts(), f"gpuq daemon pid {os.getpid()} home {HOME}", flush=True)
    while not stop:
        try:
            one_pass(verbose=True)
        except Exception:  # keep scheduling even if one pass hits a transient error
            traceback.print_exc()
            sys.stdout.flush()
        write_json(HOME / "daemon.json", {"t": now(), "pid": os.getpid()})
        for _ in range(int(load_config()["poll_s"] * 2)):
            if stop:
                break
            time.sleep(0.5)
    print(ts(), "gpuq daemon stopped (running jobs keep running)", flush=True)


def main():
    p = argparse.ArgumentParser(prog="gpuq", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd_name")
    sub.required = True

    sub.add_parser("init", help="create .gpuq/ and default config")
    sub.add_parser("daemon", help="run the scheduler loop (put it in tmux)")
    sub.add_parser("once", help="run a single scheduling pass")

    s = sub.add_parser("submit", help="queue one job: submit -n NAME -m GB -- CMD ...")
    s.add_argument("-n", "--name", required=True)
    s.add_argument("-m", "--mem", type=float, required=True, help="peak GPU memory per GPU, GB")
    s.add_argument("-g", "--ngpu", type=int, default=1)
    s.add_argument("-x", "--exclusive", action="store_true", help="no other gpuq job on its GPUs")
    s.add_argument("-p", "--prio", type=int, default=0, help="higher runs first")
    s.add_argument("-t", "--tag", default="", help="experiment group, e.g. E3")
    s.add_argument("--note", default="")
    s.add_argument("-a", "--after", default="", help="comma list: job ids, job names or tag:TAG")
    s.add_argument("--retries", type=int, default=None, help="OOM retries")
    s.add_argument("--cpus", type=int, default=None, help="CPU slots (also sets OMP_NUM_THREADS)")
    s.add_argument("--only", default="", help="restrict to these GPUs, e.g. 0,1")
    s.add_argument("--cwd", default=None)
    s.add_argument("-e", "--env", action="append", default=[], help="KEY=VALUE, repeatable")
    s.add_argument("cmd", nargs=argparse.REMAINDER)

    s = sub.add_parser("batch", help="queue jobs from a JSONL file ('-' = stdin)")
    s.add_argument("file")

    s = sub.add_parser("ls", help="list jobs (default: active + last 24h)")
    s.add_argument("--all", action="store_true")
    s.add_argument("--tag")
    s.add_argument("--status", help="comma list, e.g. running,queued")
    s.add_argument("--count", action="store_true")
    s.add_argument("--limit", type=int, default=60)

    s = sub.add_parser("show", help="job details + log digest")
    s.add_argument("id")
    s.add_argument("--no-digest", action="store_true")

    s = sub.add_parser("cancel", help="cancel queued/running jobs")
    s.add_argument("ids", nargs="*")
    s.add_argument("--tag")
    s.add_argument("--kill", action="store_true", help="SIGKILL instead of SIGTERM")

    s = sub.add_parser("retry", help="requeue failed/cancelled jobs")
    s.add_argument("ids", nargs="+")
    s.add_argument("--mem", type=float)

    s = sub.add_parser("status", help="one-screen summary for humans and Claude")
    s.add_argument("--brief", action="store_true")
    s.add_argument("--line", action="store_true", help="single line (status bar)")
    s.add_argument("--limit", type=int, default=12)

    s = sub.add_parser("report", help="GPU-hours per tag")
    s.add_argument("--days", type=float, default=7)

    sub.add_parser("doctor", help="check nvidia-smi, accounting mode, daemon")

    s = sub.add_parser("hold", help="keep GPUs free for manual use")
    s.add_argument("gpus", nargs="+")
    s = sub.add_parser("release", help="undo hold ('all' releases every GPU)")
    s.add_argument("gpus", nargs="+")

    a = p.parse_args()
    if a.cmd_name == "init":
        ensure_home()
        print("gpuq home:", HOME)
    elif a.cmd_name == "once":
        one_pass(verbose=True)
    elif a.cmd_name in ("hold", "release"):
        a.release = a.cmd_name == "release"
        cmd_hold(a)
    else:
        globals()["cmd_" + a.cmd_name](a)


if __name__ == "__main__":
    main()
