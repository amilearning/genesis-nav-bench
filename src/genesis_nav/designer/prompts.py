"""Gemini prompts for nav-task design."""
from __future__ import annotations

from importlib.resources import files

# --- System prompt: persona + hard rules -----------------------------
SYSTEM_PROMPT = """You are a robotics navigation environment designer.

You will design a Genesis simulation scene + a navigation task as a YAML file.
The scene contains the environment (ground, terrain, props, lighting, sky)
PLUS a `robot` block with start and goal positions.

Output rules:
- Reply with VALID YAML ONLY. No prose, no markdown fences, no code blocks.
- All coordinates are SI: meters and radians.
- All position fields are world-frame Cartesian [x, y, z].
- z is the body CENTER for primitives, not the bottom — e.g. a box of height 0.4
  sitting on the ground has z=0.2. For USD/GLB assets, z is the ground level
  (the asset itself defines where its base sits).
- Avoid overlapping objects.
- Obstacles SHOULD be placed between start and goal so the robot has to weave
  around them — straight-line clear corridors are NOT interesting tasks.
  Aim for 2-5 detours along the natural straight line. Just ensure at least
  one navigable route exists (A* must find SOME path) and every passage is
  at least `min_corridor_width` wide for the robot — see asset_registry.robots.
- Keep all objects within scene.bounds.
- Use 5-10 objects total. Quality over quantity — each obstacle should
  meaningfully contribute to the navigation challenge.
- Use only HDRI / ground_texture / terrain_mode / usd_name / glb_name from the
  asset registry. Don't invent new names.
- The HDRI must match env_type (outdoor HDRIs for outdoor scenes,
  indoor HDRIs for indoor scenes)."""


# --- User prompt: per-call substitutions ------------------------------
PROMPT_TEMPLATE = """Design ONE Genesis nav-task scene with the following intent:

  Description: {description}
  Robot:       {robot}        ({robot_summary})
  Bounds:      x in [{xmin}, {xmax}], y in [{ymin}, {ymax}]
  Scene name:  {name}

# Asset registry — what you can place + which HDRIs/textures/terrain are available:
{registry}

# Schema — your output MUST conform to this:
{schema}

Now write the nav-task YAML. Pick env_type that matches the description, choose
a matching HDRI + ground texture from the registry, pick terrain (flat or
random_uniform or sloped), spawn 5-10 objects (some of them ALONG the
straight-line from start to goal so the robot has to detour around them), and
set a `robot.start` and `robot.goal` that have SOME navigable route (A* must
succeed) — preferably a non-trivial one. No fences, no markdown — YAML only.
"""


# Concise per-robot summary surfaced into the prompt so Gemini sees the
# important constraint without re-parsing the whole registry.
ROBOT_SUMMARIES = {
    "husky": (
        "Ground rover, 0.99 × 0.67 m footprint, radius 0.5 m, "
        "needs ≥1.2 m corridor, climbs ≤10 cm bumps. Spawn z=0.2."
    ),
    "iris_drone": (
        "Aerial quadrotor, ~0.5 m wide, radius 0.4 m, "
        "needs ≥0.8 m vertical+lateral corridor. Spawn z=1.5."
    ),
}


def load_registry_text() -> str:
    """Read the bundled asset_registry.yaml as text."""
    return (files("genesis_nav.designer.data") / "asset_registry.yaml").read_text()


def load_schema_text() -> str:
    """Read the bundled scene_config_schema.md as text."""
    return (files("genesis_nav.designer.data") / "scene_config_schema.md").read_text()
