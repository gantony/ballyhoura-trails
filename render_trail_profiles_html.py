#!/usr/bin/env python3
"""Build an interactive HTML page with elevation profiles for Ballyhoura trails."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

from render_trail_profile import (
    ElevationIndex,
    densify,
    fetch_elevations,
    load_elevations,
    way_display_name,
)
Point = Tuple[float, float]
ORDER = [
    "Greenwood Loop",
    "Mountrussell Loop",
    "Garrane Loop",
    "Castlepook Loop",
    "Streamhill Loop",
]
EXTRA_SOURCES = [
    Path("new_overpass.json"),
    Path("freebird_overpass.json"),
]
EXTRA_TRAILS = [
    ("Other Red", "Red Grade - Tech 1"),
    ("Other Red", "Red Grade - Tech 2"),
    ("Other Red", "Red Grade - Free bird"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("ballyhoura-overpass.json"),
        help="Overpass JSON export (default: %(default)s)",
    )
    parser.add_argument(
        "--elevations",
        type=Path,
        action="append",
        required=True,
        help="Elevation CSVs (lat,lon,elevation). Provide multiple times to merge datasets.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("trail-profiles.html"),
        help="HTML file to write (default: %(default)s)",
    )
    parser.add_argument(
        "--loops",
        nargs="*",
        default=None,
        help="Optional subset of loop names to include (default: all)",
    )
    parser.add_argument(
        "--max-segment",
        type=float,
        default=40.0,
        help="Maximum spacing in meters for densified coordinates (default: %(default)s)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Batch size for API fallback when elevation data is missing (default: %(default)s)",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=1.0,
        help="Seconds between API requests during fallback (default: %(default)s)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
        help="Maximum retries when contacting the elevation API (default: %(default)s)",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=1e-5,
        help="Quantisation tolerance when matching elevation samples (default: %(default)s)",
    )
    return parser.parse_args()


def merge_elevations(paths: Iterable[Path], tolerance: float) -> ElevationIndex:
    merged_lookup: Dict[Tuple[int, int], float] = {}
    merged_points: List[Tuple[float, float, float]] = []
    for path in paths:
        index = load_elevations(path, tolerance)
        if not index:
            continue
        merged_lookup.update(index.lookup)
        merged_points.extend(index.points)
    if not merged_lookup:
        raise SystemExit("No elevation samples found in provided CSV files")
    return ElevationIndex(tolerance=tolerance, lookup=merged_lookup, points=merged_points)


def load_overpass(path: Path) -> Tuple[Dict[int, dict], Dict[int, dict], List[dict]]:
    if not path.exists():
        raise SystemExit(f"Input file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    nodes = {el["id"]: el for el in data.get("elements", []) if el.get("type") == "node"}
    ways = {el["id"]: el for el in data.get("elements", []) if el.get("type") == "way"}
    relations = [el for el in data.get("elements", []) if el.get("type") == "relation"]

    for extra_path in EXTRA_SOURCES:
        if not extra_path.exists():
            continue
        with extra_path.open("r", encoding="utf-8") as fh:
            extra = json.load(fh)
        for element in extra.get("elements", []):
            etype = element.get("type")
            if etype == "node" and element["id"] not in nodes:
                nodes[element["id"]] = element
            elif etype == "way":
                ways[element["id"]] = element
    return nodes, ways, relations


def way_coords(way: dict, member: dict | None, nodes: Dict[int, dict]) -> List[Point]:
    if member and member.get("geometry"):
        return [(pt["lat"], pt["lon"]) for pt in member["geometry"]]
    if "geometry" in way:
        return [(pt["lat"], pt["lon"]) for pt in way["geometry"]]
    coords: List[Point] = []
    for node_id in way.get("nodes", []):
        node = nodes.get(node_id)
        if node:
            coords.append((node["lat"], node["lon"]))
    return coords


def collect_profiles(
    nodes: Dict[int, dict],
    ways: Dict[int, dict],
    relations: List[dict],
    elevation_index: ElevationIndex,
    loops_filter: Sequence[str] | None,
    *,
    max_segment: float,
    batch_size: int,
    sleep: float,
    max_retries: int,
) -> List[dict]:
    relation_lookup = {
        rel.get("tags", {}).get("name"): rel
        for rel in relations
        if rel.get("tags", {}).get("route") == "mtb"
    }
    active_loops = [name for name in ORDER if relation_lookup.get(name)]
    if loops_filter:
        wanted = {name.lower() for name in loops_filter}
        active_loops = [name for name in active_loops if name.lower() in wanted]

    seen: set[str] = set()
    profiles: List[dict] = []
    sequence = 0

    for loop_name in active_loops:
        relation = relation_lookup[loop_name]
        for member in relation.get("members", []):
            if member.get("type") != "way":
                continue
            way = ways.get(member.get("ref"))
            if not way:
                continue
            label = way_display_name(way)
            if label in seen:
                continue
            seen.add(label)
            coords = way_coords(way, member, nodes)
            if len(coords) < 2:
                continue
            dense = densify(coords, max_segment)
            elevations = fetch_elevations(
                dense,
                batch_size,
                sleep,
                max_retries,
                elevation_index,
            )
            distances = cumulative_distances(dense)
            profiles.append(
                {
                    "loop": loop_name,
                    "label": label,
                    "dist_km": [d / 1000.0 for d in distances],
                    "elev_m": elevations,
                    "sequence": sequence,
                }
            )
            sequence += 1

    for category, trail_name in EXTRA_TRAILS:
        way = next((w for w in ways.values() if way_display_name(w) == trail_name), None)
        if not way or way_display_name(way) in seen:
            continue
        coords = way_coords(way, None, nodes)
        if len(coords) < 2:
            continue
        dense = densify(coords, max_segment)
        elevations = fetch_elevations(dense, batch_size, sleep, max_retries, elevation_index)
        distances = cumulative_distances(dense)
        seen.add(way_display_name(way))
        profiles.append(
            {
                "loop": category,
                "label": way_display_name(way),
                "dist_km": [d / 1000.0 for d in distances],
                "elev_m": elevations,
                "sequence": sequence,
            }
        )
        sequence += 1
    return profiles


def cumulative_distances(points: Sequence[Point]) -> List[float]:
    from render_trail_profile import haversine_distance

    distances = [0.0]
    for a, b in zip(points, points[1:]):
        distances.append(distances[-1] + haversine_distance(a, b))
    return distances


def build_html(profiles: List[dict]) -> str:
    if not profiles:
        raise SystemExit("No profiles generated")
    import json

    data_json = json.dumps(profiles)
    html = f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\" />
  <title>Ballyhoura Trail Elevation Profiles</title>
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 1.5rem; }}
    h1 {{ margin-bottom: 0.5rem; }}
    #controls {{ margin-bottom: 1rem; }}
    #chart {{ width: 100%; height: 60vh; }}
    select {{ min-width: 280px; padding: 0.4rem; }}
  </style>
  <script src=\"https://cdn.plot.ly/plotly-2.32.0.min.js\"></script>
</head>
<body>
  <h1>Ballyhoura Trail Elevation Profiles</h1>
  <div id=\"controls\">
    <label for=\"trail-select\">Choose trail:</label>
    <select id=\"trail-select\"></select>
  </div>
  <div id=\"chart\"></div>
  <script>
    const profiles = {data_json};

    function renderProfile(index) {{
      const profile = profiles[index];
      const trace = {{
        x: profile.dist_km,
        y: profile.elev_m,
        mode: 'lines',
        type: 'scatter',
        line: {{ color: '#1f77b4', width: 2 }},
        fill: 'tozeroy',
        fillcolor: 'rgba(31, 119, 180, 0.2)',
        hovertemplate: 'Distance: %{{x:.2f}} km<extra>Elevation: %{{y:.0f}} m</extra>',
        name: `${{profile.loop}} — ${{profile.label}}`
      }};
      const layout = {{
        title: `${{profile.label}} (${{profile.loop}})` ,
        xaxis: {{ title: 'Distance (km)' }},
        yaxis: {{ title: 'Elevation (m)' }},
        hovermode: 'x unified',
        margin: {{ t: 60, r: 30, b: 60, l: 70 }}
      }};
      Plotly.react('chart', [trace], layout, {{ responsive: true }});
    }}

    function init() {{
      const select = document.getElementById('trail-select');
      profiles.forEach((profile, idx) => {{
        const option = document.createElement('option');
        option.value = idx;
        option.textContent = `${{profile.loop}}: ${{profile.label}}`;
        select.appendChild(option);
      }});
      select.addEventListener('change', (event) => {{
        renderProfile(parseInt(event.target.value, 10));
      }});
      renderProfile(0);
    }}

    document.addEventListener('DOMContentLoaded', init);
  </script>
</body>
</html>"""
    return html


def main() -> None:
    args = parse_args()
    elevation_index = merge_elevations(args.elevations, args.tolerance)
    nodes, ways, relations = load_overpass(args.input)
    profiles = collect_profiles(
        nodes,
        ways,
        relations,
        elevation_index,
        args.loops,
        max_segment=args.max_segment,
        batch_size=args.batch_size,
        sleep=args.sleep,
        max_retries=args.max_retries,
    )
    profiles.sort(key=lambda p: p["sequence"])
    for profile in profiles:
        profile.pop("sequence", None)
    html = build_html(profiles)
    args.output.write_text(html, encoding="utf-8")
    print(f"Saved interactive profiles to {args.output}")


if __name__ == "__main__":
    main()
