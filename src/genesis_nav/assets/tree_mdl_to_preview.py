"""
Convert NVIDIA Vegetation tree USDs (Shumard_Oak.usd etc.) to Genesis-loadable
form. These trees reference OmniPBR via .mdl files whose texture paths are
baked INTO the .mdl source, NOT authored on the USD shader prim — so the
generic `mdl_to_preview` converter can't see them.

This script reads each tree's materials/*.mdl as text, regex-extracts the
texture paths (`diffuse_texture`, `normalmap_texture`, `reflectionroughness_texture`),
and rewrites the USD with a UsdPreviewSurface graph using the extracted paths.

Programmatic API:
    from genesis_nav.assets.tree_mdl_to_preview import convert_tree
    convert_tree(Path("/abs/Shumard_Oak.usd"))

CLI: `genesis-nav convert-trees [path_or_dir]`
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

TREE_NAMES = [
    "Shumard_Oak", "Black_Oak", "Scarlet_Oak", "Hawthorn",
    "Common_Apple", "Dogwood", "Blue_Berry_Elder",
    "Sweet_Mock_Orange", "Japanese_Cherry",
]


def parse_mdl_textures(mdl_path: Path) -> dict[str, str]:
    """Return {canonical_input_name → absolute texture path} extracted from .mdl source."""
    txt = mdl_path.read_text(errors="ignore")
    canon_map = {
        "diffuse_texture":            "diffuse",
        "normalmap_texture":          "normal",
        "reflectionroughness_texture":"roughness",
        "ORM_texture":                "orm",
    }
    out: dict[str, str] = {}
    for m in re.finditer(r'(\w+):\s*texture_2d\(\s*"([^"]+)"', txt, flags=re.MULTILINE):
        param, rel = m.group(1), m.group(2)
        abs_path = (mdl_path.parent / rel).resolve()
        if not abs_path.exists():
            continue
        canon = canon_map.get(param)
        if canon:
            out[canon] = str(abs_path)
    return out


def convert_tree(usd_path: Path, out_path: Path | None = None) -> Path | None:
    try:
        from pxr import Usd, UsdShade, Sdf
    except ImportError as e:
        raise RuntimeError("usd-core not installed. `pip install usd-core`") from e

    if not usd_path.exists():
        print(f"  SKIP {usd_path} (missing)"); return None
    if out_path is None:
        out_path = usd_path.with_name(usd_path.stem + "_genesis.usd")

    stage = Usd.Stage.Open(str(usd_path))
    if stage is None:
        print(f"  FAIL open {usd_path}"); return None

    shaders = []
    for prim in stage.Traverse():
        if prim.GetTypeName() != "Shader": continue
        src = prim.GetAttribute("info:mdl:sourceAsset")
        if not (src and src.IsValid() and src.Get() is not None):
            continue
        shaders.append(prim)

    n = 0
    for prim in shaders:
        src = prim.GetAttribute("info:mdl:sourceAsset")
        v = src.Get()
        mdl_rel = v.path if hasattr(v, "path") else str(v)
        mdl_path = (usd_path.parent / mdl_rel).resolve()
        if not mdl_path.exists():
            continue
        textures = parse_mdl_textures(mdl_path)
        if not textures: continue

        material_prim = prim.GetParent()
        if not material_prim.IsValid(): continue
        stage.RemovePrim(prim.GetPath())

        mat = UsdShade.Material(material_prim)
        mat_path = material_prim.GetPath()
        surf = UsdShade.Shader.Define(stage, mat_path.AppendChild("Surface"))
        surf.CreateIdAttr("UsdPreviewSurface")
        mat.CreateSurfaceOutput().ConnectToSource(surf.ConnectableAPI(), "surface")

        uv = UsdShade.Shader.Define(stage, mat_path.AppendChild("uv_reader"))
        uv.CreateIdAttr("UsdPrimvarReader_float2")
        uv.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
        uv_out = uv.CreateOutput("result", Sdf.ValueTypeNames.Float2)

        if "diffuse" in textures:
            tex = UsdShade.Shader.Define(stage, mat_path.AppendChild("diffuse_tex"))
            tex.CreateIdAttr("UsdUVTexture")
            tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(textures["diffuse"])
            tex.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(uv_out)
            tex.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
            rgb = tex.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
            surf.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(rgb)
            # NB: leaf diffuse textures are RGB-only — wiring `opacity` to the alpha
            # channel crashes Genesis's USD loader because it tries to read
            # tex[:,:,3] on a 3-channel image. Leave opacity unset.

        if "normal" in textures:
            tex = UsdShade.Shader.Define(stage, mat_path.AppendChild("normal_tex"))
            tex.CreateIdAttr("UsdUVTexture")
            tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(textures["normal"])
            tex.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(uv_out)
            tex.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("raw")
            rgb = tex.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
            surf.CreateInput("normal", Sdf.ValueTypeNames.Normal3f).ConnectToSource(rgb)

        if "roughness" in textures:
            tex = UsdShade.Shader.Define(stage, mat_path.AppendChild("rough_tex"))
            tex.CreateIdAttr("UsdUVTexture")
            tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(textures["roughness"])
            tex.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(uv_out)
            tex.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("raw")
            g = tex.CreateOutput("g", Sdf.ValueTypeNames.Float)
            surf.CreateInput("roughness", Sdf.ValueTypeNames.Float).ConnectToSource(g)
        n += 1

    stage.GetRootLayer().Export(str(out_path))
    print(f"  OK  {usd_path.name} ({n} materials) → {out_path.name}")
    return out_path


def convert_all(veg_root: Path) -> int:
    """Convert all known tree species under veg_root/<Species>/<Species>.usd."""
    ok = 0
    for nm in TREE_NAMES:
        usd = veg_root / nm / f"{nm}.usd"
        if convert_tree(usd) is not None:
            ok += 1
    return ok


def main(argv: list[str] | None = None) -> int:
    from genesis_nav.config import paths
    ap = argparse.ArgumentParser(prog="genesis-nav convert-trees")
    ap.add_argument("path", nargs="?",
                     help="Single .usd path, OR a Vegetation root dir, OR omit "
                          "to use paths().simready_vegetation")
    args = ap.parse_args(argv)
    target = Path(args.path) if args.path else paths().simready_vegetation
    if target is None:
        ap.error("no SIMREADY_VEGETATION configured; pass path explicitly")
    target = Path(target)
    if target.is_file():
        return 0 if convert_tree(target) else 1
    if target.is_dir():
        n = convert_all(target)
        print(f"converted {n}/{len(TREE_NAMES)} trees")
        return 0 if n > 0 else 1
    print(f"{target} not found", file=sys.stderr); return 1


if __name__ == "__main__":
    sys.exit(main())
