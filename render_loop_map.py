#!/usr/bin/env python3
"""Render a Ballyhoura loop map given the loop name and optional filenames."""
from __future__ import annotations

import argparse
from pathlib import Path

from render_ballyhoura_map import generate_map

DEFAULT_INPUT = Path("ballyhoura-overpass.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("loop", help="Loop name filter to select (case-insensitive substring).")
    parser.add_argument(
        "--input",
        default=DEFAULT_INPUT,
        type=Path,
        help="Overpass JSON input path (default: %(default)s)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output PNG filename (default: derived from loop name)",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=200,
        help="Output resolution in DPI (default: %(default)s)",
    )
    parser.add_argument(
        "--figsize",
        type=float,
        nargs=2,
        metavar=("WIDTH", "HEIGHT"),
        default=(9.0, 9.0),
        help="Figure size in inches (default: %(default)s)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = args.output or Path(f"{args.loop.lower().replace(' ', '-')}-loop-map.png")
    generate_map(
        input_json=args.input,
        output_png=output,
        loops=[args.loop],
        figsize=tuple(args.figsize),
        dpi=args.dpi,
    )


if __name__ == "__main__":
    main()
