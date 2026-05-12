"""
Stage classes + NavPipeline orchestrator.

The three stages each get a small class that holds its own configuration
and exposes a single canonical method. The functional APIs from
genesis_nav.{designer,planner,runner} remain (they're what the classes
delegate to), so nothing already importing them needs to change.

    from genesis_nav.pipeline import NavPipeline, NavTaskDesigner, NavPlanner, NavRunner

    pipeline = NavPipeline()   # default config
    result = pipeline.run(
        description="Outdoor park with cherry trees, husky west→east",
        name="park_v1", robot="husky", bounds=(-15, 15, -15, 15),
    )
    print(result["run"].goal_residual)

Or wire stages with custom config:

    pipeline = NavPipeline(
        designer=NavTaskDesigner(model="gemini-2.5-flash", temperature=0.4),
        planner=NavPlanner(res=0.05, safety=0.15),
        runner=NavRunner(rasterizer=True),
    )
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from genesis_nav.config import output_dir, paths
from genesis_nav.designer import designer as _designer_mod
from genesis_nav.planner import astar as _astar_mod
from genesis_nav.runner import husky_drive as _runner_mod


# ── Stage results ──────────────────────────────────────────────────
@dataclass
class DesignResult:
    yaml_text: str
    yaml_path: Path
    n_objects: int
    env_type: str
    hdri: str | None
    start: list[float]
    goal: list[float]
    elapsed_seconds: float = 0.0


@dataclass
class RunResult:
    output_dir: Path
    goal_residual: float
    max_xtrack: float
    success: bool                   # True if within goal_tolerance at the end
    sim_seconds: float = 0.0        # sim-clock seconds Husky drove
    sim_steps: int = 0
    wall_seconds: float = 0.0       # wall-clock seconds for the whole run stage
    n_frames: int = 0


# ── Stage 1 ─────────────────────────────────────────────────────────
class NavTaskDesigner:
    """Stage 1: Gemini designs a nav-task YAML from a short description."""

    def __init__(self, *, model: str = "gemini-2.5-flash", temperature: float = 0.6):
        self.model = model
        self.temperature = temperature

    def design(self, *, description: str, name: str,
               robot: str = "husky",
               bounds: tuple[float, float, float, float] = (-15, 15, -15, 15),
               configs_dir: Path | None = None) -> DesignResult:
        """Run one Gemini call. Saves the YAML to
        `<outputs>/nav_<name>/config.yaml` by default — pass `configs_dir`
        to redirect to a custom location."""
        import yaml as _yaml
        t0 = time.perf_counter()
        yaml_text = _designer_mod.design_task(
            description=description, name=name, robot=robot,
            bounds=bounds, temperature=self.temperature, model=self.model,
        )
        # configs_dir override stays for back-compat; default is the task's
        # own output folder (so each task is self-contained).
        yaml_path = _designer_mod.save_task_yaml(yaml_text, name, configs_dir)
        elapsed = time.perf_counter() - t0
        parsed = _yaml.safe_load(yaml_text) or {}
        return DesignResult(
            yaml_text=yaml_text, yaml_path=yaml_path,
            n_objects=len(parsed.get("objects", [])),
            env_type=parsed.get("world", {}).get("env_type", "?"),
            hdri=parsed.get("world", {}).get("sky", {}).get("hdri"),
            start=list(parsed.get("robot", {}).get("start", [])),
            goal=list(parsed.get("robot", {}).get("goal", [])),
            elapsed_seconds=round(elapsed, 3),
        )


# ── Stage 2 ─────────────────────────────────────────────────────────
class NavPlanner:
    """Stage 2: A* on the occupancy grid + line-of-sight smoothing."""

    def __init__(self, *, res: float = 0.10, safety: float = 0.10):
        self.res = res
        self.safety = safety

    def plan(self, task_name: str, *, yaml_path: Path | None = None) -> _astar_mod.PlanResult:
        """Plan one task. Returns the existing PlanResult dataclass.
        Writes occupancy.png / path.png / path.json to the output dir."""
        t0 = time.perf_counter()
        result = _astar_mod.plan(task_name, yaml_path=yaml_path,
                                  res=self.res, safety=self.safety)
        # Attach elapsed wall time to the existing dataclass via a small write-through.
        # (PlanResult is frozen-ish — we just stash it on the JSON output dir.)
        elapsed = time.perf_counter() - t0
        try:
            data = json.loads(result.json_path.read_text())
            data["elapsed_seconds"] = round(elapsed, 3)
            result.json_path.write_text(json.dumps(data, indent=2))
        except Exception:
            pass
        result.elapsed_seconds = round(elapsed, 3)
        return result


# ── Stage 3 ─────────────────────────────────────────────────────────
class NavRunner:
    """Stage 3: build Genesis scene, spawn Husky, drive along the planned path."""

    def __init__(self, *, rasterizer: bool = False):
        self.rasterizer = rasterizer

    def run(self, task_name: str) -> RunResult:
        """Run one task. Returns RunResult with metrics read back from the
        runner's persisted drive_metrics.json."""
        t0 = time.perf_counter()
        out_dir = _runner_mod.run(task_name, rasterizer=self.rasterizer)
        wall = time.perf_counter() - t0

        metrics_path = out_dir / "drive_metrics.json"
        if metrics_path.exists():
            m = json.loads(metrics_path.read_text())
            # Also patch the wall_seconds to include scene-build time (which
            # happened inside _runner_mod.run before the drive loop started).
            m["wall_seconds"] = round(wall, 3)
            metrics_path.write_text(json.dumps(m, indent=2))
            return RunResult(
                output_dir=out_dir,
                goal_residual=float(m.get("goal_residual_m", float("nan"))),
                max_xtrack=float(m.get("max_xtrack_m", float("nan"))),
                success=bool(m.get("goal_reached", False)),
                sim_seconds=float(m.get("sim_seconds", 0.0)),
                sim_steps=int(m.get("sim_steps", 0)),
                wall_seconds=round(wall, 3),
                n_frames=int(m.get("n_frames", 0)),
            )
        # Fallback — runner didn't write metrics (older code path).
        success = (out_dir / "fpv.mp4").exists() and (out_dir / "trace.png").exists()
        return RunResult(
            output_dir=out_dir,
            goal_residual=float("nan"), max_xtrack=float("nan"),
            success=success, wall_seconds=round(wall, 3),
        )


