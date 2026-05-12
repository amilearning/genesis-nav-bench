"""
A* planner — stage 2 of the genesis-nav-bench pipeline.

Reads a nav-task YAML, builds a 2D occupancy grid by painting object
footprints (inflated by robot radius + safety margin), runs A* + LOS
smoothing, saves occupancy.png + path.png + path.json.

Programmatic API:
    from genesis_nav.planner import plan
    result = plan(task_name="park_v1")        # uses default paths
"""
from __future__ import annotations

import argparse
import heapq
import json
import math
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import yaml
from PIL import Image, ImageDraw

from genesis_nav.config import output_dir, paths
from genesis_nav.designer.prompts import load_registry_text


# Cache the parsed asset registry for footprint lookups.
_REGISTRY: dict | None = None
def _registry() -> dict:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = yaml.safe_load(load_registry_text())
    return _REGISTRY


@dataclass
class PlanResult:
    task_name: str
    n_obstacles: int
    walkable_cells: int
    path_cells: int
    path_length_m: float
    n_waypoints: int
    waypoints_world: list[list[float]]
    occupancy_png: Path
    overlay_png: Path
    json_path: Path
    elapsed_seconds: float = 0.0    # set by NavPlanner wrapper


# --- Footprint painting ---------------------------------------------
def _robot_radius(robot_type: str) -> float:
    return float(_registry()["robots"][robot_type]["radius"])


def _usd_footprint_radius(name: str) -> float:
    entry = _registry().get("usd_assets", {}).get(name, {})
    return float(entry.get("typical_footprint_radius", 0.5))


def _glb_footprint_radius(name: str) -> float:
    entry = _registry().get("glb_assets", {}).get(name, {})
    return float(entry.get("typical_footprint_radius", 0.5))


def _paint_box(trav, obj, x0, y0, res, inflate):
    H, W = trav.shape
    px, py, _ = obj["position"]
    sx, sy, _ = obj["size"]
    half_x = sx / 2 + inflate
    half_y = sy / 2 + inflate
    c0 = max(0, int(round((px - half_x - x0) / res)))
    c1 = min(W-1, int(round((px + half_x - x0) / res)))
    r0 = max(0, int(round((py - half_y - y0) / res)))
    r1 = min(H-1, int(round((py + half_y - y0) / res)))
    trav[r0:r1+1, c0:c1+1] = 0


def _paint_disk(trav, x_w, y_w, r_m, x0, y0, res):
    H, W = trav.shape
    cx = int(round((x_w - x0) / res))
    cy = int(round((y_w - y0) / res))
    rr = int(round(r_m / res))
    for dy in range(-rr, rr+1):
        for dx in range(-rr, rr+1):
            if dx*dx + dy*dy <= rr*rr:
                r_idx, c_idx = cy + dy, cx + dx
                if 0 <= r_idx < H and 0 <= c_idx < W:
                    trav[r_idx, c_idx] = 0


def _paint_obj(trav, obj, x0, y0, res, inflate):
    t = obj["type"]
    px, py = obj["position"][:2]
    if t == "box":
        _paint_box(trav, obj, x0, y0, res, inflate)
    elif t in ("cylinder", "sphere"):
        _paint_disk(trav, px, py, obj["radius"] + inflate, x0, y0, res)
    elif t == "usd":
        nm = obj.get("usd_name")
        rr = (_usd_footprint_radius(nm) if nm else 0.6) + inflate
        _paint_disk(trav, px, py, rr, x0, y0, res)
    elif t == "glb":
        nm = obj.get("glb_name")
        rr = (_glb_footprint_radius(nm) if nm else 0.6) + inflate
        _paint_disk(trav, px, py, rr, x0, y0, res)
    else:
        raise ValueError(f"unknown object type {t!r}")


# --- A* + LOS smoothing ---------------------------------------------
def _astar(grid, start, goal):
    H, W = grid.shape
    if not (grid[start] > 0 and grid[goal] > 0):
        return None
    open_set = [(0.0, start)]; came_from = {}; g = {start: 0.0}
    NB = [(-1,-1,1.414),(-1,0,1),(-1,1,1.414),(0,-1,1),(0,1,1),
          (1,-1,1.414),(1,0,1),(1,1,1.414)]
    h = lambda p: math.hypot(p[0]-goal[0], p[1]-goal[1])
    while open_set:
        _, cur = heapq.heappop(open_set)
        if cur == goal:
            path = [cur]
            while cur in came_from: cur = came_from[cur]; path.append(cur)
            return path[::-1]
        for dr, dc, cost in NB:
            nb = (cur[0]+dr, cur[1]+dc)
            if not (0 <= nb[0] < H and 0 <= nb[1] < W): continue
            if grid[nb] == 0: continue
            ng = g[cur] + cost
            if ng < g.get(nb, math.inf):
                came_from[nb] = cur; g[nb] = ng
                heapq.heappush(open_set, (ng + h(nb), nb))
    return None


def _line_clear(a, b, grid):
    r0, c0 = a; r1, c1 = b
    n = max(abs(r1-r0), abs(c1-c0))
    if n == 0: return True
    for t in np.linspace(0, 1, n+1):
        r = int(round(r0 + t*(r1-r0)))
        c = int(round(c0 + t*(c1-c0)))
        if grid[r, c] == 0: return False
    return True


def _smooth_path(path, grid):
    if not path: return path
    out = [path[0]]; i = 0
    while i < len(path) - 1:
        j = len(path) - 1
        while j > i + 1:
            if _line_clear(path[i], path[j], grid): break
            j -= 1
        out.append(path[j]); i = j
    return out


