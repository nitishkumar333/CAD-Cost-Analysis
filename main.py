"""
main.py — CLI entry point for CNC G-code generator.

Usage:
    python main.py --step part.step [--config machining_config.yaml] [--output part.gcode]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from step_analyzer import analyze_step
from gcode_generator import generate_gcode
from time_estimator import estimate_time


def load_config(path: Path) -> dict:
    """Load and return the YAML machining config."""
    with open(path, "r") as f:
        return yaml.safe_load(f)


def print_geometry_summary(geo):
    """Print a human-readable geometry summary."""
    bb = geo.bounding_box
    print("=" * 55)
    print(" PART GEOMETRY ANALYSIS")
    print("=" * 55)
    print(f"  Bounding Box:")
    print(f"    X: {bb.x_min:.3f} → {bb.x_max:.3f}  ({bb.x_size:.3f} mm)")
    print(f"    Y: {bb.y_min:.3f} → {bb.y_max:.3f}  ({bb.y_size:.3f} mm)")
    print(f"    Z: {bb.z_min:.3f} → {bb.z_max:.3f}  ({bb.z_size:.3f} mm)")
    print(f"  Stock Size: {geo.stock_x:.1f} x {geo.stock_y:.1f} x {geo.stock_z:.1f} mm")
    print(f"  Faces: {geo.num_faces} total ({geo.num_planar_faces} planar, {geo.num_curved_faces} curved)")
    print(f"  Holes detected: {len(geo.holes)}")
    for i, h in enumerate(geo.holes):
        print(f"    Hole {i+1}: center=({h.center_x}, {h.center_y}) "
              f"dia={h.diameter:.2f} mm  depth={h.depth:.2f} mm")
    print("=" * 55)


def main():
    parser = argparse.ArgumentParser(
        description="Generate CNC G-code from a STEP file with standard machining template."
    )
    parser.add_argument(
        "--step", required=True,
        help="Path to the input STEP file (.step or .stp)"
    )
    parser.add_argument(
        "--config", default="machining_config.yaml",
        help="Path to machining config YAML (default: machining_config.yaml)"
    )
    parser.add_argument(
        "--output", default=None,
        help="Path for the output G-code file (default: <step_name>.gcode)"
    )
    args = parser.parse_args()

    step_path = Path(args.step)
    config_path = Path(args.config)

    if not step_path.exists():
        print(f"ERROR: STEP file not found: {step_path}", file=sys.stderr)
        sys.exit(1)
    if not config_path.exists():
        print(f"ERROR: Config file not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    output_path = Path(args.output) if args.output else step_path.with_suffix(".gcode")

    # 1. Load config
    print(f"\n[1/4] Loading config from {config_path} ...")
    config = load_config(config_path)

    # 2. Analyze STEP
    print(f"[2/4] Analyzing STEP file: {step_path} ...")
    stock_clearance = config.get("safety", {}).get("stock_clearance", 2.0)
    geometry = analyze_step(step_path, stock_clearance)
    print()
    print_geometry_summary(geometry)

    # 3. Generate G-code
    print(f"\n[3/4] Generating G-code ...")
    gcode = generate_gcode(geometry, config)
    output_path.write_text(gcode)
    print(f"  ✓ G-code written to: {output_path}")
    print(f"  ✓ Total lines: {len(gcode.splitlines())}")

    # 4. Estimate time
    print(f"\n[4/4] Estimating machining time ...")
    rapid_feed = config.get("machine", {}).get("rapid_feed_rate", 8000)
    estimate = estimate_time(gcode, rapid_feed)
    print()
    print(estimate.summary())
    print()

    print("Done! Your G-code is ready.")


if __name__ == "__main__":
    main()
