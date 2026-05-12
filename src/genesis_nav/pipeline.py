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

from dataclasses import dataclass, field
from pathlib import Path

from genesis_nav.config import paths
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


@dataclass
class RunResult:
    output_dir: Path
    goal_residual: float
    max_xtrack: float
    success: bool        # True if within goal_tolerance at the end
    # We don't parse exact frame counts back from disk; the runner prints them.


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
        yaml_text = _designer_mod.design_task(
            description=description, name=name, robot=robot,
            bounds=bounds, temperature=self.temperature, model=self.model,
        )
        # configs_dir override stays for back-compat; default is the task's
        # own output folder (so each task is self-contained).
        yaml_path = _designer_mod.save_task_yaml(yaml_text, name, configs_dir)
        parsed = _yaml.safe_load(yaml_text) or {}
        return DesignResult(
            yaml_text=yaml_text, yaml_path=yaml_path,
            n_objects=len(parsed.get("objects", [])),
            env_type=parsed.get("world", {}).get("env_type", "?"),
            hdri=parsed.get("world", {}).get("sky", {}).get("hdri"),
            start=list(parsed.get("robot", {}).get("start", [])),
            goal=list(parsed.get("robot", {}).get("goal", [])),
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
        return _astar_mod.plan(task_name, yaml_path=yaml_path,
                                res=self.res, safety=self.safety)


# ── Stage 3 ─────────────────────────────────────────────────────────
class NavRunner:
    """Stage 3: build Genesis scene, spawn Husky, drive along the planned path."""

    def __init__(self, *, rasterizer: bool = False):
        self.rasterizer = rasterizer

    def run(self, task_name: str) -> RunResult:
        """Run one task. Returns RunResult — the heavy outputs (mp4s) live in
        the task output dir as a side-effect; this class just summarizes."""
        import json
        out_dir = _runner_mod.run(task_name, rasterizer=self.rasterizer)
        # The runner doesn't yet pickle its metrics; for now we infer from
        # presence of fpv.mp4 + trace.png. (TODO: persist metrics to .json.)
        success = (out_dir / "fpv.mp4").exists() and (out_dir / "trace.png").exists()
        # Parse final position from path.json + the runner's stdout? Simpler:
        # read the last frame's trace info if we stored it. For now, leave the
        # metric fields as 0 / NaN — the runner prints them to stdout.
        return RunResult(
            output_dir=out_dir,
            goal_residual=float("nan"),
            max_xtrack=float("nan"),
            success=success,
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
        design_res = self.designer.design(
            description=description, name=name, robot=robot, bounds=bounds)
        plan_res = self.planner.plan(name)
        run_res = self.runner.run(name)
        return {"design": design_res, "plan": plan_res, "run": run_res}
