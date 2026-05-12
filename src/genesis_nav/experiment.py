"""
Experiment runner — execute `NavPipeline` N times with varied descriptions
and aggregate the metrics.

Each pipeline run is spawned in a fresh subprocess so Genesis's one-shot
`gs.init()` doesn't carry over between runs. (Genesis raises "already
initialized" if `init()` is called twice in the same Python process.)

Use cases:
  - Stress-test the pipeline across many environments
  - Build a benchmark dataset of nav tasks
  - Compare designer/planner/runner config across runs

CLI:
    genesis-nav experiment --count 5                      # 5 random from the prompt bank
    genesis-nav experiment --descriptions prompts.txt     # one description per line
    genesis-nav experiment --count 10 --rasterizer        # all rasterizer (no HDRI)
    genesis-nav experiment --count 5 --seed 42            # reproducible sampling

Outputs go under ~/.genesis_nav/outputs/exp_<YYYYMMDD_HHMMSS>/ with:
  - each pipeline run as a subfolder (nav_<name>_<ts>/)
  - summary.csv  — one row per run with timings + success + metrics
  - summary.md   — markdown summary table
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


# Curated prompt bank: varied env types, terrains, obstacle styles.
# Each prompt MUST be unambiguous to the LLM (env_type, what objects, what behavior).
DEFAULT_PROMPT_BANK: list[dict] = [
    {"name": "forest_oaks",     "robot": "husky",
      "description": "Forest-like flat outdoor area with mature oaks and a couple of bushes. Husky weaves west to east."},
    {"name": "urban_courtyard", "robot": "husky",
      "description": "Urban courtyard with two parked cars and a few hedges. Husky navigates from one side to the other."},
    {"name": "park_fountain",   "robot": "husky",
      "description": "Park with a central stone fountain, cherry trees, and a bench. Husky circles around the fountain to reach the other side."},
    {"name": "industrial_yard", "robot": "husky",
      "description": "Industrial yard with scattered crates and metal drums. Husky weaves between them."},
    {"name": "garden_hedges",   "robot": "husky",
      "description": "Garden with flowering bushes and a small hedge maze. Husky from west entrance to east exit."},
    {"name": "construction",    "robot": "husky",
      "description": "Construction site with concrete blocks and traffic cones. Husky navigates from staging area to the build zone."},
    {"name": "backyard_picnic", "robot": "husky",
      "description": "Backyard with a picnic table, two trees, and a trash bin. Husky drives diagonally across."},
    {"name": "warehouse_aisle", "robot": "husky",
      "description": "Indoor warehouse with crates stacked along the aisle. Husky drives down the central aisle."},
    {"name": "campus_paths",    "robot": "husky",
      "description": "Outdoor campus area with lamp posts, benches, and scattered trees. Husky finds a path through."},
    {"name": "open_terrain",    "robot": "husky",
      "description": "Open outdoor area with three large obstacles forcing a clear S-curve detour. Flat ground."},
]


@dataclass
class ExperimentRunResult:
    """One row in the experiment summary."""
    name: str
    description: str
    robot: str
    success: bool
    folder: Path
    design_seconds: float
    plan_seconds: float
    run_wall_seconds: float
    total_seconds: float
    sim_seconds: float
    n_waypoints: int
    path_length_m: float
    goal_residual_m: float
    max_xtrack_m: float
    n_objects: int
    error: str = ""


def _safe_name(s: str) -> str:
    """Make a description suitable as a task name slug."""
    keep = [c if c.isalnum() else "_" for c in s.lower()]
    return "".join(keep).strip("_")[:48]


class ExperimentRunner:
    """Run a batch of nav-task pipelines + aggregate results.

    Each run is spawned in a **fresh subprocess** so Genesis's one-shot
    init doesn't conflict between runs. Subprocesses inherit the parent's
    env (so GEMINI_API_KEY, PYTHONPATH for LuisaRender, etc. carry over)."""

    def __init__(self, pipeline=None, output_root: Path | None = None,
                 rasterizer: bool = False, model: str = "gemini-2.5-flash",
                 temperature: float = 0.6):
        from genesis_nav.config import paths
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.exp_dir = (output_root or paths().outputs) / f"exp_{ts}"
        self.exp_dir.mkdir(parents=True, exist_ok=True)
        # Pipeline kwargs go via CLI flags into the subprocess
        self.rasterizer = rasterizer
        self.model = model
        self.temperature = temperature
        # Kept for back-compat; if a caller passes their own pipeline we'd
        # in-process it, but the subprocess path is the supported route.
        self._pipeline = pipeline

    def _run_one_subprocess(self, t: dict, bounds) -> tuple[ExperimentRunResult, str]:
        """Spawn `genesis-nav pipeline` as a subprocess for one task. Returns
        (result, stdout_tail). Reads metrics.json from the run's output dir."""
        cmd = [
            sys.executable, "-m", "genesis_nav.cli", "pipeline",
            "--description", t["description"], "--name", t["name"],
            "--robot", t.get("robot", "husky"),
            # use --bounds=… so argparse doesn't mistake the leading `-` for a flag
            f"--bounds={','.join(str(v) for v in bounds)}",
            "--model", self.model,
            "--temperature", str(self.temperature),
        ]
        if self.rasterizer: cmd.append("--rasterizer")
        env = os.environ.copy()
        t0 = time.perf_counter()
        proc = subprocess.run(cmd, capture_output=True, text=True, env=env,
                                input="Yes\n", check=False)
        wall = time.perf_counter() - t0
        stdout_tail = "\n".join(proc.stdout.splitlines()[-30:])
        if proc.returncode != 0:
            return ExperimentRunResult(
                name=t["name"], description=t["description"],
                robot=t.get("robot", "husky"),
                success=False, folder=Path(""),
                design_seconds=0, plan_seconds=0, run_wall_seconds=0,
                total_seconds=round(wall, 3), sim_seconds=0,
                n_waypoints=0, path_length_m=0,
                goal_residual_m=float("nan"), max_xtrack_m=float("nan"),
                n_objects=0,
                error=f"exit {proc.returncode}; " + (proc.stderr.splitlines()[-1] if proc.stderr else "no stderr"),
            ), stdout_tail
        # Find this run's metrics.json (timestamped folder, scan latest under outputs)
        from genesis_nav.config import paths
        candidates = sorted(paths().outputs.glob(f"nav_{t['name']}_*/metrics.json"),
                              key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            return ExperimentRunResult(
                name=t["name"], description=t["description"],
                robot=t.get("robot", "husky"),
                success=False, folder=Path(""),
                design_seconds=0, plan_seconds=0, run_wall_seconds=0,
                total_seconds=round(wall, 3), sim_seconds=0,
                n_waypoints=0, path_length_m=0,
                goal_residual_m=float("nan"), max_xtrack_m=float("nan"),
                n_objects=0, error="metrics.json not found",
            ), stdout_tail
        m = json.loads(candidates[0].read_text())
        stages = m["stages"]
        return ExperimentRunResult(
            name=t["name"], description=t["description"],
            robot=t.get("robot", "husky"),
            success=bool(stages["run"]["success"]),
            folder=candidates[0].parent,
            design_seconds=stages["design"]["elapsed_seconds"],
            plan_seconds=stages["plan"]["elapsed_seconds"],
            run_wall_seconds=stages["run"]["wall_seconds"],
            total_seconds=m["total_wall_seconds"],
            sim_seconds=stages["run"]["sim_seconds"],
            n_waypoints=stages["plan"]["n_waypoints"],
            path_length_m=stages["plan"]["path_length_m"],
            goal_residual_m=stages["run"]["goal_residual_m"],
            max_xtrack_m=stages["run"]["max_xtrack_m"],
            n_objects=stages["design"]["n_objects"],
        ), stdout_tail

    def run_batch(self, tasks: list[dict],
                  bounds: tuple[float, float, float, float] = (-15, 15, -15, 15),
                  ) -> list[ExperimentRunResult]:
        """Run each entry in `tasks` (dicts with 'name', 'description', 'robot')."""
        results: list[ExperimentRunResult] = []
        for i, t in enumerate(tasks, 1):
            print(f"\n══════ experiment {i}/{len(tasks)}: {t['name']} ══════")
            print(f"  {t['description']}")
            result, tail = self._run_one_subprocess(t, bounds)
            if result.success:
                print(f"  ✅ ok  → {result.folder.name}  "
                       f"(residual {result.goal_residual_m:.2f} m, "
                       f"{result.total_seconds:.1f} s wall)")
            else:
                print(f"  ❌ FAIL: {result.error or 'see run.log in folder'}")
                # Tail of the subprocess's stdout for debugging
                if tail:
                    print(f"  ── subprocess tail ──")
                    for ln in tail.splitlines()[-6:]:
                        print(f"    {ln}")
            results.append(result)
        self._write_summary(results)
        return results

    def _write_summary(self, results: list[ExperimentRunResult]) -> None:
        # CSV
        csv_path = self.exp_dir / "summary.csv"
        fields = ["name", "description", "robot", "success", "folder",
                  "design_seconds", "plan_seconds", "run_wall_seconds",
                  "total_seconds", "sim_seconds", "n_waypoints", "path_length_m",
                  "goal_residual_m", "max_xtrack_m", "n_objects", "error"]
        with open(csv_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in results:
                row = {k: getattr(r, k) for k in fields}
                row["folder"] = str(row["folder"])
                w.writerow(row)

        # Markdown
        n = len(results)
        n_ok = sum(1 for r in results if r.success)
        mean_total = sum(r.total_seconds for r in results) / max(1, n)
        md = [
            f"# Experiment summary — {self.exp_dir.name}",
            f"",
            f"- Runs: **{n}**, succeeded: **{n_ok}/{n}**",
            f"- Mean wall time per run: **{mean_total:.1f} s**",
            f"- Generated: `{datetime.now().isoformat(timespec='seconds')}`",
            f"",
            "| # | name | success | objects | wp | length | residual | xtrack | total wall |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for i, r in enumerate(results, 1):
            md.append(f"| {i} | `{r.name}` | "
                       f"{'✅' if r.success else '❌'} | "
                       f"{r.n_objects} | {r.n_waypoints} | {r.path_length_m:.1f} m | "
                       f"{r.goal_residual_m:.2f} m | {r.max_xtrack_m:.2f} m | "
                       f"{r.total_seconds:.1f} s |")
        (self.exp_dir / "summary.md").write_text("\n".join(md) + "\n")

        print(f"\nExperiment summary  → {csv_path}")
        print(f"                    → {self.exp_dir / 'summary.md'}")


def _load_descriptions(path: Path) -> list[dict]:
    """Read non-empty lines as descriptions; auto-name them."""
    tasks: list[dict] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tasks.append({"name": _safe_name(line), "description": line, "robot": "husky"})
    return tasks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="genesis-nav experiment",
        description="Run N nav-task pipelines + aggregate metrics.",
    )
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--count", type=int, default=3,
                    help="how many random samples from the default prompt bank (default 3)")
    g.add_argument("--descriptions", type=Path,
                    help="text file with one prompt per line (overrides --count)")
    ap.add_argument("--robot", default="husky",
                     help="robot for all runs when sampling (default husky)")
    ap.add_argument("--bounds", default="-15,15,-15,15")
    ap.add_argument("--seed", type=int, default=None,
                     help="random seed for prompt-bank sampling (reproducible)")
    ap.add_argument("--rasterizer", action="store_true",
                     help="force rasterizer renderer (skip HDRI/LuisaRender)")
    ap.add_argument("--model", default="gemini-2.5-flash")
    ap.add_argument("--temperature", type=float, default=0.6)
    args = ap.parse_args(argv)

    bounds = tuple(float(v) for v in args.bounds.split(","))

    # Build the task list
    if args.descriptions:
        tasks = _load_descriptions(args.descriptions)
    else:
        rng = random.Random(args.seed)
        # If count > bank size, sample WITH replacement and tag each name
        # with a per-task index so the timestamped folders never collide
        # even if two runs start in the same second.
        if args.count <= len(DEFAULT_PROMPT_BANK):
            chosen = rng.sample(DEFAULT_PROMPT_BANK, k=args.count)
            tasks = [{**t, "robot": args.robot} for t in chosen]
        else:
            chosen = rng.choices(DEFAULT_PROMPT_BANK, k=args.count)
            tasks = []
            for i, t in enumerate(chosen):
                tasks.append({
                    **t, "robot": args.robot,
                    "name": f"{t['name']}_r{i:02d}",   # disambiguates repeats
                })
    if not tasks:
        print("no tasks to run", file=sys.stderr); return 1

    runner = ExperimentRunner(
        rasterizer=args.rasterizer,
        model=args.model, temperature=args.temperature,
    )
    print(f"=== experiment {runner.exp_dir.name} — {len(tasks)} runs ===")
    runner.run_batch(tasks, bounds=bounds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
