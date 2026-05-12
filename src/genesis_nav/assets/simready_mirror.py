"""
Mirror NVIDIA SimReady environments (Simple_Warehouse / Office / Hospital /
etc.) from the omniverse-content-production S3 bucket. Each environment
preserves its directory structure so relative paths in the USDs resolve.

CLI: `genesis-nav fetch-simready Simple_Warehouse Office Hospital`
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

S3_BASE = "https://omniverse-content-production.s3-us-west-2.amazonaws.com"
PREFIX = "Assets/Isaac/5.1/Isaac/Environments"


def _list_keys(env: str, marker: str = "") -> tuple[list[str], bool]:
    """List S3 keys under <PREFIX>/<env>/. Returns (keys, truncated)."""
    import urllib.request
    import xml.etree.ElementTree as ET
    url = f"{S3_BASE}/?prefix={PREFIX}/{env}/&marker={marker}&max-keys=1000"
    with urllib.request.urlopen(url) as resp:
        xml = resp.read().decode()
    root = ET.fromstring(xml)
    ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
    keys = [c.find("s3:Key", ns).text for c in root.findall("s3:Contents", ns)
             if int(c.find("s3:Size", ns).text) > 0]
    truncated = (root.findtext("s3:IsTruncated", default="false", namespaces=ns) == "true")
    return keys, truncated


def fetch_env(env: str, dest_root: Path) -> int:
    """Download one environment. Returns file count."""
    import urllib.request
    n = 0
    marker = ""
    while True:
        keys, truncated = _list_keys(env, marker)
        if not keys: break
        for key in keys:
            rel = key.removeprefix(f"{PREFIX}/{env}/")
            target = dest_root / env / rel
            if target.exists() and target.stat().st_size > 0:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            url = f"{S3_BASE}/{key}"
            try:
                urllib.request.urlretrieve(url, str(target))
                n += 1
            except Exception as e:
                print(f"  FAIL {target.name}: {e}", file=sys.stderr)
        if not truncated: break
        marker = keys[-1]
    total = sum(1 for _ in (dest_root / env).rglob("*") if _.is_file())
    print(f"  done {env}: {total} files on disk")
    return n


def main(argv: list[str] | None = None) -> int:
    from genesis_nav.config import paths
    ap = argparse.ArgumentParser(prog="genesis-nav fetch-simready")
    ap.add_argument("envs", nargs="+",
                     help="environment names (e.g. Simple_Warehouse Office Hospital)")
    args = ap.parse_args(argv)
    dest = paths().simready_root
    if dest is None:
        ap.error("simready_root not configured")
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for env in args.envs:
        print(f"=== {env} ===")
        fetch_env(env, dest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
