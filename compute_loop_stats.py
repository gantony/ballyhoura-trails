#!/usr/bin/env python3
"""Compute ordered trail segments for a Ballyhoura loop with length and ascent/descent.

Usage:
    python compute_loop_stats.py --loop "Greenwood Loop" \
        --input ballyhoura-overpass.json --elevations ballyhoura-elevation.csv

If no elevation file is provided, ascent/descent will be reported as 0 and a warning emitted.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

Point = Tuple[float, float]  # (lat, lon)


@dataclass
class SegmentStats:
    name: str
    way_id: int
    distance_m: float
    ascent_m: float
    descent_m: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--loop", required=True, help="Name (or substring) of the MTB loop relation to analyse")
    parser.add_argument(
        "--input",
        default="ballyhoura-overpass.json",
        help="Overpass JSON export (default: %(default)s)",
    )
    parser.add_argument(
        "--elevations",
        help="Optional CSV with columns lat,lon,elevation (meters). Used to compute ascent/descent",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=1e-5,
        help="Coordinate rounding tolerance when matching elevations (default: %(default)s)",
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


def haversine_distance(p1: Point, p2: Point) -> float:
    lat1, lon1 = map(math.radians, p1)
    lat2, lon2 = map(math.radians, p2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return 6371000.0 * c  # Earth mean radius in meters


def total_distance(points: Sequence[Point]) -> float:
    return sum(haversine_distance(a, b) for a, b in zip(points, points[1:]))


def load_elevations(csv_path: Optional[Path], tolerance: float) -> Optional[Dict[Tuple[int, int], float]]:
    if not csv_path:
        return None
    if not csv_path.exists():
        raise SystemExit(f"Elevation file not found: {csv_path}")
    elev_map: Dict[Tuple[int, int], float] = {}
    with csv_path.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing_columns = {"lat", "lon", "elevation"} - set(reader.fieldnames or [])
        if missing_columns:
            raise SystemExit(f"Elevation file missing columns: {', '.join(sorted(missing_columns))}")
        for row in reader:
            try:
                lat = float(row["lat"])
                lon = float(row["lon"])
                ele = float(row["elevation"])
            except (TypeError, ValueError) as exc:
                raise SystemExit(f"Invalid elevation row: {row}") from exc
            key = quantise(lat, lon, tolerance)
            elev_map[key] = ele
    return elev_map


def quantise(lat: float, lon: float, tolerance: float) -> Tuple[int, int]:
    factor = 1.0 / tolerance
    return int(round(lat * factor)), int(round(lon * factor))


def collect_relation(loop_name: str, relations: Iterable[dict]) -> dict:
    loop_name_lower = loop_name.lower()
    matches = [rel for rel in relations if (rel.get("tags", {}).get("route") == "mtb" and loop_name_lower in (rel.get("tags", {}).get("name", "").lower()))]
    if not matches:
        raise SystemExit(f"No relation found matching loop name: {loop_name}")
    if len(matches) > 1:
        names = ", ".join(sorted(rel.get("tags", {}).get("name", f"relation {rel['id']}") for rel in matches))
        raise SystemExit(f"Multiple relations matched name '{loop_name}': {names}. Please provide a more specific string.")
    return matches[0]


def member_geometry(member: dict, ways: Dict[int, dict], nodes: Dict[int, dict]) -> Tuple[str, int, List[Point]]:
    way_id = member.get("ref")
    way = ways.get(way_id)
    if not way:
        raise SystemExit(f"Relation references missing way id {way_id}")
    tags = way.get("tags", {})
    name = tags.get("name") or tags.get("ref") or f"way {way_id}"

    if member.get("geometry"):
        coords = [(pt["lat"], pt["lon"]) for pt in member["geometry"]]
    elif way.get("geometry"):
        coords = [(pt["lat"], pt["lon"]) for pt in way["geometry"]]
    else:
        coords = []
        for node_id in way.get("nodes", []):
            node = nodes.get(node_id)
            if not node:
                continue
            coords.append((node["lat"], node["lon"]))
    return name, way_id, coords


def compute_ascent_descent(points: Sequence[Point], elevation_map: Optional[Dict[Tuple[int, int], float]], tolerance: float) -> Tuple[float, float]:
    if not elevation_map:
        return 0.0, 0.0
    elevations: List[float] = []
    for lat, lon in points:
        key = quantise(lat, lon, tolerance)
        if key in elevation_map:
            elevations.append(elevation_map[key])
    if len(elevations) < 2:
        return 0.0, 0.0
    ascent = 0.0
    descent = 0.0
    for prev, cur in zip(elevations, elevations[1:]):
        delta = cur - prev
        if delta > 0:
            ascent += delta
        else:
            descent -= delta
    return ascent, descent


def compute_loop(loop_relation: dict, ways: Dict[int, dict], nodes: Dict[int, dict], elevation_map: Optional[Dict[Tuple[int, int], float]], tolerance: float) -> List[SegmentStats]:
    segments: List[SegmentStats] = []
    for member in loop_relation.get("members", []):
        if member.get("type") != "way":
            continue
        name, way_id, coords = member_geometry(member, ways, nodes)
        if len(coords) < 2:
            continue
        distance = total_distance(coords)
        ascent, descent = compute_ascent_descent(coords, elevation_map, tolerance)
        segments.append(
            SegmentStats(
                name=name,
                way_id=way_id,
                distance_m=distance,
                ascent_m=ascent,
                descent_m=descent,
            )
        )
    return segments


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")

    nodes, ways, relations = load_overpass(input_path)
    relation = collect_relation(args.loop, relations)
    elevation_map = load_elevations(Path(args.elevations) if args.elevations else None, args.tolerance)
    if args.elevations and not elevation_map:
        raise SystemExit("Elevation file provided but no valid rows parsed")
    if not args.elevations:
        print("Warning: no elevation data supplied; ascent/descent will be zero. Use --elevations to add data.")

    segments = compute_loop(relation, ways, nodes, elevation_map, args.tolerance)
    if not segments:
        raise SystemExit("No segments extracted for the requested loop")

    total_distance = sum(seg.distance_m for seg in segments)
    total_ascent = sum(seg.ascent_m for seg in segments)
    total_descent = sum(seg.descent_m for seg in segments)

    header = ["#", "Segment", "Way ID", "Distance (m)", "Ascent (m)", "Descent (m)"]
    print("\t".join(header))
    for idx, seg in enumerate(segments, start=1):
        print(
            "\t".join(
                [
                    str(idx),
                    seg.name,
                    str(seg.way_id),
                    f"{seg.distance_m:.1f}",
                    f"{seg.ascent_m:.1f}",
                    f"{seg.descent_m:.1f}",
                ]
            )
        )
    print("\nTotals:\t-\t-\t{:.1f}\t{:.1f}\t{:.1f}".format(total_distance, total_ascent, total_descent))


if __name__ == "__main__":
    main()
