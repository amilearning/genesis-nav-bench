"""
Go2 navigation drive — Stage 3 for robot.type == "go2".

Loads the trained Go2 velocity-tracking policy (rsl-rl), reuses our planner's
A* waypoints. Pure-pursuit produces `(v_lin, omega)` which becomes the policy
command [vx, vy=0, ωz]. The policy outputs joint target deltas at 50 Hz; the
Go2Env steps the sim. We capture FPV + boom + chase camera streams + a trace
overlay onto the planned path.

Required:
  - paths().go2_policy_dir = directory containing `cfgs.pkl` + `model_<ckpt>.pt`
  - `pip install rsl-rl-lib>=5.0.0`
  - Genesis assets bundled (go2.urdf + plane.urdf are already shipped)

The trained policy was trained on a FLAT plane → terrain.mode MUST be flat
when robot.type=go2. The planner is fine with any obstacle layout because
we run obstacles as visual-only (collision=False).
"""
from __future__ import annotations

import json
import math
import os
import pickle
import time
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


# Helpers shared with husky_drive (re-importing keeps fixes in one place).
from genesis_nav.planner.astar import _resolve_task_yaml
from genesis_nav.runner.husky_drive import (
    _grab, _yaw_from_quat, _color_surface, _resolve_usd_path, _resolve_glb,
    _luisarender_available,
)


def _spawn_objects_callback(cfg):
    """Return a function that adds the YAML's textured ground + objects to a
    scene before build. Used as Go2Env._extra_objects_fn."""
    def fn(scene):
        import genesis as gs
        from genesis_nav.config import paths as _paths
        # ── Textured ground (replaces the URDF plane Go2Env would've added) ──
        ground_cfg = (cfg.get("world", {}) or {}).get("ground", {}) or {}
        tex_name = ground_cfg.get("texture", "grass")
        tex_path = _paths().ground_textures / f"{tex_name}.jpg"
        if tex_path.exists():
            scene.add_entity(
                gs.morphs.Plane(),
                surface=gs.surfaces.Default(
                    diffuse_texture=gs.textures.ImageTexture(image_path=str(tex_path)),
                    roughness=0.85,
                ),
            )
        else:
            # Fallback: bare Plane (Genesis default checker) if the texture is missing
            scene.add_entity(gs.morphs.Plane())

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
        print(f"[scene] go2: " + " ".join(f"{k}={v}" for k, v in counters.items()))
    return fn


