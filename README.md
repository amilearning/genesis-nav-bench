# genesis-nav-bench

**LLM-driven navigation-task generation + photoreal simulation for the
[Genesis](https://genesis-world.readthedocs.io) robot simulator.**

> Status: alpha — interfaces are still moving.

## What it does

A 3-stage pipeline:

1. **Design** — Google's Gemini gets a short natural-language description
   ("Outdoor park with cherry trees, Husky crosses east-to-west") plus an
   asset registry, and emits a complete navigation-task YAML: environment
   type, HDRI skybox, ground texture, terrain mode, obstacle layout, and
   start + goal coordinates.

2. **Plan** — A* on a 2D occupancy grid (inflated by the robot's body
   radius) finds a collision-free path. Line-of-sight smoothing collapses
   it to a small set of waypoints.

3. **Run** — Genesis builds the scene (LuisaRender path-traced renderer
   with the HDRI skybox if available, otherwise rasterizer), spawns the
   robot, and a diff-drive controller follows the waypoints. Outputs FPV
   / boom / chase MP4s plus a trace overlay onto the planned path.

The point: cheaply generate hundreds of varied navigation tasks for data
collection / benchmarking, with each task as a self-contained YAML.

## Quickstart

```bash
# 1. Install (Genesis itself has to be installed separately for now)
pip install genesis-nav-bench[designer,assets]

# 2. One-time: download asset packs (see "Required external assets" below)
genesis-nav fetch hdris textures   # PolyHaven HDRIs + PBR textures

# 3. Configure asset paths (env vars OR ~/.genesis_navrc)
export GENESIS_NAV_ASSETS=~/.genesis_nav/assets
export GEMINI_API_KEY="..."

# 4. Generate one task
genesis-nav design \
    --description "Outdoor park with scattered trees, husky from west to east" \
    --name park_v1 --robot husky --bounds=-15,15,-15,15

# 5. Plan + run it
genesis-nav plan park_v1
genesis-nav run  park_v1
```

Outputs land in `~/.genesis_nav/outputs/nav_park_v1/`:
- `config.yaml`            — Gemini's task design
- `occupancy.png`          — 2D obstacle grid
- `path.png`               — A* path overlay
- `path.json`              — waypoint list
- `fpv.mp4`, `boom.mp4`, `chase.mp4` — robot views
- `trace.png`              — actual driven trajectory on top of the plan

## Required external assets

Most of the data is licensed CC BY-NC or similar non-commercial — you have
to download it yourself. The fetcher will pull whatever it can; the rest
needs manual download per the links below.

| Source | What | License | How |
|---|---|---|---|
| [PolyHaven](https://polyhaven.com) | HDRIs + PBR ground textures | CC0 | `genesis-nav fetch hdris textures` |
| [Objaverse](https://objaverse.allenai.org) | Trees, bushes, hedges, cars (GLB) | mixed (per-asset) | `genesis-nav fetch objaverse` (needs `pip install objaverse`) |
| [NVIDIA SimReady](https://docs.omniverse.nvidia.com/) | Restaurant Demopack vegetation (USD trees) + Simple_Warehouse / Office / Hospital scenes | NVIDIA SimReady (free non-commercial) | S3 mirror — `genesis-nav fetch simready` |
| [Lightwheel](https://github.com/LightwheelAI/Lightwheel-simready-asset) | 243 kitchen appliances (USD) | CC BY-NC 4.0 | Google Drive zip — manual; see `docs/lightwheel.md` |
| [Genesis](https://genesis-world.readthedocs.io) | Simulator itself + optional LuisaRender path tracer | Apache-2.0 | `pip install genesis-world`; LuisaRender is a separate optional build |

## Repository layout

```
src/genesis_nav/
├── config.py          # path resolution from env vars + .genesis_navrc
├── cli.py             # `genesis-nav <subcommand>` dispatcher
├── designer/          # Gemini scene designer
│   ├── designer.py
│   ├── prompts.py     # SYSTEM_PROMPT, PROMPT_TEMPLATE
│   └── data/
│       ├── asset_registry.yaml       # what Gemini sees
│       └── scene_config_schema.md    # output spec
├── planner/
│   └── astar.py
├── runner/
│   └── husky_drive.py
├── assets/            # asset converters + fetchers
│   ├── mdl_to_preview.py
│   ├── tree_mdl_to_preview.py
│   ├── polyhaven_fetcher.py
│   └── objaverse_index.py
└── catalog/
    └── catalog.py     # generate asset catalog CSV/MD
```

## License

Apache-2.0 (code). External assets remain under their respective licenses
— see `DATA_LICENSES.md` for the full list.
