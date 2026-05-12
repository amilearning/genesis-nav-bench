# Asset packs

The package itself contains only the prompts + glue code. Geometry + textures
are downloaded separately. Default storage layout (override via `~/.genesis_navrc`):

```
~/.genesis_nav/assets/
├── hdris/                       # PolyHaven HDRIs (.hdr)
├── ground/                      # PolyHaven ground textures (.jpg)
├── wall/                        # PolyHaven wall textures (.jpg)
├── husky_bullet/                # Husky URDF + meshes
├── Lightwheel_OpenSource/       # Lightwheel kitchen appliances (243 USDs)
├── simready/                    # NVIDIA SimReady envs + Vegetation
│   ├── Simple_Warehouse/
│   ├── Office/
│   ├── Hospital/
│   └── restaurant/Vegetation/   # tree USDs
└── asset_objaverse_index.json   # curated Objaverse UIDs + scales
```

## PolyHaven HDRIs + PBR ground/wall textures (CC0)

One command:
```bash
genesis-nav fetch                # all three packs
# OR pick:
genesis-nav fetch hdris textures
```

Pulls ~250 MB total (~36 HDRIs + 12 ground textures + 6 wall textures, all
with normal+roughness maps). Files cached; re-running skips existing.

To add more HDRIs/textures, edit `polyhaven_fetcher.DEFAULT_HDRIS` etc. or
fetch directly:
```
curl -O https://dl.polyhaven.org/file/ph-assets/HDRIs/hdr/2k/<slug>_2k.hdr
```

## NVIDIA SimReady envs (free for non-commercial)

```bash
genesis-nav fetch-simready Simple_Warehouse Office Hospital
```

Mirrors from `omniverse-content-production.s3-us-west-2.amazonaws.com`.

**Warehouse Props directory has hardcoded paths.** After downloading,
symlink so internal references resolve:
```bash
cd ~/.genesis_nav/assets/simready/Simple_Warehouse
sudo ln -s "$(pwd)/Props"     /home/frl/genesis/Props      # adjust to your prefix
sudo ln -s "$(pwd)/Materials" /home/frl/genesis/Materials
```

Then convert MDL → UsdPreviewSurface:
```bash
genesis-nav convert ~/.genesis_nav/assets/simready/Simple_Warehouse/warehouse.usd
```

## NVIDIA Vegetation USD trees

The 9 tree species (`Shumard_Oak`, `Black_Oak`, etc.) live in the NVIDIA
Restaurant Demopack. Manual download via NVIDIA's content portal —
not on S3 directly. Place under `<root>/assets/simready/restaurant/Vegetation/`.

Convert each species:
```bash
genesis-nav convert-trees    # iterates over all 9 known species
# or:
genesis-nav convert-trees ~/.genesis_nav/assets/simready/restaurant/Vegetation/Shumard_Oak/Shumard_Oak.usd
```

## Lightwheel 243 kitchen appliances (CC BY-NC 4.0)

3.4 GB Google Drive bundle — manual download:
- <https://github.com/LightwheelAI/Lightwheel-simready-asset>

Extract to `~/.genesis_nav/assets/Lightwheel_OpenSource/`. Then convert each:
```bash
for d in ~/.genesis_nav/assets/Lightwheel_OpenSource/Manipulation/*/; do
    base=$(basename "$d")
    genesis-nav convert "$d/$base.usd"
done
```

## Objaverse photoreal GLBs

```bash
pip install objaverse
```

Use the curated index that ships with this repo at
`src/genesis_nav/data/asset_objaverse_index.json` — it lists 10 hand-picked
UIDs with their `target_size_m` and pre-computed `scale`. First call to
`objaverse.load_objects([uid])` caches the GLB locally to `~/.objaverse/`.

## Husky URDF + meshes

```bash
git clone --depth 1 https://github.com/bulletphysics/bullet3
mkdir -p ~/.genesis_nav/assets
cp -r bullet3/data/husky ~/.genesis_nav/assets/husky_bullet
```

## Genesis simulator

```bash
pip install genesis-world
```

For LuisaRender (optional, photoreal):
- Build from the Genesis upstream source per their docs
- Path to `LuisaRenderPy.cpython-*.so` goes in `~/.genesis_navrc` under
  `luisarender_bin`.

## Verify

```bash
genesis-nav catalog
```

Empty section ⇒ that pack isn't installed yet.
