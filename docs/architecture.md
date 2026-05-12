# Architecture

A 3-stage navigation-data pipeline, each stage producing artifacts the
next stage consumes — so you can re-run any stage independently.

```
                 ┌──────────────────────┐
   description ─►│ 1.  designer (LLM)   │── nav_<name>.yaml ──┐
   robot       ─►│     prompts + Gemini │                     │
   bounds      ─►│                      │                     │
                 └──────────────────────┘                     ▼
                                                ┌──────────────────────┐
                                                │ 2. planner (A* + LOS)│── occupancy.png
                                                │     deterministic    │── path.png
                                                │                      │── path.json (waypoints)
                                                └──────────────────────┘
                                                              │
                                                              ▼
                                                ┌──────────────────────┐
                                                │ 3. runner (Genesis)  │── fpv.mp4
                                                │     scene + drive    │── boom.mp4
                                                │     + LuisaRender    │── chase.mp4
                                                │                      │── trace.png
                                                └──────────────────────┘
```

## Stage 1 — `genesis_nav.designer`

Goal: turn a natural-language description into a complete nav-task YAML.

Input prompt = SYSTEM (persona + hard rules) + USER (description + bounds +
robot summary + full `asset_registry.yaml` + full `scene_config_schema.md`).
Pasting the registry verbatim means Gemini sees the exact list of valid HDRI
names, ground textures, terrain modes, USD asset names — no hallucination.

Key rules (from `prompts.py:SYSTEM_PROMPT`):
- 5-10 objects total
- Obstacles MUST sit along the natural straight-line so the path bends
- Every passage ≥ `min_corridor_width` (1.2 m for Husky)
- HDRI must match env_type (outdoor vs indoor)

Output: YAML with `scene`, `robot.{type,start,goal}`, `world.{env_type,sky,ground}`, `objects[]`.

## Stage 2 — `genesis_nav.planner`

Pure-Python A* on a 2D occupancy grid:
1. Read YAML → parse object positions + sizes.
2. Paint each object's xy footprint as blocked, inflated by
   `robot.radius + safety_margin`.
3. Run A* (8-connected) from `start` to `goal`.
4. Collapse the path with line-of-sight smoothing.
5. Save:
   - `occupancy.png` — black/white grid
   - `path.png` — RGB overlay (start blue, goal red, A* yellow, smoothed orange)
   - `path.json` — waypoint list in world coordinates

Footprint lookup hierarchy:
- primitives → uses their geometric size
- `usd_assets[name].typical_footprint_radius` (TRUNK radius, not canopy!)
- `glb_assets[name].typical_footprint_radius`

## Stage 3 — `genesis_nav.runner`

Build the Genesis scene + drive the robot:
- Renderer = `RayTracer + env_surface(HDRI)` if LuisaRender is available,
  else rasterizer with directional sun + sky-blue background.
- Ground = `Plane` (mode=flat) or `Terrain` (random_uniform / sloped).
- Each YAML object → `gs.morphs.{Box,Cylinder,Sphere,USD,Mesh}`.
- Robot = URDF from `paths().husky_urdf`.
- Cameras = FPV + boom (rigidly attached to robot) + free chase cam.
- Drive loop:
  1. The planner's sparse waypoints get densified with `smoother.densify_linear`
     (10 cm step, LOS-safe). 24 m straight path → 241 reference points.
  2. A `pure_pursuit.PurePursuit` follower picks a "carrot" point on the
     dense path ~0.6 m ahead of the robot and computes `(v_lin, omega)` from
     the heading error to the carrot.
  3. Wheel velocities are computed from `(v_lin, omega)` via the diff-drive
     kinematics and applied as velocity setpoints. **Wheel `kv` is set to
     `20.0` (load-bearing default)** — the previous `kv=1.0` only achieved
     ~10 % of commanded velocity under load and caused all sharp-turn stalls.
- Saves `fpv.mp4`, `boom.mp4`, `chase.mp4`, `trace.png`, plus stdout metrics
  `goal_residual` and `max_xtrack`.

## Configuration

All asset paths come from `genesis_nav.config.paths()`, which reads (in order):
1. `GENESIS_NAV_<KEY>` env vars
2. `~/.genesis_navrc` (TOML)
3. Defaults under `$GENESIS_NAV_ROOT` (default `~/.genesis_nav`)

The same code runs unchanged on different machines — only the config moves.

## Package layout

```
src/genesis_nav/
├── __init__.py
├── config.py            # path resolver
├── cli.py               # `genesis-nav` entry
├── bootstrap.py         # one-shot setup
├── designer/
│   ├── designer.py
│   ├── prompts.py
│   └── data/
│       ├── asset_registry.yaml
│       └── scene_config_schema.md
├── planner/astar.py
├── runner/husky_drive.py
├── assets/
│   ├── mdl_to_preview.py      # SimReady / Lightwheel converter
│   ├── tree_mdl_to_preview.py # Vegetation converter
│   ├── polyhaven_fetcher.py
│   └── simready_mirror.py
└── catalog/catalog.py
```
