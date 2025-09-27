#!/usr/bin/env python3
"""Fetch elevation samples for Ballyhoura loop coordinates using Open-Elevation API.

Usage:
    python fetch_ballyhoura_elevation.py --loops "Greenwood Loop" --output ballyhoura-elevation.csv

The script queries https://api.open-elevation.com in batches (default 100 locations per call).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Iterable, List, Sequence, Set, Tuple

import requests

Point = Tuple[float, float]

API_URL = "https://api.open-elevation.com/api/v1/lookup"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        default="ballyhoura-overpass.json",
        help="Overpass JSON export containing loop relations (default: %(default)s)",
    )
    parser.add_argument(
        "--output",
        default="ballyhoura-elevation.csv",
        help="Output CSV file for elevations (default: %(default)s)",
    )
    parser.add_argument(
        "--loops",
        action="append",
        help="Loop name(s) to sample. If omitted, all MTB relations are used.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Number of coordinate pairs per API request (default: %(default)s)",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=1.0,
        help="Seconds to wait between requests; used as base for backoff when retries occur (default: %(default)s)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
        help="Maximum retry attempts for a failed/429 request (default: %(default)s)",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=1e-5,
        help="Coordinate rounding tolerance to deduplicate points (default: %(default)s)",
    )
    parser.add_argument(
        "--max-points",
        type=int,
        help="Optional limit on number of points to sample (after dedupe)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List how many points would be fetched without calling the API",
    )
    return parser.parse_args()


def load_overpass(path: Path) -> List[dict]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data.get("elements", [])


def collect_loop_coords(elements: Sequence[dict], loops: Iterable[str] | None, tolerance: float) -> List[Point]:
    desired = [name.lower() for name in loops] if loops else None
    coords: Set[Tuple[int, int]] = set()
    samples: List[Point] = []

    for element in elements:
        if element.get("type") != "relation":
            continue
        tags = element.get("tags", {})
        if tags.get("route") != "mtb":
            continue
        name = tags.get("name", "")
        if desired and not any(req in name.lower() for req in desired):
            continue
        for member in element.get("members", []):
            geometry = member.get("geometry") or []
            for pt in geometry:
                lat = pt["lat"]
                lon = pt["lon"]
                key = quantise(lat, lon, tolerance)
                if key not in coords:
                    coords.add(key)
                    samples.append((lat, lon))

    return samples


def quantise(lat: float, lon: float, tolerance: float) -> Tuple[int, int]:
    factor = 1.0 / tolerance
    return int(round(lat * factor)), int(round(lon * factor))


def chunked(seq: Sequence[Point], size: int) -> Iterable[List[Point]]:
    for start in range(0, len(seq), size):
        yield list(seq[start : start + size])


def fetch_batch(points: Sequence[Point], sleep_seconds: float, max_retries: int) -> List[float]:
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


def write_csv(path: Path, points: Sequence[Point], elevations: Sequence[float]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        fh.write("lat,lon,elevation\n")
        for (lat, lon), ele in zip(points, elevations):
            fh.write(f"{lat:.8f},{lon:.8f},{ele:.2f}\n")


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")

    elements = load_overpass(input_path)
    points = collect_loop_coords(elements, args.loops, args.tolerance)

    if args.max_points is not None:
        points = points[: args.max_points]

    if not points:
        raise SystemExit("No coordinates found for requested loops")

    print(f"Unique coordinate samples: {len(points)}")
    if args.dry_run:
        print("Dry run requested; exiting without fetching elevations")
        return

    elevations: List[float] = []
    for chunk in chunked(points, args.batch_size):
        elevations.extend(fetch_batch(chunk, args.sleep, args.max_retries))
        time.sleep(args.sleep)

    output_path = Path(args.output)
    write_csv(output_path, points, elevations)
    print(f"Saved elevations to {output_path}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("Cancelled")
