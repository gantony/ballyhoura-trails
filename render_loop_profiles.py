#!/usr/bin/env python3
"""Generate elevation profile PNGs for every way in a Ballyhoura loop.

Requires prior elevation sampling (use fetch_ballyhoura_elevation.py) and matplotlib.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from render_trail_profile import (
    ElevationIndex,
    densify,
    load_elevations,
    render_profile,
    fetch_elevations,
    way_display_name,
)

Point = Tuple[float, float]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--loop", required=True, help="Name (or substring) of the MTB loop relation")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("ballyhoura-overpass.json"),
        help="Overpass JSON export (default: %(default)s)",
    )
    parser.add_argument(
        "--elevations",
        type=Path,
        required=True,
        help="Elevation CSV produced by fetch_ballyhoura_elevation.py",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("profiles"),
        help="Directory to write PNG profiles (default: %(default)s)",
    )
    parser.add_argument(
        "--max-segment",
        type=float,
        default=40.0,
        help="Maximum spacing in meters between densified points (default: %(default)s)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Batch size for API fallback (unused when elevations file covers all points)",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=1.0,
        help="Seconds between API calls if needed (default: %(default)s)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
        help="Retries for API fallback (default: %(default)s)",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=1e-5,
        help="Quantisation tolerance when matching elevations (default: %(default)s)",
    )
    return parser.parse_args()


def load_overpass(path: Path) -> Tuple[Dict[int, dict], Dict[int, dict], List[dict]]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    nodes: Dict[int, dict] = {}
    ways: Dict[int, dict] = {}
    relations: List[dict] = []
    for element in data.get("elements", []):
        etype = element.get("type")
        if etype == "node":
            nodes[element["id"]] = element
        elif etype == "way":
            ways[element["id"]] = element
        elif etype == "relation":
            relations.append(element)
    return nodes, ways, relations


def match_relation(loop_name: str, relations: Iterable[dict]) -> dict:
    target = loop_name.lower()
    matches = [rel for rel in relations if rel.get("tags", {}).get("route") == "mtb" and target in rel.get("tags", {}).get("name", "").lower()]
    if not matches:
        raise SystemExit(f"No loop relation found matching '{loop_name}'")
    if len(matches) > 1:
        names = ", ".join(rel.get("tags", {}).get("name", f"relation {rel['id']}") for rel in matches)
        raise SystemExit(f"Multiple loop relations matched '{loop_name}': {names}")
    return matches[0]


def member_geometry(member: dict, ways: Dict[int, dict], nodes: Dict[int, dict]) -> Tuple[str, List[Point]]:
    way_id = member.get("ref")
    way = ways.get(way_id)
    if not way:
        raise SystemExit(f"Relation references missing way {way_id}")
    name = way_display_name(way)
    coords: List[Point] = []
    if "geometry" in member:
        coords = [(pt["lat"], pt["lon"]) for pt in member["geometry"]]
    elif "geometry" in way:
        coords = [(pt["lat"], pt["lon"]) for pt in way["geometry"]]
    else:
        for node_id in way.get("nodes", []):
            node = nodes.get(node_id)
            if node:
                coords.append((node["lat"], node["lon"]))
    return name, coords


def main() -> None:
    args = parse_args()
    nodes, ways, relations = load_overpass(args.input)
    relation = match_relation(args.loop, relations)
    elevation_index: ElevationIndex | None = load_elevations(args.elevations, args.tolerance)
    if elevation_index is None:
        raise SystemExit("Elevation data is required for batch profile generation")

    args.output_dir.mkdir(parents=True, exist_ok=True)

    for member in relation.get("members", []):
        if member.get("type") != "way":
            continue
        name, coords = member_geometry(member, ways, nodes)
        if len(coords) < 2:
            continue
        dense_coords = densify(coords, args.max_segment)
        elevations = fetch_elevations(
            dense_coords,
            args.batch_size,
            args.sleep,
            args.max_retries,
            elevation_index,
        )
        distances = cumulative_distances(dense_coords)
        output_path = args.output_dir / f"{slugify(name)}-profile.png"
        render_profile(distances, elevations, name, output_path)
        print(f"Saved {output_path}")


def cumulative_distances(points: List[Point]) -> List[float]:
    from render_trail_profile import haversine_distance

    distances = [0.0]
    for a, b in zip(points, points[1:]):
        distances.append(distances[-1] + haversine_distance(a, b))
    return distances


def slugify(name: str) -> str:
    allowed = [c.lower() if c.isalnum() else "-" for c in name]
    slug = "".join(allowed)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "trail"


if __name__ == "__main__":
    main()
