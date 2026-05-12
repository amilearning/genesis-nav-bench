# Nav-task YAML schema (v2)

A nav-task config describes the environment + a single navigation problem
(start → goal). The robot is added separately by the runner script (which
uses the `robot` block to pick the right URDF/dynamics).

```yaml
scene:
  name: "outdoor_park_to_bench_v1"
  description: "Cross a grassy park, weave between trees, end near a bench."
  bounds: [-15, 15, -15, 15]            # [xmin, xmax, ymin, ymax] in m

robot:
  type: husky                            # one of: husky | iris_drone
                                         # Determines URDF, dynamics, body radius
                                         # See robots: in asset_registry.yaml
  start: [-12.0, 0.0]                    # world (x, y) — 2D start. z is set from
                                         # typical_spawn_z of the robot.
  goal:  [ 12.0, 0.0]                    # world (x, y) — 2D goal.

world:
  env_type: outdoor                      # outdoor | indoor
  sky:
    hdri: outdoor_sunny                  # filename (no .hdr) from
                                         # scenes/textures/hdris/<name>.hdr
    intensity: 1.0                       # optional, default 1.0
  ground:
    texture: grass                       # filename (no .jpg) from
                                         # scenes/textures/ground/<name>.jpg
    terrain:
      mode: random_uniform               # flat | random_uniform | sloped
      vertical_scale: 0.008              # for random_uniform: peak amplitude
      horizontal_scale: 0.25             # grid cell size, default 0.25
      subterrain_size: [30.0, 30.0]      # must cover scene.bounds
      slope: 0.05                        # for sloped only

objects:
  # Object types — pick the right one per asset:
  #
  #   type: box        size: [sx, sy, sz]
  #   type: cylinder   radius: r,  height: h
  #   type: sphere     radius: r
  #   type: usd        usd_name: tree_japanese_cherry      # key from asset_registry.usd_assets
  #                    OR usd_path: "/abs/path.usd"        # explicit override
  #                    scale: 1.0                          # default 1.0
  #   type: glb        glb_name: tree_pine                 # key from asset_objaverse_index.json
  #
  # Common fields (all types):
  #   name:     "<unique snake_case>"
  #   position: [x, y, z]                  # m, world frame; z = body CENTER for primitives,
  #                                        # z = ground level for USD/GLB (they rest on z)
  #   rotation: [roll, pitch, yaw]         # radians, ZYX. Optional, default [0,0,0].
  #   color:    [r, g, b]                  # primitives only. 0..1.
  #   surface:  "rough" | "smooth" | "reflective"   # primitives only.
  #   fixed:    bool                       # default true.

  - name: cherry_1
    type: usd
    usd_name: tree_japanese_cherry
    position: [-4.0, 3.0, 0.0]

  - name: bench_seat
    type: box
    size: [1.6, 0.4, 0.05]
    position: [10.0, -3.0, 0.45]
    color: [0.45, 0.30, 0.15]

  - name: tree_pine_objaverse
    type: glb
    glb_name: tree_pine
    position: [5.0, -7.0, 0.0]

spawn_zones:
  # Optional — named free-space spots usable as alternate starts/goals later.
  # Loader/planner currently ignores this; downstream task generation uses it.
  - {name: north_open, center: [0.0, 10.0], radius: 2.0}
  - {name: south_open, center: [0.0, -10.0], radius: 2.0}
```

## Constraints (Gemini must respect):
- `start` and `goal` MUST be inside `bounds` and at least `min_corridor_width`
  of the robot away from any object footprint.
- At least one collision-free corridor must exist between `start` and `goal`
  (A* must find a path). Don't completely fence the goal off.
- **Obstacles SHOULD sit between start and goal so the robot has to weave —
  trivial straight-line paths are not interesting tasks.** Aim for 2-5
  detours along the natural straight-line. Every passage stays
  ≥ `min_corridor_width`.
- For aerial robots, all primitives/USDs should leave a vertical corridor at
  the robot's spawn height.
- `world.sky.hdri` and `world.ground.texture` MUST be from the lists in
  `asset_registry.yaml`. Don't invent new names.
- Aim for 5-10 objects total — quality over quantity. Each obstacle should
  meaningfully contribute to the navigation challenge (i.e., force a detour).
