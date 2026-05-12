# Changelog

## 0.3.1 (2026-05-12)

### Fixed
- **Go2 runner now actually honors `--rasterizer`.** Previously
  `go2_drive.run_go2(rasterizer=...)` accepted the flag but never wired a
  renderer into `Go2Env`, so the Scene was always built with the default
  rasterizer regardless of the flag. Patch:
  - `robots/go2_env.py` reads `_renderer` and `_use_raytracer_cams` set by
    the runner before `__init__`, passes `renderer=` to `gs.Scene` and
    switches FPV/boom camera options to `RaytracerCameraOptions` (spp=32,
    denoise=True) when in raytracer mode.
  - `runner/go2_drive.py` mirrors `husky_drive`'s LuisaRender detection:
    builds `gs.renderers.RayTracer(env_surface=Emission(hdri))` when
    LuisaRender is importable and the YAML names an HDRI.
- **CLI dispatch bug**: `genesis-nav run <task>` always called `husky_drive`
  regardless of `robot.type` in the task YAML. Fixed `cli.py` to peek at
  `robot.type` and dispatch to `go2_drive.run_go2` when it says `go2`.

## 0.3.0 (2026-05-12)

### Added
- **Go2 quadruped** as a first-class robot in the nav pipeline. `robot.type`
  in the YAML now selects between `husky` (existing) and `go2` (new); the
  runner auto-dispatches.
- **Bundled Go2 policy**: `cfgs.pkl` + `model_299.pt` ship inside the wheel
  at `src/genesis_nav/robots/go2_policy/`. Resolved via
  `paths().go2_policy_dir` (overridable in `~/.genesis_navrc`).
- `src/genesis_nav/runner/go2_drive.py` — Go2 nav runner. Pure-pursuit
  produces `(v_lin, ω)` which becomes the velocity command for the trained
  RL policy; the policy outputs joint targets at 50 Hz.
- `src/genesis_nav/robots/go2_env.py` — Go2Env (vendored from RoboGen-style
  upstream, patched: pre-build hook for spawning the planner's obstacles
  into the scene + spawn-pose override).
- New `[go2]` optional install: `pip install genesis-nav-bench[go2]`
  brings `rsl-rl-lib>=5.0.0`, `tensordict`, `scipy`.
- `asset_registry.yaml`: `go2` robot row (radius 0.30 m, min corridor 0.80 m,
  target speed 0.5 m/s, spawn z 0.35 m). Marks the policy as flat-terrain only.
- `prompts.py:ROBOT_SUMMARIES['go2']` — tells Gemini Go2 needs flat ground.

### Fixed
- Two Go2-specific bugs surfaced during integration:
  1. Go2Env auto-resets at `episode_length_s` (20 s) mid-mission — fixed by
     overriding it to 600 s for nav drives.
  2. Pure-pursuit can't stop Go2 if it overshoots the goal (no reverse) —
     fixed via a wider `goal_tolerance_m=0.60` + an overshoot detector
     (stops when min-dist-to-goal was < 1.2 m and now growing for ≥50 steps).

## 0.2.0 (2026-05-12)

### Added
- **`genesis_nav.pipeline`** — `NavTaskDesigner`, `NavPlanner`, `NavRunner`
  stage classes + `NavPipeline` orchestrator. Each class holds its own
  configuration (model/temperature, res/safety, rasterizer/HDRI). Existing
  function APIs are kept as the underlying impl. Surfaced at package
  top-level: `from genesis_nav import NavPipeline`.
- **`examples/run_pipeline.py`** — programmatic master script using the
  stage classes (analog of `genesis-nav pipeline`).
- **`genesis_nav.planner.smoother`** — `densify_linear` (LOS-safe resampling of
  A*+smoothed paths at 10 cm) and `densify_spline` (natural cubic spline, when
  you can afford to risk minor obstacle shortcutting).
- **`genesis_nav.runner.pure_pursuit`** — `PurePursuit` follower with
  configurable lookahead, curvature-aware speed scaling, and wheel-velocity
  limits. Replaces the bare per-waypoint P-controller.
- **Bundled example configs**: `nav_smoke_test_v1.yaml`, `nav_chicane_stress_v1.yaml`.
  Copy them out with `genesis-nav examples`.
- New CLI subcommand: `genesis-nav examples [--dest DIR] [--overwrite]`.

