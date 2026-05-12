"""
Master script — programmatic end-to-end nav-task generation + simulation.

This is what `genesis-nav pipeline` does under the hood, but spelled out so
you can subclass / mock / pre-filter individual stages.

Run:
    export GEMINI_API_KEY=...
    LUISA=/abs/path/to/Genesis/genesis/ext/LuisaRender/build/bin   # for HDRI
    PYTHONPATH=$LUISA LD_LIBRARY_PATH=$LUISA \
        python examples/run_pipeline.py
"""
from genesis_nav import NavPipeline, NavRunner, NavTaskDesigner, NavPlanner


def main():
    # Configure each stage however you want. Defaults match the CLI defaults.
    pipeline = NavPipeline(
        designer=NavTaskDesigner(model="gemini-2.5-flash", temperature=0.6),
        planner =NavPlanner(res=0.10, safety=0.10),
        runner  =NavRunner(rasterizer=False),   # True to skip LuisaRender / HDRI
    )

    results = pipeline.run(
        description="Outdoor park with cherry trees and a fountain. "
                     "Husky weaves from west to east, ≥1.2 m clearance.",
        name="park_pipeline_demo2",
        robot="husky",
        bounds=(-15, 15, -15, 15),
    )

    d, p, r = results["design"], results["plan"], results["run"]
    print()
    print(f"design  : {d.n_objects} objects, env={d.env_type}, hdri={d.hdri}")
    print(f"          start={d.start}, goal={d.goal}")
    print(f"          yaml: {d.yaml_path}")
    print(f"plan    : A* {p.path_cells} cells, smoothed {p.n_waypoints} wp, "
           f"length {p.path_length_m:.1f} m")
    print(f"          {p.overlay_png}")
    print(f"run     : success={r.success}")
    print(f"          outputs: {r.output_dir}")


if __name__ == "__main__":
    main()
