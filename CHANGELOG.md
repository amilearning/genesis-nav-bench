# Changelog

## 0.2.0 (2026-05-12)

### Added
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