### Changed
- **Experiment runner** (`genesis-nav experiment`) — runs N pipelines in
  sequence with varied descriptions and writes a summary CSV/MD aggregating
  timings, success, residuals. Pulls from a 10-prompt curated bank by default
  (or use `--descriptions prompts.txt` for your own list). Reproducible with
  `--seed`. Single failed runs (e.g. Gemini 503) are caught and reported in
  the summary, the batch keeps going.
  Programmatic: `from genesis_nav import ExperimentRunner, DEFAULT_PROMPT_BANK`.
- **Timestamped task folders by default**. `genesis-nav pipeline` now writes
  to `~/.genesis_nav/outputs/nav_<name>_<YYYYMMDD_HHMMSS>/`, so re-running the
  same task name preserves the previous run's videos + metrics. The standalone
  `design / plan / run` subcommands still treat the name literally (so they
  can target an existing folder). Use `--no-timestamp` to opt out.
- **Per-stage timing + metrics persisted** to each task folder:
  - `drive_metrics.json` (runner-only: sim_steps, sim_seconds, wall_seconds,
    final_pos, goal_residual_m, max_xtrack_m, n_frames, goal_reached)
  - `metrics.json` (pipeline-wide consolidated: design/plan/run elapsed +
    parameters + total_wall_seconds, with ISO-8601 UTC timestamp)
  - `run.log` (human-readable one-pager of everything that happened)
  Surfaced in the dataclasses: `DesignResult.elapsed_seconds`,
  `PlanResult.elapsed_seconds`, `RunResult.{wall_seconds, sim_seconds,
   sim_steps, n_frames}`.
- **Each task is now self-contained**: the YAML config is saved as
  `<outputs>/nav_<name>/config.yaml`, alongside `occupancy.png`, `path.png`,
  `path.json`, `fpv.mp4`, `boom.mp4`, `chase.mp4`, `trace.png`. The legacy
  `<root>/configs/nav_<name>.yaml` path is still accepted as a fallback by
  the planner / runner, so old hand-authored configs keep working.
- `genesis-nav examples` now drops bundled YAMLs into per-task output
  subfolders (one `config.yaml` per task), matching the new layout.
- **Husky wheel velocity gain bumped from `kv=1.0` → `kv=20.0`** in the runner.
  This is the load-bearing fix that lets diff-drive corrections actually take
  effect — the old `kv=1.0` made wheels reach ~10% of commanded velocity under
  load, so sharp turns stalled. With `kv=20.0`, the chicane stress test now
  passes (0.30 m goal residual, 0.36 m max cross-track).
- Runner reports `goal_residual` and `max_xtrack` at the end of each drive.

### Verified
| scene | controller | goal residual | max cross-track | sim time |
|---|---|---|---|---|
| smoke_test (straight 24 m) | P-controller (old) | 0.49 m | 0.18 m (growing) | 30 s |
| smoke_test (straight 24 m) | pure-pursuit + kv=20 | 0.29 m | 0.05 m (flat) | 24 s |
| chicane_stress (S-curve, 8 wp) | P-controller (old) | STUCK | 0.65 m | timeout |
| chicane_stress (S-curve, 8 wp) | pure-pursuit + kv=20 | 0.30 m | 0.36 m | 44 s |

## 0.1.0 (2026-05-11)

Initial layout:
- `genesis_nav.designer` (Gemini scene designer) + bundled
  `asset_registry.yaml` (320 lines: HDRIs, ground textures, terrain modes,
  USD/GLB assets, primitives, recipes) and `scene_config_schema.md`.
- `genesis_nav.planner.astar` — A* on inflated 2D occupancy grid + LOS smoothing.
- `genesis_nav.runner.husky_drive` — Genesis scene build + Husky diff-drive +
  3-cam capture (FPV + boom + chase).
- `genesis_nav.assets` — `mdl_to_preview`, `tree_mdl_to_preview`,
  `polyhaven_fetcher`, `simready_mirror`.
- `genesis_nav.catalog` — `catalog.py` (scans configured asset paths into CSV+MD).
- `genesis_nav.bootstrap` — writes `~/.genesis_navrc` + prints external-asset guide.
- `genesis_nav.cli` — unified `genesis-nav <subcommand>` dispatcher.
- Apache-2.0 license, full asset-license table in `DATA_LICENSES.md`.
