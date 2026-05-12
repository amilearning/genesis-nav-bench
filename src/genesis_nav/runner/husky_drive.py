"""
Husky-drive runner — stage 3 of the genesis-nav-bench pipeline.

Reads a nav-task YAML + planner's path.json, builds the Genesis scene
(LuisaRender path-traced if available, else rasterizer), spawns Husky at
start, drives along waypoints, saves fpv/boom/chase MP4s + trace.png.

Programmatic API:
    from genesis_nav.runner import run
    run("park_v1")                            # uses bundled paths
    run("park_v1", rasterizer=True)           # force rasterizer
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import torch
import yaml
from PIL import Image
from imageio import mimwrite

from genesis_nav.config import output_dir, paths
from genesis_nav.designer.prompts import load_registry_text


_REGISTRY: dict | None = None
def _registry() -> dict:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = yaml.safe_load(load_registry_text())
    return _REGISTRY


# --- Frame grab helper ----------------------------------------------
def _grab(c, *, is_sensor=False):
    if is_sensor:
        data = c.read()
        rgb = data.rgb if hasattr(data, "rgb") else data
    else:
        rgb = c.render()[0]
    if isinstance(rgb, torch.Tensor): rgb = rgb.cpu().numpy()
    if rgb.ndim == 4: rgb = rgb[0]
    if rgb.dtype in (np.float32, np.float64):
        rgb = (rgb*255 if rgb.max() <= 1.0 else rgb).clip(0, 255).astype(np.uint8)
    return rgb[:, :, :3].copy()


def _yaw_from_quat(q):
    if hasattr(q, "cpu"): q = q.cpu().numpy().flatten()
    w, x, y, z = q
    siny = 2*(w*z + x*y); cosy = 1 - 2*(y*y + z*z)
    return math.atan2(siny, cosy)

def _angle_diff(a, b):
    d = a - b
    while d >  math.pi: d -= 2*math.pi
    while d < -math.pi: d += 2*math.pi
    return d


def _color_surface(obj):
    import genesis as gs
    rgb = tuple(obj.get("color", [0.6, 0.6, 0.6]))
    style = obj.get("surface", "rough")
    if style == "smooth":     return gs.surfaces.Smooth(color=rgb)
    if style == "reflective": return gs.surfaces.Reflective(color=rgb)
    return gs.surfaces.Rough(color=rgb)


def _resolve_usd_path(obj) -> str:
    if "usd_path" in obj: return obj["usd_path"]
    name = obj.get("usd_name")
    if not name: raise ValueError("usd object missing usd_name or usd_path")
    entry = _registry().get("usd_assets", {}).get(name)
    if entry is None: raise KeyError(f"unknown usd_name '{name}'")
    if "path" in entry: return entry["path"]
    if "path_pattern" in entry:
        nn = obj.get("nn", "043")
        return entry["path_pattern"].replace("{NN}", str(nn))
    raise KeyError(f"usd_name '{name}' has no path or path_pattern")


def _resolve_glb(obj) -> tuple[str, float]:
    glb_index = json.loads(paths().objaverse_index.read_text()) \
        if paths().objaverse_index and paths().objaverse_index.exists() else {}
    name = obj.get("glb_name")
    if name not in glb_index:
        raise KeyError(f"unknown glb_name '{name}'")
    e = glb_index[name]
    return e["glb_path"], e["scale"]


# --- Scene + drive ---------------------------------------------------
def _build_scene(cfg, use_hdri):
    import genesis as gs
    P = paths()
    scene_kwargs = dict(sim_options=gs.options.SimOptions(dt=0.01),
                        show_viewer=False)
    world = cfg.get("world", {})
    sky = world.get("sky", {}) or {}
    hdri_name = sky.get("hdri")
    hdri_path = P.hdris / f"{hdri_name}.hdr" if hdri_name else None
    if use_hdri and hdri_path and hdri_path.exists():
        scene_kwargs["renderer"] = gs.renderers.RayTracer(
            env_surface=gs.surfaces.Emission(
                emissive_texture=gs.textures.ImageTexture(
                    image_path=str(hdri_path), encoding="linear"),
            ),
            env_radius=500.0, tracing_depth=8,
        )
    else:
        scene_kwargs["vis_options"] = gs.options.VisOptions(
            background_color=(0.55, 0.72, 0.92),
            ambient_light=(0.75, 0.75, 0.75),
            shadow=True,
            lights=[dict(type="directional", dir=(-0.4, -0.3, -0.9),
                          color=(1.0, 0.95, 0.85), intensity=5.0)],
        )
    scene = gs.Scene(**scene_kwargs)

    # Ground
    ground = world.get("ground", {}) or {}
    tex_name = ground.get("texture", "grass")
    tex_path = P.ground_textures / f"{tex_name}.jpg"
    surface = gs.surfaces.Default(
        diffuse_texture=gs.textures.ImageTexture(image_path=str(tex_path)),
        roughness=0.85,
    )
    terrain_cfg = ground.get("terrain") or {"mode": "flat"}
    mode = terrain_cfg.get("mode", "flat")
    if mode in ("random_uniform", "sloped"):
        bx0, bx1, by0, by1 = cfg["scene"]["bounds"]
        sx, sy = terrain_cfg.get("subterrain_size", [bx1-bx0, by1-by0])
        kind = "random_uniform_terrain" if mode == "random_uniform" else "pyramid_sloped_terrain"
        vs = float(terrain_cfg.get("vertical_scale" if mode == "random_uniform" else "slope", 0.008))
        scene.add_entity(
            gs.morphs.Terrain(
                pos=(bx0, by0, 0.0),
                n_subterrains=(1, 1),
                subterrain_size=(float(sx), float(sy)),
                horizontal_scale=float(terrain_cfg.get("horizontal_scale", 0.25)),
                vertical_scale=vs,
                subterrain_types=kind,
            ),
            surface=surface,
        )
    else:
        scene.add_entity(gs.morphs.Plane(), surface=surface)

    # Objects
    counters = {"box": 0, "cyl": 0, "sph": 0, "usd": 0, "glb": 0, "skip": 0}
    for obj in cfg.get("objects", []):
        t = obj["type"]; pos = tuple(map(float, obj["position"]))
        rot = tuple(map(float, obj.get("rotation", [0, 0, 0])))
        try:
            if t == "box":
                scene.add_entity(
                    gs.morphs.Box(size=tuple(obj["size"]), pos=pos, euler=rot,
                                   fixed=obj.get("fixed", True)),
                    surface=_color_surface(obj))
                counters["box"] += 1
            elif t == "cylinder":
                scene.add_entity(
                    gs.morphs.Cylinder(radius=obj["radius"], height=obj["height"],
                                        pos=pos, euler=rot, fixed=obj.get("fixed", True)),
                    surface=_color_surface(obj))
                counters["cyl"] += 1
            elif t == "sphere":
                scene.add_entity(
                    gs.morphs.Sphere(radius=obj["radius"], pos=pos,
                                      fixed=obj.get("fixed", True)),
                    surface=_color_surface(obj))
                counters["sph"] += 1
            elif t == "usd":
                p = _resolve_usd_path(obj)
                scene.add_entity(gs.morphs.USD(
                    file=p, pos=pos, euler=rot,
                    scale=float(obj.get("scale", 1.0)),
                    fixed=True, convexify=False, collision=False))
                counters["usd"] += 1
            elif t == "glb":
                p, scale = _resolve_glb(obj)
                scene.add_entity(gs.morphs.Mesh(
                    file=p, pos=pos, euler=rot,
                    scale=float(obj.get("scale", scale)),
                    fixed=True, collision=False))
                counters["glb"] += 1
            else:
                counters["skip"] += 1
        except Exception as e:
            counters["skip"] += 1
            print(f"  SKIP {obj.get('name')} ({t}): {type(e).__name__}: {str(e)[:160]}")
    print(f"[scene] " + " ".join(f"{k}={v}" for k, v in counters.items()))
    return scene


def _drive(scene, robot_cfg, waypoints, out_dir, use_hdri):
    import genesis as gs
    rb_meta = _registry()["robots"][robot_cfg["type"]]
    spawn_z = float(rb_meta["typical_spawn_z"])

    husky = scene.add_entity(gs.morphs.URDF(
        file=str(paths().husky_urdf),
        pos=(float(robot_cfg["start"][0]), float(robot_cfg["start"][1]), spawn_z),
        fixed=False,
    ))

    cam_class = gs.sensors.RaytracerCameraOptions if use_hdri else gs.sensors.RasterizerCameraOptions
    cam_extra = {"spp": 32, "denoise": True} if use_hdri else {}
    fpv_cam = scene.add_sensor(cam_class(
        res=(1280, 720), pos=(0.55, 0, 0.30), lookat=(3.0, 0, 0.30),
        fov=86.0, entity_idx=husky.idx, link_idx_local=0, **cam_extra))
    boom_cam = scene.add_sensor(cam_class(
        res=(1280, 720), pos=(-0.5, 0, 0.7), lookat=(1.5, 0, 0.0),
        fov=86.0, entity_idx=husky.idx, link_idx_local=0, **cam_extra))
    chase_cam = scene.add_camera(res=(1280, 720), pos=(-3, -3, 2.5),
                                  lookat=(2, 0, 0.4), fov=55, GUI=False)
    scene.build()

    wheel_dofs = torch.tensor([6, 7, 8, 9], device=gs.device, dtype=torch.int32)
    # kv=20 keeps wheel velocity tracking tight; kv=1.0 (the old default) only
    # achieved ~10% of commanded differential → tiny effective turn rate.
    husky.set_dofs_kv([20.0]*4, dofs_idx_local=wheel_dofs)
    for _ in range(60):
        husky.control_dofs_velocity(torch.zeros(4, device=gs.device), dofs_idx_local=wheel_dofs)
        scene.step()

    # ── Path smoothing + pure-pursuit controller ───────────────
    from genesis_nav.planner.smoother import densify_linear, path_length
    from genesis_nav.runner.pure_pursuit import PurePursuit, PurePursuitConfig
    # Linear densification keeps the planner's LOS-clear corridors intact.
    dense_path = densify_linear(waypoints, step=0.1)
    print(f"[drive] dense path: {len(dense_path)} points, {path_length(dense_path):.2f} m")
    follower = PurePursuit(dense_path, PurePursuitConfig(
        lookahead_m=0.6, target_v=1.0, max_omega=2.0,
        wheel_max=12.0, wheel_radius=0.178, track=0.555,
        goal_tolerance_m=0.30, slow_in_turn_threshold=0.4))

    fpv_frames, boom_frames, chase_frames = [], [], []
    xy_trace = []
    MAX_STEPS = 6000
    # Cross-track metric — distance from current pose to nearest dense waypoint.
    max_xtrack = 0.0
    for step_i in range(MAX_STEPS):
        pos = husky.get_pos().cpu().numpy().flatten()
        quat = husky.get_quat().cpu().numpy().flatten()
        yaw = _yaw_from_quat(quat)
        v_lin, omega = follower.step(float(pos[0]), float(pos[1]), yaw)
        v_l, v_r = follower.wheel_velocities(v_lin, omega)
        husky.control_dofs_velocity(
            torch.tensor([v_l, v_r, v_l, v_r], device=gs.device),
            dofs_idx_local=wheel_dofs)
        scene.step()
        xy_trace.append((float(pos[0]), float(pos[1])))
        # Cross-track distance to nearest path point (cheap; uses search_idx)
        nx, ny = dense_path[follower._search_idx]
        xtrack = math.hypot(nx - pos[0], ny - pos[1])
        if xtrack > max_xtrack: max_xtrack = xtrack
        if step_i % 4 == 0:
            fpv_frames.append(_grab(fpv_cam, is_sensor=True))
            boom_frames.append(_grab(boom_cam, is_sensor=True))
            chase_cam.set_pose(pos=(pos[0]-4, pos[1]-4, max(2.5, pos[2]+2.0)),
                                lookat=(pos[0]+2, pos[1], pos[2]))
            chase_frames.append(_grab(chase_cam))
            if step_i % 200 == 0:
                print(f"  step {step_i:4d}  pos=({pos[0]:+.2f},{pos[1]:+.2f}) "
                      f"v={v_lin:.2f} ω={omega:+.2f} xtrack={xtrack:.2f}")
        if follower.done:
            print(f"[drive] GOAL reached at step {step_i}")
            break

    final = xy_trace[-1]
    gx, gy = robot_cfg["goal"]
    goal_residual = math.hypot(final[0] - gx, final[1] - gy)
    print(f"[drive] final pos = ({final[0]:.2f}, {final[1]:.2f})  "
           f"goal residual = {goal_residual:.2f} m  max xtrack = {max_xtrack:.2f} m")
    mimwrite(str(out_dir / "fpv.mp4"), fpv_frames, fps=25)
    mimwrite(str(out_dir / "boom.mp4"), boom_frames, fps=25)
    mimwrite(str(out_dir / "chase.mp4"), chase_frames, fps=25)

    plan_overlay = out_dir / "path.png"
    if plan_overlay.exists():
        meta = json.loads((out_dir / "path.json").read_text())
        x0, x1, y0, y1 = meta["bounds"]
        res = meta["resolution_m"]
        trace_pil = Image.open(plan_overlay).convert("RGB").copy()
        for wx, wy in xy_trace:
            px = int(round((wx - x0)/res))
            py = int(round((wy - y0)/res))
            if 0 <= py < trace_pil.height and 0 <= px < trace_pil.width:
                trace_pil.putpixel((px, py), (255, 0, 255))
        trace_pil.save(out_dir / "trace.png")


# --- Public API + CLI -----------------------------------------------
def _luisarender_available() -> bool:
    bin_dir = paths().luisarender_bin
    if not bin_dir or not bin_dir.exists():
        return False
    return any(p.name.startswith("LuisaRenderPy") for p in bin_dir.glob("LuisaRenderPy*"))


def run(task_name: str, *, rasterizer: bool = False) -> Path:
    """Run one nav task. Returns the output dir."""
    task_name = task_name.removeprefix("nav_").removesuffix(".yaml")
    yaml_path = paths().root / "configs" / f"nav_{task_name}.yaml"
    out_dir = output_dir(task_name)
    path_json = out_dir / "path.json"
    if not yaml_path.exists():
        raise FileNotFoundError(f"task YAML not found at {yaml_path}")
    if not path_json.exists():
        raise FileNotFoundError(f"plan not found at {path_json}; run plan first")

    cfg = yaml.safe_load(yaml_path.read_text())
    path_data = json.loads(path_json.read_text())
    waypoints = path_data["waypoints"]

    use_hdri = not rasterizer and _luisarender_available()
    if rasterizer is False and not use_hdri:
        print("[run] LuisaRender not available — falling back to rasterizer")

    import genesis as gs
    print(f"[run] booting Genesis (use_hdri={use_hdri})")
    gs.init(backend=gs.cuda)
    scene = _build_scene(cfg, use_hdri=use_hdri)
    _drive(scene, cfg["robot"], waypoints, out_dir, use_hdri=use_hdri)
    print(f"[run] DONE → {out_dir}")
    return out_dir


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="genesis-nav run")
    ap.add_argument("task_name")
    ap.add_argument("--rasterizer", action="store_true",
                     help="force rasterizer renderer (skip HDRI/LuisaRender)")
    args = ap.parse_args(argv)
    try:
        run(args.task_name, rasterizer=args.rasterizer)
    except FileNotFoundError as e:
        print(f"[run] {e}", file=sys.stderr); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
