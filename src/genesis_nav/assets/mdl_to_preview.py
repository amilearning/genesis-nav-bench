"""
Convert SimReady / Lightwheel USDs from MDL materials to UsdPreviewSurface so
Genesis's rasterizer + LuisaRender can render them with textures.

Handles three MDL flavors via input-name aliases:
  - Lightwheel OmniPBR     (diffuse_texture / normalmap_texture / ORM_texture)
  - NVIDIA SimReady (warehouse / vegetation) MI_*.mdl
                            (AlbedoTexture / MainNormalInput / MergeMapInput)
  - SimReady Office MI_*    (BaseColor / Normal / AO_R_Rough_G_Metallic_B_)

Writes `<name>_genesis.usd` next to the input.

Programmatic API:
    from genesis_nav.assets.mdl_to_preview import convert_one
    convert_one(Path("/abs/path/to/asset.usd"))

CLI: `genesis-nav convert <usd_path>`
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


# Map from MDL input names → our internal canonical names
TEX_MAP: dict[str, str] = {
    # SimReady Warehouse / Vegetation (NVIDIA MI_*.mdl)
    "inputs:AlbedoTexture":           "diffuse",
    "inputs:MainNormalInput":         "normal",
    "inputs:MergeMapInput":           "orm",
    # SimReady Office (MI_Clock / MI_Props / MI_WallOffice_01)
    "inputs:BaseColor":               "diffuse",
    "inputs:Normal":                  "normal",
    "inputs:AO_R_Rough_G_Metallic_B_":"orm",
    # Lightwheel (OmniPBR)
    "inputs:diffuse_texture":         "diffuse",
    "inputs:normalmap_texture":       "normal",
    "inputs:ORM_texture":             "orm",
    # Generic UsdPreview-style
    "inputs:BaseColor_Texture":       "diffuse",
}
COL_MAP: dict[str, str] = {
    "inputs:ColorAlbedo":             "diffuse_color",
    "inputs:diffuse_color_constant":  "diffuse_color",
    "inputs:BaseColor_Tint":          "diffuse_color",
    "inputs:Color":                   "diffuse_color",
}


def _asset_resolved(shader_prim, attr_name):
    a = shader_prim.GetAttribute(attr_name)
    if not a or not a.IsValid():
        return None
    v = a.Get()
    if v is None:
        return None
    resolved = getattr(v, "resolvedPath", "") or ""
    if resolved and Path(resolved).exists():
        return resolved
    authored = v.path if hasattr(v, "path") else str(v)
    return authored if authored else None


def _col(shader_prim, attr_name):
    a = shader_prim.GetAttribute(attr_name)
    if not a or not a.IsValid():
        return None
    return a.Get()


def convert_one(usd_path: Path, out_path: Path | None = None) -> Path | None:
    """Rewrite OmniPBR/MI_* materials → UsdPreviewSurface in this USD file."""
    try:
        from pxr import Usd, UsdShade, Sdf, Gf
    except ImportError as e:
        raise RuntimeError("usd-core not installed. `pip install usd-core`") from e

    if not usd_path.exists():
        print(f"  SKIP {usd_path} (missing)"); return None
    if out_path is None:
        out_path = usd_path.with_name(usd_path.stem + "_genesis.usd")

    stage = Usd.Stage.Open(str(usd_path))
    if stage is None:
        print(f"  FAIL open {usd_path}"); return None

    mdl_shaders = []
    for prim in stage.Traverse():
        if prim.GetTypeName() != "Shader": continue
        src = prim.GetAttribute("info:mdl:sourceAsset")
        if not (src and src.IsValid() and src.Get() is not None):
            continue
        mdl_shaders.append(prim)

    if not mdl_shaders:
        print(f"  no MDL shaders in {usd_path.name}"); return None

    n_replaced = 0
    for shader_prim in mdl_shaders:
        material_prim = shader_prim.GetParent()
        if not material_prim.IsValid(): continue

        textures: dict[str, str] = {}
        colors: dict[str, object] = {}
        for attr_name, canon in TEX_MAP.items():
            t = _asset_resolved(shader_prim, attr_name)
            if t: textures[canon] = t
        for attr_name, canon in COL_MAP.items():
            c = _col(shader_prim, attr_name)
            if c is not None: colors[canon] = c
        if not textures and not colors: continue

        stage.RemovePrim(shader_prim.GetPath())
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
        elif "diffuse_color" in colors:
            c = colors["diffuse_color"]
            if hasattr(c, "__len__") and len(c) >= 3:
                surf.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f
                                  ).Set(Gf.Vec3f(c[0], c[1], c[2]))

        if "normal" in textures:
            tex = UsdShade.Shader.Define(stage, mat_path.AppendChild("normal_tex"))
            tex.CreateIdAttr("UsdUVTexture")
            tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(textures["normal"])
            tex.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(uv_out)
            tex.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("raw")
            rgb = tex.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
            surf.CreateInput("normal", Sdf.ValueTypeNames.Normal3f).ConnectToSource(rgb)

        if "orm" in textures:
            tex = UsdShade.Shader.Define(stage, mat_path.AppendChild("orm_tex"))
            tex.CreateIdAttr("UsdUVTexture")
            tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(textures["orm"])
            tex.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(uv_out)
            tex.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("raw")
            r = tex.CreateOutput("r", Sdf.ValueTypeNames.Float)
            g = tex.CreateOutput("g", Sdf.ValueTypeNames.Float)
            b = tex.CreateOutput("b", Sdf.ValueTypeNames.Float)
            surf.CreateInput("occlusion", Sdf.ValueTypeNames.Float).ConnectToSource(r)
            surf.CreateInput("roughness", Sdf.ValueTypeNames.Float).ConnectToSource(g)
            surf.CreateInput("metallic",  Sdf.ValueTypeNames.Float).ConnectToSource(b)

        n_replaced += 1

    stage.GetRootLayer().Export(str(out_path))
    print(f"  OK  {usd_path.name}  ({n_replaced}/{len(mdl_shaders)} shaders)  →  {out_path.name}")
    return out_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="genesis-nav convert")
    ap.add_argument("usd", help="USD file to convert")
    ap.add_argument("--out", help="output path (default: <stem>_genesis.usd)")
    args = ap.parse_args(argv)
    out = Path(args.out) if args.out else None
    res = convert_one(Path(args.usd), out)
    return 0 if res else 1


if __name__ == "__main__":
    sys.exit(main())
