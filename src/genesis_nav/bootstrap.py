"""
`genesis-nav bootstrap` — one-shot setup.

Writes ~/.genesis_navrc with sensible defaults, creates the asset dir tree,
and optionally kicks off PolyHaven downloads. Prints clear instructions for
the asset packs you have to download manually (Lightwheel, SimReady,
Objaverse, Genesis sim, LuisaRender).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


RC_TEMPLATE = """# genesis-nav-bench: machine-local asset paths.
# Override any of these via env var GENESIS_NAV_<KEY>.
[paths]
root                 = "{root}"
hdris                = "{root}/assets/hdris"
ground_textures      = "{root}/assets/ground"
wall_textures        = "{root}/assets/wall"
husky_urdf           = "{root}/assets/husky_bullet/husky.urdf"
lightwheel_root      = "{root}/assets/Lightwheel_OpenSource"
simready_root        = "{root}/assets/simready"
simready_vegetation  = "{root}/assets/simready/restaurant/Vegetation"
objaverse_cache      = "~/.objaverse"
objaverse_index      = "{root}/assets/asset_objaverse_index.json"
outputs              = "{root}/outputs"
# Optional: only needed for LuisaRender path-traced HDRI rendering
# luisarender_bin    = "/abs/path/to/Genesis/genesis/ext/LuisaRender/build/bin"
"""


EXTERNAL_ASSETS_GUIDE = """\
=== External asset packs (you have to fetch these yourself) ===

1. Genesis simulator + optional LuisaRender path tracer
   pip install genesis-world
   LuisaRender: build from source — see Genesis docs
   Then set in ~/.genesis_navrc:
     luisarender_bin = "/abs/path/to/Genesis/genesis/ext/LuisaRender/build/bin"

2. PolyHaven HDRIs + PBR textures (CC0)
   genesis-nav fetch        # already done if you said yes during bootstrap

3. NVIDIA SimReady envs (free non-commercial)
   genesis-nav fetch-simready Simple_Warehouse Office Hospital

4. NVIDIA Vegetation USD trees (part of the Restaurant Demopack)
   Manual download:
     https://docs.omniverse.nvidia.com/...    # check NVIDIA's portal
   Place under {simready_vegetation}/
   Then convert MDL → UsdPreviewSurface:
     genesis-nav convert-trees

5. Lightwheel 243 kitchen appliances (CC BY-NC 4.0)
   Google Drive zip (3.4 GB):
     https://github.com/LightwheelAI/Lightwheel-simready-asset
   Extract to {lightwheel_root}/
   Then convert each subdir to *_genesis.usd with:
     for d in {lightwheel_root}/Manipulation/*/; do
       genesis-nav convert "$d/$(basename $d).usd"
     done

6. Objaverse photoreal GLBs
   pip install objaverse
   Pre-built index with curated tree / car / hedge UIDs:
     {objaverse_index}
   GLBs cache locally to ~/.objaverse/ on first load.

7. Husky URDF + meshes (BSD-3, bullet3 distribution)
   git clone https://github.com/bulletphysics/bullet3
   cp -r bullet3/data/husky {husky_urdf_dir}
   The package's runner uses {husky_urdf} as the URDF.

8. Gemini API key (for `genesis-nav design`)
   Get one at https://aistudio.google.com/apikey
   export GEMINI_API_KEY=...

=== Verify your install ===

   genesis-nav catalog        # see what the package can find
   genesis-nav --help         # all subcommands
"""


def make_rc(root: Path, overwrite: bool = False) -> Path:
    rc_path = Path("~/.genesis_navrc").expanduser()
    if rc_path.exists() and not overwrite:
        print(f"  [SKIP] {rc_path} already exists (use --overwrite-rc to replace)")
        return rc_path
    rc_path.write_text(RC_TEMPLATE.format(root=str(root)))
    print(f"  wrote {rc_path}")
    return rc_path


def make_tree(root: Path) -> None:
    for sub in ("assets/hdris", "assets/ground", "assets/wall",
                "assets/husky_bullet", "assets/Lightwheel_OpenSource",
                "assets/simready", "configs", "outputs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    print(f"  created dir tree under {root}/")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="genesis-nav bootstrap")
    ap.add_argument("--root", default="~/.genesis_nav",
                     help="base asset dir; default ~/.genesis_nav")
    ap.add_argument("--fetch", action="store_true",
                     help="also run `genesis-nav fetch` for PolyHaven assets")
    ap.add_argument("--overwrite-rc", action="store_true",
                     help="replace ~/.genesis_navrc if it exists")
    args = ap.parse_args(argv)

    root = Path(os.path.expanduser(args.root)).resolve()
    print(f"=== bootstrap → {root} ===")
    make_tree(root)
    make_rc(root, overwrite=args.overwrite_rc)

    if args.fetch:
        from genesis_nav.assets.polyhaven_fetcher import main as fetch_main
        rc = fetch_main(["all"])
        if rc != 0:
            print("  (some PolyHaven downloads failed — re-run `genesis-nav fetch` later)")

    # Show the external-asset guide. Use current config to interpolate paths.
    from genesis_nav.config import paths
    paths.cache_clear()    # re-read with new RC
    P = paths()
    print(EXTERNAL_ASSETS_GUIDE.format(
        simready_vegetation=P.simready_vegetation,
        lightwheel_root=P.lightwheel_root,
        objaverse_index=P.objaverse_index,
        husky_urdf_dir=P.husky_urdf.parent if P.husky_urdf else "<root>/assets/husky_bullet",
        husky_urdf=P.husky_urdf,
    ))
    return 0


if __name__ == "__main__":
    sys.exit(main())
