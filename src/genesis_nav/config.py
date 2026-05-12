"""
Asset-path resolution for genesis-nav-bench.

Single source of truth for "where is the Husky URDF / Lightwheel root /
HDRI dir / Objaverse cache / etc." on this machine. Reads from (in order):

  1. Env vars (GENESIS_NAV_<KEY>)
  2. ~/.genesis_navrc (TOML, optional)
  3. Built-in defaults under $GENESIS_NAV_ROOT (default: ~/.genesis_nav)

Designed so the same code runs on different machines by changing one env
var (`GENESIS_NAV_ROOT`) or a TOML file, never the source.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path


def _expand(p: str | Path | None) -> Path | None:
    if p is None:
        return None
    return Path(os.path.expanduser(os.path.expandvars(str(p)))).resolve()


@dataclass(frozen=True)
class Paths:
    """Resolved asset paths."""
    root: Path                  # base dir; all defaults sit under here
    hdris: Path                 # *.hdr files
    ground_textures: Path       # *.jpg PBR ground textures
    wall_textures: Path
    husky_urdf: Path            # robot URDF
    lightwheel_root: Path | None       # Lightwheel_OpenSource/Manipulation/...
    simready_root: Path | None         # NVIDIA SimReady mirror
    simready_vegetation: Path | None   # restaurant demopack vegetation
    objaverse_cache: Path | None       # ~/.objaverse
    objaverse_index: Path | None       # JSON with {alias: {glb_path, scale, ...}}
    luisarender_bin: Path | None       # LuisaRenderPy.so dir
    go2_policy_dir: Path | None        # dir containing cfgs.pkl + model_<ckpt>.pt
    outputs: Path               # generated nav-task outputs


def _read_rc(rc_path: Path) -> dict:
    if not rc_path.exists():
        return {}
    with rc_path.open("rb") as f:
        return tomllib.load(f)


@cache
def paths() -> Paths:
    """Resolve and cache all asset paths."""
    rc = _read_rc(Path("~/.genesis_navrc").expanduser())
    p = rc.get("paths", {})

    def pick(env_key: str, rc_key: str, default: str | None) -> Path | None:
        v = os.environ.get(f"GENESIS_NAV_{env_key}") or p.get(rc_key) or default
        return _expand(v)

    root = pick("ROOT", "root", "~/.genesis_nav") or Path("~/.genesis_nav").expanduser()

    return Paths(
        root=root,
        hdris=pick("HDRI_DIR",            "hdris",              str(root / "assets/hdris")),
        ground_textures=pick("GROUND_DIR","ground_textures",    str(root / "assets/ground")),
        wall_textures=pick("WALL_DIR",    "wall_textures",      str(root / "assets/wall")),
        husky_urdf=pick("HUSKY_URDF",     "husky_urdf",         str(root / "assets/husky_bullet/husky.urdf")),
        lightwheel_root=pick("LIGHTWHEEL_ROOT", "lightwheel_root", str(root / "assets/Lightwheel_OpenSource")),
        simready_root=pick("SIMREADY_ROOT",     "simready_root",   str(root / "assets/simready")),
        simready_vegetation=pick("SIMREADY_VEGETATION", "simready_vegetation",
                                   str(root / "assets/simready/restaurant/Vegetation")),
        objaverse_cache=pick("OBJAVERSE_CACHE", "objaverse_cache", "~/.objaverse"),
        objaverse_index=pick("OBJAVERSE_INDEX", "objaverse_index",
                               str(root / "assets/asset_objaverse_index.json")),
        luisarender_bin=pick("LUISARENDER_BIN", "luisarender_bin", None),
        # Default: the policy bundled with the package (small enough to ship).
        go2_policy_dir=pick("GO2_POLICY_DIR", "go2_policy_dir",
                              str(Path(__file__).resolve().parent / "robots" / "go2_policy")),
        outputs=pick("OUTPUTS_DIR", "outputs", str(root / "outputs")),
    )


def hdri_path(name: str) -> Path:
    """Resolve an HDRI by its registry name → absolute .hdr path."""
    return paths().hdris / f"{name}.hdr"


def ground_texture_path(name: str) -> Path:
    """Resolve a ground texture by registry name → absolute .jpg path."""
    return paths().ground_textures / f"{name}.jpg"


def lightwheel_usd_path(category: str, nn: str | int) -> Path:
    """E.g. lightwheel_usd_path('Refrigerator', '043') → .../Refrigerator043_genesis.usd"""
    lw = paths().lightwheel_root
    if lw is None:
        raise RuntimeError("LIGHTWHEEL_ROOT not configured")
    nn = f"{int(nn):03d}" if str(nn).isdigit() else str(nn)
    return lw / "Manipulation" / f"{category}{nn}" / f"{category}{nn}_genesis.usd"


def vegetation_usd_path(species: str) -> Path:
    """E.g. vegetation_usd_path('Shumard_Oak') → .../Shumard_Oak_genesis.usd"""
    veg = paths().simready_vegetation
    if veg is None:
        raise RuntimeError("SIMREADY_VEGETATION not configured")
    return veg / species / f"{species}_genesis.usd"


def output_dir(task_name: str) -> Path:
    """Where to put outputs for a single task. Created if missing."""
    d = paths().outputs / f"nav_{task_name.removeprefix('nav_').removesuffix('.yaml')}"
    d.mkdir(parents=True, exist_ok=True)
    return d
