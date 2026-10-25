#!/usr/bin/env python3
"""Tests for gpuq / digest_log / results_table using fake GPUs (no CUDA needed).

    python3 research-ops/tests/test_tools.py -v
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "template" / "tools"
GPUQ = [sys.executable, str(TOOLS / "gpuq.py")]
FINAL = ("done", "failed", "cancelled", "dep_failed")


class GpuqCase(unittest.TestCase):
    ngpu, gb = 4, 80
    config = {"cpu_slots": 64, "safety_gb": 3.0}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.env = dict(os.environ, GPUQ_HOME=str(self.dir / ".gpuq"),
                        GPUQ_FAKE_GPUS=f"{self.ngpu}x{self.gb}")
        self.env.pop("GPUQ_FAKE_USED", None)
        self.q("init")
        self.set_config(project_dir=str(self.dir), on_finish="", **self.config)

    def tearDown(self):
        for j in self.jobs().values():
            if j["status"] == "running":
                subprocess.run(GPUQ + ["cancel", "--kill", j["id"]], env=self.env, capture_output=True)
        self.tmp.cleanup()

    def set_config(self, **kw):
        path = self.dir / ".gpuq" / "config.json"
        cfg = json.loads(path.read_text())
        cfg.update(kw)
        path.write_text(json.dumps(cfg))

    def q(self, *args, input=None):
        r = subprocess.run(GPUQ + list(args), env=self.env, cwd=self.dir, input=input,
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise AssertionError(f"gpuq {args} failed: {r.stderr}")
        return r.stdout

    def submit(self, name, mem, cmd="sleep 2", *extra):
        return self.q("submit", "-n", name, "-m", str(mem), *extra, "--", cmd).strip()

    def jobs(self):
        out = {}
        for p in (self.dir / ".gpuq" / "jobs").glob("j*.json"):
            j = json.loads(p.read_text())
            out[j["name"]] = j
        return out

    def run_until_final(self, timeout=40):
        t0 = time.time()
        while time.time() - t0 < timeout:
            self.q("once")
            if all(j["status"] in FINAL for j in self.jobs().values()):
                return self.jobs()
            time.sleep(0.5)
        raise AssertionError(f"jobs not finished: {[(n, j['status']) for n, j in self.jobs().items()]}")

    def per_gpu(self):
        count = {}
        for j in self.jobs().values():
            if j["status"] == "running":
                for g in j["gpus"]:
                    count[g] = count.get(g, 0) + 1
        return count


class TestPlacement(GpuqCase):
    def test_small_jobs_spread_over_all_gpus(self):
        for i in range(8):
            self.submit(f"s{i}", 10, "sleep 3", "--cpus", "1")
        self.q("once")
        self.assertEqual(self.per_gpu(), {0: 2, 1: 2, 2: 2, 3: 2})

    def test_cpu_slots_limit_concurrency(self):
        self.set_config(cpu_slots=8)
        for i in range(6):
            self.submit(f"s{i}", 5, "sleep 3", "--cpus", "4")
        self.q("once")
        self.assertEqual(sum(self.per_gpu().values()), 2)

    def test_memory_packing_respects_capacity(self):
        for i in range(12):
            self.submit(f"m{i}", 30, "sleep 3")
        self.q("once")
        # (80 - 3 safety) // 30 = 2 jobs per GPU
        self.assertEqual(self.per_gpu(), {0: 2, 1: 2, 2: 2, 3: 2})

    def test_external_memory_is_respected(self):
        self.env["GPUQ_FAKE_USED"] = "0:70,1:70,2:70"
        self.submit("a", 20, "sleep 3")
        self.submit("b", 20, "sleep 3")
        self.q("once")
        jobs = self.jobs()
        self.assertEqual(jobs["a"]["gpus"], [3])
        self.assertEqual(jobs["b"]["gpus"], [3])

    def test_hold_and_only(self):
        self.q("hold", "0", "1")
        self.submit("x", 10, "sleep 3")
        self.submit("y", 10, "sleep 3", "--only", "3")
        self.q("once")
        jobs = self.jobs()
        self.assertIn(jobs["x"]["gpus"][0], (2, 3))
        self.assertEqual(jobs["y"]["gpus"], [3])

    def test_exclusive_multi_gpu_job_is_not_starved(self):
        for i in range(4):
            self.submit(f"small{i}", 10, "sleep 2")
        self.q("once")
        self.submit("big", 60, "sleep 1", "-g", "2", "-x")
        for i in range(4):
            self.submit(f"late{i}", 10, "sleep 2")
        self.q("once")
        drained = set(json.loads((self.dir / ".gpuq" / "sched.json").read_text())["drained"])
        self.assertEqual(len(drained), 2)
        jobs = self.run_until_final()
        self.assertEqual(jobs["big"]["status"], "done")
        for name, j in jobs.items():
            if name.startswith("late") and set(j["gpus"]) & drained:
                self.assertGreaterEqual(j["started"], jobs["big"]["started"],
                                        f"{name} took a GPU drained for the big job")


class TestLifecycle(GpuqCase):
    def test_success_failure_and_oom_retry(self):
        self.submit("ok", 5, "echo step=10 loss=0.25")
        self.submit("bug", 5, "python3 -c 'raise ValueError(\"bad shape\")'")
        self.submit("oom", 20, "python3 -c 'import sys; print(\"CUDA out of memory\"); sys.exit(1)'",
                    "--retries", "1")
        jobs = self.run_until_final()
        self.assertEqual(jobs["ok"]["status"], "done")
        self.assertEqual(jobs["bug"]["status"], "failed")
        self.assertIn("ValueError: bad shape", jobs["bug"]["err"])
        self.assertEqual(jobs["bug"]["attempt"], 0, "non-OOM failures are not retried")
        self.assertEqual(jobs["oom"]["status"], "failed")
        self.assertEqual(jobs["oom"]["attempt"], 1)
        self.assertAlmostEqual(jobs["oom"]["mem"], 26.0)
        self.assertTrue(jobs["oom"]["err"].startswith("OOM"))

    def test_batch_with_name_dependencies(self):
        lines = [
            {"name": "prep", "mem": 5, "cmd": "echo prep"},
            {"name": "train", "mem": 5, "cmd": "echo train", "after": ["prep"]},
            {"name": "broken", "mem": 5, "cmd": "exit 3"},
            {"name": "eval", "mem": 5, "cmd": "echo eval", "after": "broken"},
        ]
        self.q("batch", "-", input="\n".join(json.dumps(x) for x in lines))
        jobs = self.run_until_final()
        self.assertEqual(jobs["train"]["status"], "done")
        self.assertGreaterEqual(jobs["train"]["started"], jobs["prep"]["ended"])
        self.assertEqual(jobs["broken"]["exit"], 3)
        self.assertEqual(jobs["eval"]["status"], "dep_failed")

    def test_batch_rejects_unknown_keys_atomically(self):
        bad = "\n".join([json.dumps({"name": "a", "mem": 5, "cmd": "true"}),
                         json.dumps({"name": "b", "mem": 5, "cmd": "true", "gpu": 1})])
        r = subprocess.run(GPUQ + ["batch", "-"], env=self.env, input=bad, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.jobs(), {})

    def test_rejects_backgrounded_or_gpu_pinned_commands(self):
        for cmd in ("nohup python train.py", "python train.py > log.txt 2>&1 &",
                    "python a.py & python b.py", "CUDA_VISIBLE_DEVICES=0 python train.py"):
            r = subprocess.run(GPUQ + ["submit", "-n", "bad", "-m", "5", "--", cmd], env=self.env,
                               cwd=self.dir, capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0, cmd)
        for cmd in ("python train.py > log.txt 2>&1", "cd src && python train.py && echo ok",
                    "python train.py &> log.txt"):
            self.submit("good", 5, cmd)
        self.assertEqual(len(list((self.dir / ".gpuq" / "jobs").glob("j*.json"))), 3)

    def test_submit_keeps_everything_after_double_dash(self):
        jid = self.q("submit", "-n", "flags", "-m", "5", "--", "python", "train.py", "--lr", "0.1", "--", "x").strip()
        cmd = json.loads((self.dir / ".gpuq" / "jobs" / f"{jid}.json").read_text())["cmd"]
        self.assertEqual(cmd, "python train.py --lr 0.1 -- x")

    def test_daemon_reports_failing_passes(self):
        self.set_config(poll_s=1)
        env = dict(self.env, GPUQ_FAKE_GPUS="4xbroken")
        d = subprocess.Popen(GPUQ + ["daemon"], env=env, cwd=self.dir,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            line = ""
            for _ in range(30):
                time.sleep(0.5)
                r = subprocess.run(GPUQ + ["status", "--line"], env=self.env, cwd=self.dir,
                                   capture_output=True, text=True)
                line = r.stdout
                if "ERR" in line:
                    break
            self.assertIn("gpuq ERR", line)
            self.assertIn("DAEMON ERROR", self.q("status"))
        finally:
            d.terminate()
            d.wait(timeout=10)

    def test_cancel_running_job(self):
        self.submit("long", 5, "sleep 60")
        self.q("once")
        self.q("cancel", self.jobs()["long"]["id"])
        jobs = self.run_until_final(timeout=15)
        self.assertEqual(jobs["long"]["status"], "cancelled")

    def test_retry_requeues_failed_job(self):
        self.submit("flaky", 5, "test -f ok.flag")
        jobs = self.run_until_final()
        self.assertEqual(jobs["flaky"]["status"], "failed")
        (self.dir / "ok.flag").write_text("")
        self.q("retry", jobs["flaky"]["id"])
        self.assertEqual(self.run_until_final()["flaky"]["status"], "done")

    def test_on_finish_hook_receives_job_env(self):
        self.set_config(on_finish='echo "$GPUQ_JOB_ID $GPUQ_STATUS $GPUQ_TAG" >> hook.txt')
        self.submit("h", 5, "true", "-t", "E9")
        self.run_until_final()
        for _ in range(20):
            if (self.dir / "hook.txt").exists():
                break
            time.sleep(0.2)
        self.assertEqual((self.dir / "hook.txt").read_text().split(), ["j0001", "done", "E9"])

    def test_launch_failure_is_reported(self):
        self.submit("nocwd", 5, "true", "--cwd", str(self.dir / "missing"))
        jobs = self.run_until_final()
        self.assertEqual(jobs["nocwd"]["status"], "failed")
        self.assertIn("launch failed", jobs["nocwd"]["err"])

    def test_status_and_report_render(self):
        self.submit("r", 5, "sleep 2", "-t", "E3")
        self.q("once")
        out = self.q("status")
        self.assertIn("RUNNING", out)
        self.assertIn("GPU0", out)
        self.assertIn("busy", self.q("status", "--line"))
        self.run_until_final()
        self.assertIn("E3", self.q("report", "--days", "1"))


class TestSnapshot(unittest.TestCase):
    def test_snapshot_collects_logs_results_commands_and_args(self):
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            proj, home = Path(tmp) / "proj", Path(tmp) / "home"
            shutil.copytree(TOOLS, proj / "tools")
            (proj / "logs").mkdir(parents=True)
            (proj / "logs" / "train_a.log").write_text("epoch 1 step=10 loss=0.9 val_dice=0.70\n" * 5)
            (proj / "outputs" / "exp1").mkdir(parents=True)
            (proj / "outputs" / "exp1" / "eval_results.json").write_text(json.dumps({"dice": 0.91, "hd95": 3.2}))
            (proj / "scripts").mkdir()
            (proj / "scripts" / "train.py").write_text(
                "p.add_argument('--budget', type=int, default=10)\np.add_argument('--seed', default=0)\n")
            tdir = home / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(proj.resolve()))
            tdir.mkdir(parents=True)
            rec = {"timestamp": "2026-10-24T10:00:00Z", "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "nohup python scripts/train.py --budget 2 > logs/a.log 2>&1 &"}},
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls outputs"}}]}}
            (tdir / "s.jsonl").write_text(json.dumps(rec) + "\n")
            env = dict(os.environ, HOME=str(home), GPUQ_FAKE_GPUS="4x80")
            r = subprocess.run(["bash", str(proj / "tools" / "snapshot.sh"), "24"], env=env,
                               capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            snap = (proj / "research" / "snapshot.md").read_text()
            self.assertIn("val_dice", snap)
            self.assertIn("eval_results.json: dice=0.91 hd95=3.2", snap)
            self.assertIn("python scripts/train.py --budget 2", snap)
            self.assertNotIn("`ls outputs`", snap)
            self.assertIn("scripts/train.py: --budget=10 --seed=0", snap)
            self.assertIn("outputs/exp1/", snap)


class TestDigest(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(TOOLS))
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_digest_metrics_nan_and_traceback(self):
        import digest_log
        log = self.dir / "train.log"
        lines = ["### gpuq j0001 x start", "### cmd: python train.py loss=9"]
        lines += [f"epoch {e} step={e * 100} loss={1.0 / (e + 1):.4f} val_dice={0.5 + e * 0.04:.3f} lr=1e-4"
                  for e in range(10)]
        lines += ["100%|####| 50/50 [00:10<00:00, loss=nan]",
                  "Traceback (most recent call last):",
                  '  File "train.py", line 10, in <module>',
                  '  File "model.py", line 42, in forward',
                  "RuntimeError: shape mismatch [4, 3] vs [4, 5]"]
        log.write_text("\n".join(lines) + "\n")
        out = digest_log.digest(log)
        self.assertIn("step=900", out)
        self.assertIn("val_dice", out)
        self.assertIn("NaN/Inf", out)
        self.assertIn("RuntimeError: shape mismatch", out)
        self.assertIn('model.py", line 42', out)
        self.assertNotIn("\nlr ", out)
        self.assertIn("val_dice=0.86", digest_log.last_metrics(log))

    def test_results_table_groups_seeds(self):
        import results_table
        for name, seed, dice in [("B2-b10", 0, 0.80), ("B2-b10", 1, 0.82), ("B6-b10", 0, 0.86)]:
            d = self.dir / "runs" / f"{name}-s{seed}"
            d.mkdir(parents=True)
            (d / "result.json").write_text(json.dumps(
                {"exp": "E3", "name": f"{name}-s{seed}", "seed": seed, "metrics": {"dice": dice}}))
        rows = results_table.load(self.dir / "runs", "**/result.json", tag="E3")
        groups = results_table.aggregate(rows, None, ["dice"])
        table = results_table.render(groups, ["dice"], sort="dice")
        body = [ln for ln in table.splitlines() if ln.startswith("| B")]
        self.assertTrue(body[0].startswith("| B6-b10"), table)  # best first
        self.assertIn("0.810 ± 0.014", table)


if __name__ == "__main__":
    unittest.main()
