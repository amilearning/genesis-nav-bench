"""
Download HDRIs + PBR ground/wall textures from PolyHaven (CC0).

Saves to the directories configured in ~/.genesis_navrc (paths().hdris /
paths().ground_textures / paths().wall_textures).

CLI: `genesis-nav fetch [hdris] [textures] [walls]`
     Default: all three.
"""
from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

HDRI_URL = "https://dl.polyhaven.org/file/ph-assets/HDRIs/hdr/2k/{slug}_2k.hdr"
TEX_URL = {
    "diff":  "https://dl.polyhaven.org/file/ph-assets/Textures/jpg/2k/{slug}/{slug}_diff_2k.jpg",
    "nor":   "https://dl.polyhaven.org/file/ph-assets/Textures/jpg/2k/{slug}/{slug}_nor_gl_2k.jpg",
    "rough": "https://dl.polyhaven.org/file/ph-assets/Textures/jpg/2k/{slug}/{slug}_rough_2k.jpg",
}


# alias → PolyHaven slug. Curated default sets; users can add more via API.
DEFAULT_HDRIS: dict[str, str] = {
    "outdoor_sunny":    "kloofendal_43d_clear_puresky",
    "outdoor_dawn":     "kiara_1_dawn",
    "outdoor_sunset":   "belfast_sunset_puresky",
    "overcast_diffuse": "kloppenheim_06_puresky",
    "cloudy_dramatic":  "kloppenheim_02_puresky",
    "noon_tropical":    "kiara_5_noon",
    "dusk_savannah":    "qwantani_dusk_2_puresky",
    "night_clear":      "dikhololo_night",
    "forest_slope":     "forest_slope",
    "alpine":           "spaichingen_hill",
    "city_street":      "urban_courtyard_02",
    "indoor_warm":      "lebombo",
    "indoor_hall":      "studio_country_hall",
    "indoor_warehouse": "empty_warehouse_01",
    "indoor_workshop":  "aerodynamics_workshop",
    "warehouse":        "old_depot",
    "park":             "roof_garden",
}

DEFAULT_GROUND: dict[str, str] = {
    "grass":       "aerial_grass_rock",
    "concrete":    "rough_concrete",
    "dirt":        "rocky_terrain_02",
    "wood":        "wood_planks_grey",
    "tile":        "marble_01",
    "sand":        "aerial_beach_01",
    "mud":         "mud_cracked_dry_03",
    "gravel":      "gravel_concrete",
    "cobblestone": "cobblestone_floor_06",
}

DEFAULT_WALLS: dict[str, str] = {
    "brick_red":        "red_brick_03",
    "wood_panel":       "wood_planks",
    "castle_brick":     "castle_brick_07",
    "corrugated_iron":  "corrugated_iron_03",
    "plaster":          "plastered_wall_05",
    "weathered_planks": "weathered_planks",
}


def _fetch(url: str, dest: Path) -> bool:
    if dest.exists() and dest.stat().st_size > 1024:
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        urllib.request.urlretrieve(url, str(dest))
        return dest.stat().st_size > 1024
    except Exception as e:
        print(f"  FAIL {dest.name}: {e}", file=sys.stderr)
        if dest.exists(): dest.unlink()
        return False


def fetch_hdris(out_dir: Path, slugs: dict[str, str]) -> tuple[int, int]:
    """Download HDRIs. Returns (ok, fail)."""
    ok = fail = 0
    for alias, slug in slugs.items():
        dest = out_dir / f"{alias}.hdr"
        if dest.exists() and dest.stat().st_size > 1024:
            print(f"  [SKIP] {dest.name}"); ok += 1; continue
        print(f"  GET {alias}", end="… ", flush=True)
        if _fetch(HDRI_URL.format(slug=slug), dest):
            print(f"{dest.stat().st_size//1024} KB"); ok += 1
        else:
            fail += 1
    return ok, fail


def fetch_textures(out_dir: Path, slugs: dict[str, str], *, with_pbr_maps: bool = True) -> tuple[int, int]:
    """Download diffuse (+ normal + roughness if with_pbr_maps) for each texture slug."""
    ok = fail = 0
    for alias, slug in slugs.items():
        for tag, tmpl in TEX_URL.items():
            if tag != "diff" and not with_pbr_maps:
                continue
            suffix = "" if tag == "diff" else f"_{tag}"
            dest = out_dir / f"{alias}{suffix}.jpg"
            if dest.exists() and dest.stat().st_size > 1024:
                ok += 1; continue
            print(f"  GET {dest.name}", end="… ", flush=True)
            if _fetch(tmpl.format(slug=slug), dest):
                print(f"{dest.stat().st_size//1024} KB"); ok += 1
            else:
                fail += 1
    return ok, fail


def main(argv: list[str] | None = None) -> int:
    from genesis_nav.config import paths
    ap = argparse.ArgumentParser(prog="genesis-nav fetch")
    ap.add_argument("kinds", nargs="*",
                     choices=["hdris", "textures", "walls", "all"],
                     default=["all"],
                     help="which packs to fetch (default: all)")
    ap.add_argument("--no-pbr", action="store_true",
                     help="skip _nor / _rough maps; only diffuse")
    args = ap.parse_args(argv)
    kinds = set(args.kinds) if args.kinds else {"all"}
    if "all" in kinds: kinds |= {"hdris", "textures", "walls"}
    P = paths()
    tot_ok = tot_fail = 0
    if "hdris" in kinds:
        print(f"=== HDRIs → {P.hdris} ===")
        ok, fail = fetch_hdris(P.hdris, DEFAULT_HDRIS)
        tot_ok += ok; tot_fail += fail
    if "textures" in kinds:
        print(f"\n=== Ground textures → {P.ground_textures} ===")
        ok, fail = fetch_textures(P.ground_textures, DEFAULT_GROUND,
                                    with_pbr_maps=not args.no_pbr)
        tot_ok += ok; tot_fail += fail
    if "walls" in kinds:
        print(f"\n=== Wall textures → {P.wall_textures} ===")
        ok, fail = fetch_textures(P.wall_textures, DEFAULT_WALLS,
                                    with_pbr_maps=not args.no_pbr)
        tot_ok += ok; tot_fail += fail
    print(f"\n{tot_ok} ok, {tot_fail} failed")
    return 0 if tot_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
