"""
`genesis-nav examples` — copy bundled example nav-task YAMLs into the user's
configs dir so they can be planned and run immediately.

Bundled examples (under genesis_nav/data/configs/):
  - nav_smoke_test_v1.yaml         23-object park, straight 24 m drive (sanity check)
  - nav_chicane_stress_v1.yaml     S-curve stress test for the pure-pursuit follower
"""
from __future__ import annotations

import argparse
import shutil
import sys
from importlib.resources import files
from pathlib import Path


BUNDLED = [
    "nav_smoke_test_v1.yaml",
    "nav_chicane_stress_v1.yaml",
]


def copy_examples(dest_dir: Path, overwrite: bool = False) -> list[Path]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    src_root = files("genesis_nav.data.configs")
    for nm in BUNDLED:
        src = src_root / nm
        dst = dest_dir / nm
        if dst.exists() and not overwrite:
            print(f"  [SKIP] {dst.name} (exists; use --overwrite)")
            continue
        dst.write_text(src.read_text())
        written.append(dst)
        print(f"  wrote {dst}")
    return written


def main(argv: list[str] | None = None) -> int:
    from genesis_nav.config import paths
    ap = argparse.ArgumentParser(
        prog="genesis-nav examples",
        description="Copy bundled example nav-task YAMLs to the user's configs dir.",
    )
    ap.add_argument("--dest", default=None,
                     help="target dir (default: paths().root / 'configs')")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args(argv)
    dest = Path(args.dest) if args.dest else (paths().root / "configs")
    written = copy_examples(dest, overwrite=args.overwrite)
    if not written:
        print("Nothing copied (use --overwrite to force).")
    else:
        print(f"\n{len(written)} examples copied. Try:")
        print(f"  genesis-nav plan smoke_test_v1")
        print(f"  genesis-nav run  smoke_test_v1 --rasterizer")
    return 0


if __name__ == "__main__":
    sys.exit(main())