# ── Orchestrator ────────────────────────────────────────────────────
class NavPipeline:
    """Compose Designer → Planner → Runner. The master entrypoint."""

    def __init__(self,
                  designer: NavTaskDesigner | None = None,
                  planner:  NavPlanner | None = None,
                  runner:   NavRunner  | None = None):
        self.designer = designer or NavTaskDesigner()
        self.planner  = planner  or NavPlanner()
        self.runner   = runner   or NavRunner()

    def run(self, *, description: str, name: str,
            robot: str = "husky",
            bounds: tuple[float, float, float, float] = (-15, 15, -15, 15)) -> dict:
        """Run all 3 stages end-to-end. Returns a dict with the stage results.
        Raises if any stage fails (e.g. A* finds no path)."""
        pipeline_t0 = time.perf_counter()
        design_res = self.designer.design(
            description=description, name=name, robot=robot, bounds=bounds)
        plan_res = self.planner.plan(name)
        run_res = self.runner.run(name)
        total_wall = time.perf_counter() - pipeline_t0

        # Write consolidated metrics + human-readable log to the task folder.
        out = run_res.output_dir
        consolidated = {
            "task_name": name,
            "description": description,
            "robot": robot,
            "bounds": list(bounds),
            "timestamp_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "stages": {
                "design": {
                    "elapsed_seconds": design_res.elapsed_seconds,
                    "n_objects": design_res.n_objects,
                    "env_type": design_res.env_type,
                    "hdri": design_res.hdri,
                    "start": design_res.start,
                    "goal": design_res.goal,
                },
                "plan": {
                    "elapsed_seconds": plan_res.elapsed_seconds,
                    "n_obstacles": plan_res.n_obstacles,
                    "path_cells": plan_res.path_cells,
                    "n_waypoints": plan_res.n_waypoints,
                    "path_length_m": plan_res.path_length_m,
                },
                "run": {
                    "wall_seconds": run_res.wall_seconds,
                    "sim_seconds": run_res.sim_seconds,
                    "sim_steps": run_res.sim_steps,
                    "n_frames": run_res.n_frames,
                    "success": run_res.success,
                    "goal_residual_m": run_res.goal_residual,
                    "max_xtrack_m": run_res.max_xtrack,
                },
            },
            "total_wall_seconds": round(total_wall, 3),
        }
        (out / "metrics.json").write_text(json.dumps(consolidated, indent=2))

        def fmt_dur(s):
            if s < 60: return f"{s:.1f} s"
            m, sec = divmod(s, 60); return f"{int(m)} min {sec:.1f} s"

        log_lines = [
            f"=== nav task: {name} ===",
            f"description     : {description}",
            f"robot           : {robot}",
            f"bounds          : {list(bounds)}",
            f"timestamp (UTC) : {consolidated['timestamp_utc']}",
            "",
            f"[design] {fmt_dur(design_res.elapsed_seconds)}",
            f"    env={design_res.env_type}, hdri={design_res.hdri}, "
            f"{design_res.n_objects} objects",
            f"    start={design_res.start}, goal={design_res.goal}",
            f"    yaml: {design_res.yaml_path}",
            "",
            f"[plan]   {fmt_dur(plan_res.elapsed_seconds)}",
            f"    A* {plan_res.path_cells} cells (~{plan_res.path_length_m:.1f} m), "
            f"smoothed {plan_res.n_waypoints} waypoints",
            f"    {plan_res.overlay_png}",
            "",
            f"[run]    {fmt_dur(run_res.wall_seconds)} wall ({fmt_dur(run_res.sim_seconds)} sim, "
            f"{run_res.sim_steps} steps, {run_res.n_frames} frames each cam)",
            f"    {'GOAL reached' if run_res.success else 'GOAL NOT reached'} — "
            f"residual {run_res.goal_residual:.2f} m, max xtrack {run_res.max_xtrack:.2f} m",
            f"    output: {run_res.output_dir}",
            "",
            f"TOTAL wall      : {fmt_dur(total_wall)}",
        ]
        (out / "run.log").write_text("\n".join(log_lines) + "\n")

        return {"design": design_res, "plan": plan_res, "run": run_res,
                "total_wall_seconds": round(total_wall, 3),
                "metrics_path": out / "metrics.json",
                "log_path": out / "run.log"}
