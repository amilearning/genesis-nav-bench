"""
`genesis-nav` top-level CLI. Dispatches to subcommands:

    genesis-nav design  --description ... --name ... --robot husky
    genesis-nav plan    <task_name>
    genesis-nav run     <task_name> [--rasterizer]
    genesis-nav pipeline --description ... --name ... [more]
    genesis-nav fetch [hdris|textures|walls|all]
    genesis-nav fetch-simready Simple_Warehouse Office Hospital
    genesis-nav convert <usd_path>
    genesis-nav convert-trees [veg_dir_or_usd]
    genesis-nav catalog
    genesis-nav bootstrap
    genesis-nav examples         # copy bundled example task YAMLs
    genesis-nav experiment       # run N random pipelines + aggregate metrics

Subcommands fan out to module-level main() functions.
"""
from __future__ import annotations

import argparse
import sys


def _pipeline(argv: list[str]) -> int:
    """Run design → plan → run in one shot, via NavPipeline."""
    from genesis_nav.pipeline import NavPipeline, NavRunner, NavTaskDesigner

    ap = argparse.ArgumentParser(prog="genesis-nav pipeline")
    ap.add_argument("--description", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--robot", default="husky")
    ap.add_argument("--bounds", default="-15,15,-15,15")
    ap.add_argument("--rasterizer", action="store_true")
    ap.add_argument("--model", default="gemini-2.5-flash")
    ap.add_argument("--temperature", type=float, default=0.6)
    ap.add_argument("--no-timestamp", action="store_true",
                     help="don't append _<YYYYMMDD_HHMMSS> to the task folder")
    args = ap.parse_args(argv)
    bounds = tuple(float(v) for v in args.bounds.split(","))

    pipeline = NavPipeline(
        designer=NavTaskDesigner(model=args.model, temperature=args.temperature),
        runner=NavRunner(rasterizer=args.rasterizer),
    )
    try:
        results = pipeline.run(description=args.description, name=args.name,
                                 robot=args.robot, bounds=bounds,
                                 timestamp=not args.no_timestamp)
    except Exception as e:
        print(f"[pipeline] {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    d, p, r = results["design"], results["plan"], results["run"]
    print(f"[pipeline] design  : {d.n_objects} obj, env={d.env_type}, hdri={d.hdri}")
    print(f"[pipeline] plan    : {p.n_waypoints} waypoints, {p.path_length_m:.1f} m")
    print(f"[pipeline] run     : output → {r.output_dir}")
    return 0 if r.success else 2


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__); return 0

    cmd, rest = argv[0], argv[1:]
    if cmd == "design":
        from genesis_nav.designer.designer import main as m
        return m(rest)
    if cmd == "plan":
        from genesis_nav.planner.astar import main as m
        return m(rest)
    if cmd == "run":
        from genesis_nav.runner.husky_drive import main as m
        return m(rest)
    if cmd == "pipeline":
        return _pipeline(rest)
    if cmd == "fetch":
        from genesis_nav.assets.polyhaven_fetcher import main as m
        return m(rest)
    if cmd == "fetch-simready":
        from genesis_nav.assets.simready_mirror import main as m
        return m(rest)
    if cmd == "convert":
        from genesis_nav.assets.mdl_to_preview import main as m
        return m(rest)
    if cmd == "convert-trees":
        from genesis_nav.assets.tree_mdl_to_preview import main as m
        return m(rest)
    if cmd == "catalog":
        from genesis_nav.catalog.catalog import main as m
        return m(rest)
    if cmd == "bootstrap":
        from genesis_nav.bootstrap import main as m
        return m(rest)
    if cmd == "examples":
        from genesis_nav.examples import main as m
        return m(rest)
    if cmd == "experiment":
        from genesis_nav.experiment import main as m
        return m(rest)
    if cmd == "version":
        from genesis_nav import __version__
        print(__version__); return 0
    print(f"unknown subcommand: {cmd!r}", file=sys.stderr)
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
