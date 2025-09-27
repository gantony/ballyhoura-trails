#!/usr/bin/env python3
"""Render an elevation profile PNG for a named Ballyhoura trail segment.

Example:
    python render_trail_profile.py --trail "Blue Grade - Green Machine" \
        --input ballyhoura-overpass.json --output green-machine-profile.png

Requires `requests` (for Open-Elevation) and `matplotlib`.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import matplotlib.pyplot as plt
import requests

Point = Tuple[float, float]  # (lat, lon)
API_URL = "https://api.open-elevation.com/api/v1/lookup"


def way_display_name(way: dict) -> str:
    tags = way.get("tags", {})
    return tags.get("name") or tags.get("ref") or f"way {way['id']}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--trail",
        required=True,
        help="Name (or substring) of the way to profile",
    )
    parser.add_argument(
        "--trail-id",
        type=int,
        help="Optional explicit way ID to select when multiple matches exist",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("ballyhoura-overpass.json"),
        help="Overpass JSON export (default: %(default)s)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="PNG file to write (default: derived from trail name)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Coordinate batch size per elevation request (default: %(default)s)",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=1.0,
        help="Seconds to wait between elevation requests (default: %(default)s)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
        help="Maximum retries when the elevation API rate limits or fails (default: %(default)s)",
    )
    parser.add_argument(
        "--max-segment",
        type=float,
        default=40.0,
        help="Maximum spacing in meters between sampled points (default: %(default)s)",
    )
    parser.add_argument(
        "--elevations",
        type=Path,
        help="Optional CSV with lat,lon,elevation columns to reuse sampled data",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=1e-5,
        help="Quantisation tolerance when matching elevation samples (default: %(default)s)",
    )
    return parser.parse_args()


def load_way(trail_name: str, input_path: Path, trail_id: Optional[int]) -> Tuple[dict, List[Point]]:
    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")
    with input_path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    trail_lower = trail_name.lower()
    candidates = []
    nodes_map = {}
    for element in data.get("elements", []):
        etype = element.get("type")
        if etype == "node":
            nodes_map[element["id"]] = element
        elif etype == "way":
            tags = element.get("tags", {})
            name = tags.get("name", "")
            if trail_lower in name.lower():
                candidates.append(element)
    if not candidates:
        raise SystemExit(f"No way found containing name '{trail_name}'")
    if trail_id is not None:
        selected = next((cand for cand in candidates if cand.get("id") == trail_id), None)
        if not selected:
            raise SystemExit(f"Trail id {trail_id} not found for name '{trail_name}'")
        candidates = [selected]
    if len(candidates) > 1:
        names = ", ".join(f"{way_display_name(cand)} (id {cand['id']})" for cand in candidates)
        raise SystemExit(
            "Multiple ways matched name. Be more specific or use ID. Matches: " + names
        )
    way = candidates[0]
    coords: List[Point] = []
    if "geometry" in way:
        coords = [(pt["lat"], pt["lon"]) for pt in way["geometry"]]
    else:
        for node_id in way.get("nodes", []):
            node = nodes_map.get(node_id)
            if node:
                coords.append((node["lat"], node["lon"]))
    if len(coords) < 2:
        raise SystemExit("Insufficient geometry to plot profile")
    return way, coords


@dataclass
class ElevationIndex:
    tolerance: float
    lookup: Dict[Tuple[int, int], float]
    points: List[Tuple[float, float, float]]

    def sample(self, lat: float, lon: float) -> Optional[float]:
        key = quantise(lat, lon, self.tolerance)
        if key in self.lookup:
            return self.lookup[key]
        # Fallback to nearest neighbour search.
        best_val = None
        best_dist = float("inf")
        for plat, plon, pele in self.points:
            dist = (plat - lat) ** 2 + (plon - lon) ** 2
            if dist < best_dist:
                best_dist = dist
                best_val = pele
        return best_val


def load_elevations(csv_path: Optional[Path], tolerance: float) -> Optional[ElevationIndex]:
    if not csv_path:
        return None
    if not csv_path.exists():
        raise SystemExit(f"Elevation file not found: {csv_path}")
    elev_map: Dict[Tuple[int, int], float] = {}
    points: List[Tuple[float, float, float]] = []
    with csv_path.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames or not {"lat", "lon", "elevation"}.issubset(reader.fieldnames):
            raise SystemExit("Elevation CSV must contain lat, lon, elevation columns")
        for row in reader:
            try:
                lat = float(row["lat"])
                lon = float(row["lon"])
                ele = float(row["elevation"])
            except (TypeError, ValueError) as exc:
                raise SystemExit(f"Invalid elevation row: {row}") from exc
            elev_map[quantise(lat, lon, tolerance)] = ele
            points.append((lat, lon, ele))
    return ElevationIndex(tolerance=tolerance, lookup=elev_map, points=points)


def quantise(lat: float, lon: float, tolerance: float) -> Tuple[int, int]:
    factor = 1.0 / tolerance
    return int(round(lat * factor)), int(round(lon * factor))


def haversine_distance(p1: Point, p2: Point) -> float:
    lat1, lon1 = map(math.radians, p1)
    lat2, lon2 = map(math.radians, p2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return 6371000.0 * c


def densify(coords: Sequence[Point], max_spacing: float) -> List[Point]:
    if max_spacing <= 0:
        return list(coords)
    dense: List[Point] = [coords[0]]
    for a, b in zip(coords, coords[1:]):
        distance = haversine_distance(a, b)
        if distance <= max_spacing:
            dense.append(b)
            continue
        steps = max(int(math.ceil(distance / max_spacing)), 1)
        for step in range(1, steps + 1):
            frac = step / steps
            lat = a[0] + (b[0] - a[0]) * frac
            lon = a[1] + (b[1] - a[1]) * frac
            dense.append((lat, lon))
    return dense


def fetch_elevations(
    points: Sequence[Point],
    batch_size: int,
    sleep_seconds: float,
    max_retries: int,
    elevation_index: Optional[ElevationIndex],
) -> List[float]:
    if elevation_index is not None:
        samples: List[float] = []
        for lat, lon in points:
            value = elevation_index.sample(lat, lon)
            if value is None:
                raise SystemExit(
                    "Elevation data missing for some points; rerun fetch_ballyhoura_elevation.py with denser sampling or provide API access"
                )
            samples.append(value)
        return samples

    elevations: List[float] = []
    for idx in range(0, len(points), batch_size):
        chunk = points[idx : idx + batch_size]
        elevations.extend(fetch_batch(chunk, sleep_seconds, max_retries))
        if idx + batch_size < len(points):
            time.sleep(sleep_seconds)
    return elevations


def fetch_batch(points: Sequence[Point], sleep_seconds: float, max_retries: int) -> List[float]:
    if not points:
        return []
    locations = "|".join(f"{lat},{lon}" for lat, lon in points)
    attempt = 0
    backoff = sleep_seconds
    while True:
        attempt += 1
        try:
            response = requests.get(API_URL, params={"locations": locations}, timeout=30)
        except requests.RequestException as exc:
            if attempt > max_retries:
                raise SystemExit(f"Elevation API request failed: {exc}")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
            continue

        if response.status_code == 429:
            if attempt > max_retries:
                raise SystemExit("Elevation API request failed: 429 Too Many Requests (max retries exceeded)")
            retry_after = response.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else backoff
            time.sleep(max(delay, sleep_seconds))
            backoff = min(backoff * 2, 60)
            continue

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            if attempt > max_retries:
                raise SystemExit(f"Elevation API request failed: {exc}")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
            continue

        data = response.json()
        results = data.get("results", [])
        if len(results) != len(points):
            raise SystemExit("Elevation API returned unexpected number of results")
        return [item["elevation"] for item in results]


def cumulative_distances(points: Sequence[Point]) -> List[float]:
    distances = [0.0]
    for a, b in zip(points, points[1:]):
        distances.append(distances[-1] + haversine_distance(a, b))
    return distances


def render_profile(distances: Sequence[float], elevations: Sequence[float], title: str, output: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 4), constrained_layout=True)
    ax.plot([d / 1000.0 for d in distances], elevations, color="#1f77b4", linewidth=2)
    ax.set_xlabel("Distance (km)")
    ax.set_ylabel("Elevation (m)")
    ax.set_title(title)
    ax.grid(True, linestyle=":", linewidth=0.6, alpha=0.6)
    ax.fill_between([d / 1000.0 for d in distances], elevations, color="#1f77b4", alpha=0.2)
    fig.savefig(output, dpi=200)
    plt.close(fig)


def slugify(name: str) -> str:
    allowed = [c.lower() if c.isalnum() else "-" for c in name]
    slug = "".join(allowed)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "trail-profile"


def main() -> None:
    args = parse_args()
    elevation_index = load_elevations(args.elevations, args.tolerance)
    way, coords = load_way(args.trail, args.input, args.trail_id)
    dense_coords = densify(coords, args.max_segment)
    elevations = fetch_elevations(
        dense_coords,
        args.batch_size,
        args.sleep,
        args.max_retries,
        elevation_index,
    )
    if len(elevations) != len(dense_coords):
        raise SystemExit("Elevation sampling failed; mismatch in coordinate count")

    distances = cumulative_distances(dense_coords)
    display_name = way_display_name(way)
    output_path = args.output or Path(f"{slugify(display_name)}-profile.png")
    title = display_name
    render_profile(distances, elevations, title, output_path)
    print(f"Saved elevation profile to {output_path}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("Cancelled")