# --- Public API -----------------------------------------------------
def _resolve_task_yaml(task_name: str, explicit: Path | None) -> Path:
    """Find the task YAML: explicit arg first, then <outputs>/nav_<name>/config.yaml
    (canonical), then legacy <root>/configs/nav_<name>.yaml."""
    if explicit is not None:
        if not explicit.exists():
            raise FileNotFoundError(f"task YAML not found at {explicit}")
        return explicit
    candidates = [
        output_dir(task_name) / "config.yaml",
        paths().root / "configs" / f"nav_{task_name}.yaml",
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError(
        f"task YAML not found in any of:\n  " + "\n  ".join(str(c) for c in candidates))


def plan(task_name: str, *, yaml_path: Path | None = None,
         res: float = 0.10, safety: float = 0.10) -> PlanResult:
    """Plan a nav task. Returns PlanResult + writes 3 files into output_dir(task_name)."""
    task_name = task_name.removeprefix("nav_").removesuffix(".yaml")
    yaml_path = _resolve_task_yaml(task_name, yaml_path)

    out_dir = output_dir(task_name)
    cfg = yaml.safe_load(yaml_path.read_text())
    bounds = cfg["scene"]["bounds"]
    x0, x1 = float(bounds[0]), float(bounds[1])
    y0, y1 = float(bounds[2]), float(bounds[3])
    robot_type = cfg["robot"]["type"]
    start_xy = list(map(float, cfg["robot"]["start"]))
    goal_xy  = list(map(float, cfg["robot"]["goal"]))

    inflate = _robot_radius(robot_type) + safety
    W = int(round((x1 - x0) / res))
    H = int(round((y1 - y0) / res))
    trav = np.full((H, W), 255, dtype=np.uint8)

    n_obj = 0
    for obj in cfg.get("objects", []):
        try:
            _paint_obj(trav, obj, x0, y0, res, inflate); n_obj += 1
        except Exception as e:
            print(f"  SKIP {obj.get('name')} ({obj.get('type')}): {e}")

    walkable = int((trav > 0).sum())
    print(f"[plan] painted {n_obj} objects, walkable = {walkable} cells "
          f"({walkable*res*res:.1f} m²)")

    Image.fromarray(trav).save(out_dir / "occupancy.png")

    w2p = lambda wx, wy: (int(round((wy - y0)/res)), int(round((wx - x0)/res)))
    p2w = lambda r, c:   (c*res + x0, r*res + y0)
    start_px = w2p(*start_xy); goal_px = w2p(*goal_xy)

    if trav[start_px] == 0:
        print(f"[plan] WARN: start cell is BLOCKED")
    if trav[goal_px] == 0:
        print(f"[plan] WARN: goal cell is BLOCKED")

    path = _astar(trav, start_px, goal_px)
    if not path:
        raise RuntimeError("A* failed — no path from start to goal")
    smoothed = _smooth_path(path, trav)
    length_m = len(path) * res

    # Overlay PNG
    viz = np.stack([trav, trav, trav], axis=-1).astype(np.uint8)
    viz[trav == 0] = (60, 80, 90)
    viz[trav > 0]  = (210, 225, 235)
    for r, c in path: viz[r, c] = (255, 200, 0)
    for r, c in smoothed:
        viz[max(0,r-2):r+3, max(0,c-2):c+3] = (255, 110, 0)
    sr, sc = start_px; gr, gc = goal_px
    viz[max(0,sr-4):sr+5, max(0,sc-4):sc+5] = (0, 150, 255)
    viz[max(0,gr-4):gr+5, max(0,gc-4):gc+5] = (255, 0, 0)
    pil = Image.fromarray(viz)
    draw = ImageDraw.Draw(pil)
    draw.rectangle([(6, 6), (380, 60)], fill=(255, 255, 255), outline=(0, 0, 0))
    draw.text((12, 10), f"nav_{task_name}", fill=(0, 0, 0))
    draw.text((12, 26), f"{n_obj} objects, robot={robot_type}, inflate={inflate:.2f} m",
              fill=(0, 0, 0))
    draw.text((12, 42),
              f"A* {len(path)} cells (~{length_m:.1f} m), smoothed {len(smoothed)} wp",
              fill=(0, 0, 0))
    overlay_path = out_dir / "path.png"
    pil.save(overlay_path)

    waypoints_world = [list(p2w(*p)) for p in smoothed]
    json_path = out_dir / "path.json"
    json.dump({
        "scene": cfg["scene"]["name"],
        "robot": robot_type,
        "start": start_xy, "goal": goal_xy,
        "bounds": [x0, x1, y0, y1],
        "resolution_m": res,
        "inflate_m": inflate,
        "n_obstacles": n_obj,
        "length_m": length_m,
        "waypoints": waypoints_world,
    }, open(json_path, "w"), indent=2)

    print(f"[plan] A* {len(path)} cells (~{length_m:.1f} m), {len(smoothed)} wp → {out_dir}")
    return PlanResult(
        task_name=task_name, n_obstacles=n_obj, walkable_cells=walkable,
        path_cells=len(path), path_length_m=length_m,
        n_waypoints=len(smoothed), waypoints_world=waypoints_world,
        occupancy_png=out_dir / "occupancy.png",
        overlay_png=overlay_path, json_path=json_path,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="genesis-nav plan")
    ap.add_argument("task_name", help="task name (with or without 'nav_' prefix)")
    ap.add_argument("--res", type=float, default=0.10)
    ap.add_argument("--safety", type=float, default=0.10)
    args = ap.parse_args(argv)
    try:
        plan(args.task_name, res=args.res, safety=args.safety)
    except (FileNotFoundError, RuntimeError) as e:
        print(f"[plan] {e}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
