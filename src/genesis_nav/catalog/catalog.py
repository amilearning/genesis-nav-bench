"""
Generate a single CSV + Markdown catalog of every scene / object / robot
asset available on this machine. Structured for LLM consumption.

CLI: `genesis-nav catalog [--out-csv ... --out-md ...]`
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path


def fsize_mb(p: Path) -> float:
    if not p.exists(): return 0.0
    if p.is_dir():
        return round(sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1e6, 2)
    return round(p.stat().st_size / 1e6, 3)


def build_catalog() -> list[dict]:
    """Collect rows describing all known assets. Returns list of dicts."""
    from genesis_nav.config import paths
    P = paths()
    rows: list[dict] = []
    def add(category, subcategory, name, description, file_path, fmt,
            license_, status, size_mb, notes=""):
        rows.append({
            "category": category, "subcategory": subcategory,
            "name": name, "description": description,
            "file_path": str(file_path) if file_path else "",
            "format": fmt, "license": license_, "status": status,
            "size_mb": size_mb, "notes": notes,
        })

    # Primitives
    for name, desc in [
        ("plane",    "Infinite checker plane"),
        ("box",      "AABB rectangular box"),
        ("cylinder", "Vertical cylinder"),
        ("sphere",   "Sphere"),
        ("terrain",  "Procedural heightmap (random_uniform / sloped / etc.)"),
    ]:
        add("primitive", "genesis-native", f"genesis_{name}", desc, "",
            "gs.morphs", "Apache-2.0", "ready", 0.0, f"gs.morphs.{name.capitalize()}(...)")

    # Husky robot
    if P.husky_urdf.exists():
        add("robot", "wheeled-rover", "husky_bullet",
            "Clearpath Husky A200 4-wheel skid-steer rover",
            P.husky_urdf, "URDF", "BSD-3", "ready",
            fsize_mb(P.husky_urdf.parent), "")

    # HDRIs
    if P.hdris.exists():
        for hdr in sorted(P.hdris.glob("*.hdr")):
            add("texture", "hdri", hdr.stem,
                "PolyHaven HDR equirect skybox",
                hdr, "HDR", "CC0 (PolyHaven)", "ready", fsize_mb(hdr), "")

    # Ground textures (just diffuse jpgs, not _nor / _rough)
    if P.ground_textures.exists():
        for jpg in sorted(P.ground_textures.glob("*.jpg")):
            if jpg.stem.endswith("_nor") or jpg.stem.endswith("_rough"):
                continue
            add("texture", "ground", jpg.stem,
                "PolyHaven PBR ground texture",
                jpg, "JPG", "CC0 (PolyHaven)", "ready", fsize_mb(jpg), "")

    # Lightwheel
    if P.lightwheel_root and P.lightwheel_root.exists():
        manip = P.lightwheel_root / "Manipulation"
        if manip.exists():
            for sub in sorted(manip.iterdir()):
                if not sub.is_dir(): continue
                m = re.match(r"^(.*?)(\d+)$", sub.name)
                cat = m.group(1) if m else sub.name
                conv = list(sub.glob("*_genesis.usd"))
                if not conv: continue
                add("object", f"lightwheel-{cat.lower()}",
                    f"lightwheel_{sub.name.lower()}",
                    f"{cat} variant {m.group(2) if m else ''}",
                    conv[0], "USD", "CC BY-NC 4.0 (Lightwheel)", "ready",
                    fsize_mb(sub), "MDL → UsdPreviewSurface")

    # NVIDIA Vegetation (USD trees)
    if P.simready_vegetation and P.simready_vegetation.exists():
        for sub in sorted(P.simready_vegetation.iterdir()):
            if not sub.is_dir(): continue
            conv = list(sub.glob("*_genesis.usd"))
            if not conv: continue
            add("object", "nvidia-vegetation",
                f"nvidia_{sub.name.lower()}",
                f"NVIDIA vegetation: {sub.name}",
                conv[0], "USD", "NVIDIA SimReady (free non-commercial)",
                "ready", fsize_mb(sub), "")

    # SimReady scenes
    if P.simready_root and P.simready_root.exists():
        for env_dir in sorted(P.simready_root.iterdir()):
            if not env_dir.is_dir(): continue
            usds = list(env_dir.glob("*.usd"))
            usd = next((u for u in usds if u.stem.endswith("_genesis")),
                        usds[0] if usds else None)
            if usd:
                add("scene", f"nvidia-simready-{env_dir.name.lower()}",
                    f"nvidia_simready_{env_dir.name.lower()}",
                    f"NVIDIA SimReady {env_dir.name}",
                    usd, "USD", "NVIDIA SimReady (free non-commercial)",
                    "ready" if usd.stem.endswith("_genesis") else "needs conversion",
                    fsize_mb(env_dir), "")

    return rows


def write_outputs(rows: list[dict], csv_path: Path, md_path: Path) -> None:
    fields = ["category", "subcategory", "name", "description", "file_path",
              "format", "license", "status", "size_mb", "notes"]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    by_cat = defaultdict(list)
    for r in rows: by_cat[r["category"]].append(r)
    cat_order = ["primitive", "robot", "scene", "scene-metadata", "object", "texture"]

    with open(md_path, "w") as f:
        f.write("# Asset catalog (Genesis-ready)\n\n")
        f.write(f"Total assets: **{len(rows)}**. Generated by `genesis-nav catalog`.\n\n")
        f.write("**For LLM use** (e.g. Gemini scene proposer): pick rows by `category` + ")
        f.write("`subcategory`, then reference `name` in your generated config.\n\n")
        f.write("## Overview by category\n\n| Category | Count |\n|---|---|\n")
        for cat in cat_order + [c for c in by_cat if c not in cat_order]:
            if cat in by_cat:
                f.write(f"| {cat} | {len(by_cat[cat])} |\n")
        f.write("\n")
        for cat in cat_order + [c for c in by_cat if c not in cat_order]:
            if cat not in by_cat: continue
            f.write(f"## {cat} ({len(by_cat[cat])})\n\n")
            f.write("| name | description | status | size_mb |\n|---|---|---|---|\n")
            for r in by_cat[cat]:
                f.write(f"| `{r['name']}` | {r['description']} | {r['status']} | {r['size_mb']:.2f} |\n")
            f.write("\n")


def main(argv: list[str] | None = None) -> int:
    from genesis_nav.config import paths
    ap = argparse.ArgumentParser(prog="genesis-nav catalog")
    ap.add_argument("--out-csv", default=None,
                     help="default: <root>/asset_catalog.csv")
    ap.add_argument("--out-md", default=None,
                     help="default: <root>/asset_catalog.md")
    args = ap.parse_args(argv)
    csv_path = Path(args.out_csv) if args.out_csv else paths().root / "asset_catalog.csv"
    md_path  = Path(args.out_md)  if args.out_md  else paths().root / "asset_catalog.md"

    rows = build_catalog()
    write_outputs(rows, csv_path, md_path)
    print(f"wrote {csv_path} ({len(rows)} rows)")
    print(f"wrote {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