def run_go2(task_name: str, *, ckpt: int = 499, rasterizer: bool = False) -> Path:
    """Run Go2 navigation policy along the planned path. Writes outputs to
    output_dir(task_name)/."""
    task_name = task_name.removeprefix("nav_").removesuffix(".yaml")
    yaml_path = _resolve_task_yaml(task_name, None)
    out = output_dir(task_name)
    path_json = out / "path.json"
    if not path_json.exists():
        raise FileNotFoundError(f"plan not found at {path_json}; run plan first")

    cfg = yaml.safe_load(yaml_path.read_text())
    path_data = json.loads(path_json.read_text())
    waypoints = path_data["waypoints"]

    # The trained policy is flat-only. Force terrain to flat to avoid surprises.
    cfg.setdefault("world", {}).setdefault("ground", {})["terrain"] = {"mode": "flat"}

    # Resolve the policy directory.
    policy_dir = paths().go2_policy_dir
    if policy_dir is None or not policy_dir.exists():
        raise FileNotFoundError(
            f"Go2 policy dir not found: {policy_dir}. "
            f"Set `go2_policy_dir` in ~/.genesis_navrc or env GENESIS_NAV_GO2_POLICY_DIR.")
    cfgs_pkl = policy_dir / "cfgs.pkl"
    model_pt = policy_dir / f"model_{ckpt}.pt"
    if not cfgs_pkl.exists():
        raise FileNotFoundError(f"cfgs.pkl not found in {policy_dir}")
    if not model_pt.exists():
        raise FileNotFoundError(f"model_{ckpt}.pt not found in {policy_dir}")

    # rsl-rl import (heavy)
    from importlib import metadata
    try:
        if int(metadata.version("rsl-rl-lib").split(".")[0]) < 5:
            raise ImportError
    except (metadata.PackageNotFoundError, ImportError, ValueError) as e:
        raise ImportError("pip install 'rsl-rl-lib>=5.0.0'") from e
    from rsl_rl.runners import OnPolicyRunner
    import genesis as gs
    from genesis_nav.robots.go2_env import Go2Env

    print(f"[go2] booting Genesis...")
    gs.init(backend=gs.gpu, precision="32", logging_level="warning")

    with open(cfgs_pkl, "rb") as f:
        env_cfg, obs_cfg, reward_cfg, command_cfg, train_cfg = pickle.load(f)
    reward_cfg["reward_scales"] = {}
    # Nav drive can take up to 60 s; the training-time episode_length_s
    # (20 s) would cause Go2 to auto-reset mid-mission. Push it way up.
    env_cfg["episode_length_s"] = 600.0
    env_cfg["resampling_time_s"] = 600.0   # we set commands manually anyway

    # Spawn Go2 at the planner start, with proper z from registry.
    go2_spec = _registry()["robots"]["go2"]
    spawn_pos = list(map(float, cfg["robot"]["start"])) + [float(go2_spec["typical_spawn_z"])]
    spawn_quat = [1.0, 0.0, 0.0, 0.0]   # facing +X to start; pure-pursuit will turn it

    # Optional LuisaRender path-traced rendering with HDRI sky.
    use_hdri = (not rasterizer) and _luisarender_available()
    if (not rasterizer) and not use_hdri:
        print("[go2] LuisaRender not available — falling back to rasterizer")
    renderer = None
    if use_hdri:
        sky = (cfg.get("world", {}) or {}).get("sky", {}) or {}
        hdri_name = sky.get("hdri")
        hdri_path = paths().hdris / f"{hdri_name}.hdr" if hdri_name else None
        if hdri_path and hdri_path.exists():
            renderer = gs.renderers.RayTracer(
                env_surface=gs.surfaces.Emission(
                    emissive_texture=gs.textures.ImageTexture(
                        image_path=str(hdri_path), encoding="linear"),
                ),
                env_radius=500.0, tracing_depth=8,
            )
        else:
            print(f"[go2] HDRI {hdri_name!r} not found; raytracer skipped")
            use_hdri = False
    print(f"[go2] booting Go2Env (use_hdri={use_hdri})")

    env = Go2Env.__new__(Go2Env)
    env._spawn_pos = spawn_pos
    env._spawn_quat = spawn_quat
    env._skip_default_plane = True   # our _extra_objects_fn adds a textured Plane instead
    env._renderer = renderer
    env._use_raytracer_cams = use_hdri
    env._add_camera = True
    env._add_fpv_camera = True
    env._add_boom_camera = True
    # Go2 cam tuning (smaller body than Husky):
    env._fpv_pos    = (0.30, 0.0, 0.10);   env._fpv_lookat  = (3.0, 0.0, 0.05)
    env._fpv_fov    = 86.0
    env._boom_pos   = (-0.7, 0.0, 0.50);   env._boom_lookat = (1.5, 0.0, 0.05)
    env._boom_fov   = 80.0
    env._extra_objects_fn = _spawn_objects_callback(cfg)
    Go2Env.__init__(env, num_envs=1, env_cfg=env_cfg, obs_cfg=obs_cfg,
                    reward_cfg=reward_cfg, command_cfg=command_cfg, show_viewer=False)

    # Load policy
    runner = OnPolicyRunner(env, train_cfg, str(policy_dir), device=gs.device)
    runner.load(str(model_pt))
    policy = runner.get_inference_policy(device=gs.device)

    # Pure-pursuit on the waypoints
    from genesis_nav.planner.smoother import densify_linear, path_length
    from genesis_nav.runner.pure_pursuit import PurePursuit, PurePursuitConfig
    dense_path = densify_linear(waypoints, step=0.1)
    print(f"[go2] dense path: {len(dense_path)} points, {path_length(dense_path):.2f} m")
    follower = PurePursuit(dense_path, PurePursuitConfig(
        lookahead_m=0.6, target_v=float(go2_spec.get("target_speed_mps", 0.5)),
        max_omega=1.5, wheel_max=1.0,    # ω limit; wheel_max irrelevant (Go2 uses joint policy)
        wheel_radius=0.1, track=0.3,
        goal_tolerance_m=0.60,           # Go2 can't reverse, give it slack to "stop near" the goal
        slow_in_turn_threshold=0.4,
    ))

    obs_dict = env.reset()

    fpv_frames, boom_frames, chase_frames = [], [], []
    xy_trace = []
    max_xtrack = 0.0
    sim_steps = 0
    goal_reached_step: int | None = None
    drive_t0 = time.perf_counter()
    MAX_STEPS = 3000     # 60 s sim at 50 Hz
    CAPTURE_EVERY = 2    # 25 fps from 50 Hz
    CHASE_OFFSET = (-1.5, 0.5, 0.8)
    # Overshoot detector — Go2 can't reverse, so if dist-to-goal has been
    # growing for many steps after a close approach, stop the drive.
    gx_goal, gy_goal = cfg["robot"]["goal"]
    min_dist_to_goal = float("inf")
    overshoot_stable_steps = 0
    from scipy.spatial.transform import Rotation as R

    with torch.no_grad():
        for step_i in range(MAX_STEPS):
            sim_steps = step_i + 1
            # Pure-pursuit reads pose
            pos = env.robot.get_pos().cpu().numpy().flatten()
            quat = env.robot.get_quat().cpu().numpy().flatten()
            yaw = _yaw_from_quat(quat)
            v_lin, omega = follower.step(float(pos[0]), float(pos[1]), yaw)
            # Hand the (vx, 0, ωz) command to the env BEFORE policy infers
            env.commands[0, 0] = float(v_lin)
            env.commands[0, 1] = 0.0
            env.commands[0, 2] = float(omega)

            # Run policy + step env (which builds the next observation)
            actions = policy(obs_dict)
            obs_dict, _, _, _ = env.step(actions)

            xy_trace.append((float(pos[0]), float(pos[1])))
            # Cross-track distance to nearest dense waypoint
            nx, ny = dense_path[follower._search_idx]
            xtrack = math.hypot(nx - pos[0], ny - pos[1])
            if xtrack > max_xtrack: max_xtrack = xtrack

            if step_i % CAPTURE_EVERY == 0:
                fpv_frames.append(_grab(env.fpv_cam, is_sensor=True))
                boom_frames.append(_grab(env.boom_cam, is_sensor=True))
                rot = R.from_quat([quat[1], quat[2], quat[3], quat[0]])
                fwd = rot.apply([1.0, 0.0, 0.0])
                cam_off = rot.apply(CHASE_OFFSET)
                env.cam.set_pose(
                    pos=(pos[0]+cam_off[0], pos[1]+cam_off[1], pos[2]+cam_off[2]),
                    lookat=(pos[0]+fwd[0]*2, pos[1]+fwd[1]*2, pos[2]+0.3),
                )
                chase_frames.append(_grab(env.cam))
                if step_i % 100 == 0:
                    print(f"  step {step_i:4d}  pos=({pos[0]:+.2f},{pos[1]:+.2f}) "
                           f"v_cmd={v_lin:.2f} ω_cmd={omega:+.2f} xtrack={xtrack:.2f}")

            if follower.done:
                goal_reached_step = step_i
                print(f"[go2] GOAL reached at step {step_i}")
                break
            # Overshoot detector
            d2g = math.hypot(pos[0] - gx_goal, pos[1] - gy_goal)
            if d2g < min_dist_to_goal:
                min_dist_to_goal = d2g
                overshoot_stable_steps = 0
            elif d2g > min_dist_to_goal + 0.15:
                overshoot_stable_steps += 1
                if overshoot_stable_steps > 50 and min_dist_to_goal < 1.2:
                    print(f"[go2] overshoot detected (min dist was {min_dist_to_goal:.2f} m); stopping")
                    goal_reached_step = step_i
                    break

    drive_wall_s = time.perf_counter() - drive_t0
    sim_seconds = sim_steps * float(env.dt)
    final = xy_trace[-1]
    gx, gy = cfg["robot"]["goal"]
    goal_residual = math.hypot(final[0] - gx, final[1] - gy)
    print(f"[go2] final pos = ({final[0]:.2f}, {final[1]:.2f})  "
           f"goal residual = {goal_residual:.2f} m  max xtrack = {max_xtrack:.2f} m  "
           f"sim={sim_seconds:.1f}s, wall={drive_wall_s:.1f}s")

    fps = int(round(1.0 / float(env.dt) / CAPTURE_EVERY))
    mimwrite(str(out / "fpv.mp4"),   fpv_frames,   fps=fps)
    mimwrite(str(out / "boom.mp4"),  boom_frames,  fps=fps)
    mimwrite(str(out / "chase.mp4"), chase_frames, fps=fps)
    print(f"[go2] saved fpv/boom/chase.mp4 ({len(fpv_frames)} frames each, fps={fps})")

    # Trace overlay
    plan_overlay = out / "path.png"
    if plan_overlay.exists():
        meta = json.loads((out / "path.json").read_text())
        x0, x1, y0, y1 = meta["bounds"]
        res = meta["resolution_m"]
        trace_pil = Image.open(plan_overlay).convert("RGB").copy()
        for wx, wy in xy_trace:
            px = int(round((wx - x0)/res))
            py = int(round((wy - y0)/res))
            if 0 <= py < trace_pil.height and 0 <= px < trace_pil.width:
                trace_pil.putpixel((px, py), (255, 0, 255))
        trace_pil.save(out / "trace.png")

    # Drive metrics
    (out / "drive_metrics.json").write_text(json.dumps({
        "goal_reached": goal_reached_step is not None,
        "goal_reached_step": goal_reached_step,
        "sim_steps": sim_steps,
        "sim_seconds": round(sim_seconds, 3),
        "wall_seconds": round(drive_wall_s, 3),
        "final_pos": [round(final[0], 3), round(final[1], 3)],
        "goal_pos": [float(gx), float(gy)],
        "goal_residual_m": round(goal_residual, 3),
        "max_xtrack_m": round(max_xtrack, 3),
        "n_frames": len(fpv_frames),
        "robot": "go2",
        "policy_dir": str(policy_dir),
        "policy_ckpt": ckpt,
    }, indent=2))
    return out
