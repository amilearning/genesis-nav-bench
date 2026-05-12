# Install

## 1. Python package

```bash
pip install genesis-nav-bench[designer,assets]
```

Or from source:

```bash
git clone https://github.com/<YOUR_USERNAME>/genesis-nav-bench
cd genesis-nav-bench
pip install -e .[designer,assets,dev]
```

Optional extras:
- `[designer]` → `google-genai` (only needed for the Gemini designer step)
- `[assets]`   → `usd-core`, `trimesh`, `objaverse` (only needed for converters / Objaverse loads)
- `[dev]`      → `pytest`, `ruff`, `pyright`

## 2. Genesis simulator

The Genesis sim itself isn't a hard pip dep yet — install separately:

```bash
pip install genesis-world
# Or build from the upstream repo if you want LuisaRender:
git clone https://github.com/Genesis-Embodied-AI/Genesis
cd Genesis && pip install -e .
```

**LuisaRender** (optional but recommended for photoreal HDRI rendering)
is a separate C++ build inside the Genesis source tree. See
[Genesis docs](https://genesis-world.readthedocs.io). When built, point
the package at the binary dir:

```toml
# ~/.genesis_navrc
[paths]
luisarender_bin = "/abs/path/to/Genesis/genesis/ext/LuisaRender/build/bin"
```

When that env-var-style config is set, `genesis-nav run` automatically
swaps to the path-traced renderer with HDRI env-surface. Otherwise it
falls back to the rasterizer.

## 3. Bootstrap

```bash
genesis-nav bootstrap
```

That command:
- creates `~/.genesis_nav/{assets,configs,outputs}/`
- writes `~/.genesis_navrc` (TOML) with default paths
- prints the external-asset guide (which packs to download and how)

Add `--fetch` to also pull PolyHaven HDRIs + textures in one shot:

```bash
genesis-nav bootstrap --fetch
```

## 4. Asset packs

See [`assets.md`](./assets.md) for per-pack instructions.

## 5. Gemini API key

```bash
export GEMINI_API_KEY="..."          # one-off
echo 'export GEMINI_API_KEY=...' >> ~/.bashrc   # persistent
```

Get one at <https://aistudio.google.com/apikey>.

## 6. Verify

```bash
genesis-nav --help        # see all subcommands
genesis-nav catalog       # list every asset the package can find
```

The catalog tells you exactly what's wired up. Empty categories mean the
corresponding asset pack isn't installed.
