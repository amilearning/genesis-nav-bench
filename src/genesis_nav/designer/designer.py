"""
Gemini nav-task designer — stage 1 of the genesis-nav-bench pipeline.

Programmatic API:
    from genesis_nav.designer import design_task
    yaml_text = design_task(description=..., name=..., robot="husky",
                             bounds=(-15, 15, -15, 15))

CLI: `genesis-nav design --description ... --name ... --robot ...`
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from genesis_nav.config import output_dir, paths
from genesis_nav.designer.prompts import (
    PROMPT_TEMPLATE,
    ROBOT_SUMMARIES,
    SYSTEM_PROMPT,
    load_registry_text,
    load_schema_text,
)


_FENCE_RE = re.compile(r"^\s*```(?:yaml|yml)?\s*\n?|\n?```\s*$", re.MULTILINE)


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text).strip()


def call_gemini(prompt: str, system: str, *,
                model: str = "gemini-2.5-flash",
                temperature: float = 0.6) -> str:
    """Single Gemini call. Raises if GEMINI_API_KEY isn't set."""
    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        raise RuntimeError(
            "google-genai is not installed. Install with "
            "`pip install genesis-nav-bench[designer]`"
        ) from e
    if "GEMINI_API_KEY" not in os.environ:
        raise RuntimeError("set GEMINI_API_KEY in the environment first")
    client = genai.Client()
    config = types.GenerateContentConfig(
        system_instruction=system,
        temperature=temperature,
    )
    resp = client.models.generate_content(
        model=model, contents=prompt, config=config,
    )
    return resp.text or ""


def design_task(*, description: str, name: str, robot: str = "husky",
                bounds: tuple[float, float, float, float] = (-15, 15, -15, 15),
                temperature: float = 0.6,
                model: str = "gemini-2.5-flash") -> str:
    """Run one Gemini call. Returns the de-fenced YAML text."""
    if robot not in ROBOT_SUMMARIES:
        raise ValueError(f"unknown robot {robot!r}; supported: {list(ROBOT_SUMMARIES)}")
    xmin, xmax, ymin, ymax = bounds
    prompt = PROMPT_TEMPLATE.format(
        description=description,
        robot=robot,
        robot_summary=ROBOT_SUMMARIES[robot],
        xmin=xmin, xmax=xmax, ymin=ymin, ymax=ymax,
        name=name,
        registry=load_registry_text(),
        schema=load_schema_text(),
    )
    raw = call_gemini(prompt, SYSTEM_PROMPT, model=model, temperature=temperature)
    yaml_text = _strip_fences(raw)
    if not yaml_text:
        raise RuntimeError("Gemini returned empty response")
    return yaml_text


def save_task_yaml(yaml_text: str, name: str, out_dir: Path | None = None) -> Path:
    """Validate YAML parses + save to <out_dir>/config.yaml. By default
    `out_dir = paths().outputs / nav_<name>/` so each task is a self-contained
    folder with the YAML alongside occupancy.png, path.json, fpv.mp4 etc."""
    import yaml
    try:
        parsed = yaml.safe_load(yaml_text)
    except Exception as e:
        raise RuntimeError(f"Gemini output did not parse as YAML: {e}") from e
    if not isinstance(parsed, dict) or "scene" not in parsed:
        raise RuntimeError("Gemini output missing top-level 'scene' key")
    out_dir = out_dir or output_dir(name)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "config.yaml"
    out_path.write_text(yaml_text)
    return out_path


# --- CLI subcommand (invoked via `genesis-nav design ...`) ----------
def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="genesis-nav design",
                                  description="Generate a nav-task YAML via Gemini.")
    ap.add_argument("--description", required=True,
                    help="Free-text scene intent")
    ap.add_argument("--name", required=True, help="snake_case task name")
    ap.add_argument("--robot", default="husky", choices=list(ROBOT_SUMMARIES))
    ap.add_argument("--bounds", default="-15,15,-15,15",
                    help="xmin,xmax,ymin,ymax in meters")
    ap.add_argument("--model", default="gemini-2.5-flash")
    ap.add_argument("--temperature", type=float, default=0.6)
    args = ap.parse_args(argv)
    bounds = tuple(float(v) for v in args.bounds.split(","))
    if len(bounds) != 4:
        ap.error("--bounds must be 4 comma-separated floats")

    print(f"[design] {args.model} → '{args.name}' (robot={args.robot})")
    yaml_text = design_task(
        description=args.description, name=args.name, robot=args.robot,
        bounds=bounds, temperature=args.temperature, model=args.model)

    # Sanity-summary
    import yaml
    parsed = yaml.safe_load(yaml_text)
    n_obj = len(parsed.get("objects", []))
    env = parsed.get("world", {}).get("env_type", "?")
    hdri = parsed.get("world", {}).get("sky", {}).get("hdri", "?")
    start = parsed.get("robot", {}).get("start")
    goal  = parsed.get("robot", {}).get("goal")
    print(f"[design] env={env}, hdri={hdri}, {n_obj} obj, start={start}, goal={goal}")

    # save_task_yaml defaults to <outputs>/nav_<name>/config.yaml
    out_path = save_task_yaml(yaml_text, args.name)
    print(f"[design] saved {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
