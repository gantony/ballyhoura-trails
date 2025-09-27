#!/usr/bin/env python3
"""Render Ballyhoura MTB trails to an interactive Folium (Leaflet) map.

Requires `pip install folium`.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import folium
from folium import Map
from folium.features import DivIcon
from folium.plugins import Fullscreen, GroupedLayerControl, MeasureControl, MiniMap, MousePosition

DEFAULT_INPUT = Path("ballyhoura-overpass.json")
DEFAULT_OUTPUT = Path("ballyhoura-map.html")
DEFAULT_TILE = "CartoDB positron"
LOOP_FALLBACK_COLOURS = {
    "garrane loop": "#d95f02",
}
DEFAULT_ROUTE_COLOUR = "#2b83ba"
DEFAULT_PATH_COLOUR = "#7570b3"
ORDERED_LOOPS = [
    "Greenwood Loop",
    "Mountrussell Loop",
    "Garrane Loop",
    "Streamhill Loop",
    "Castlepook Loop",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help="Overpass JSON file (default: %(default)s)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output HTML map path (default: %(default)s)",
    )
    parser.add_argument(
        "--loop",
        action="append",
        dest="loops",
        metavar="NAME",
        help="Filter to loops whose name contains NAME (case-insensitive). Repeatable.",
    )
    parser.add_argument(
        "--tiles",
        default=DEFAULT_TILE,
        help="Leaflet tile layer (default: %(default)s). Use 'OpenStreetMap', 'Stamen Terrain', etc.",
    )
    parser.add_argument(
        "--zoom",
        type=int,
        default=13,
        help="Initial zoom level (default: %(default)s)",
    )
    return parser.parse_args()


def load_overpass(path: Path) -> Tuple[Dict[int, Tuple[float, float]], List[dict], Dict[int, List[dict]]]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    nodes: Dict[int, Tuple[float, float]] = {}
    way_lookup: Dict[int, dict] = {}
    candidate_way_ids: set[int] = set()
    relation_index: Dict[int, List[dict]] = {}

    for element in data.get("elements", []):
        etype = element.get("type")
        if etype == "node":
            nodes[element["id"]] = (element["lon"], element["lat"])
        elif etype == "way":
            way_lookup[element["id"]] = element
            tags = element.get("tags", {})
            if is_mtb_way(tags):
                candidate_way_ids.add(element["id"])
        elif etype == "relation":
            tags = element.get("tags", {})
            if tags.get("route") == "mtb":
                for member in element.get("members", []):
                    if member.get("type") != "way":
                        continue
                    way_id = member["ref"]
                    candidate_way_ids.add(way_id)
                    relation_index.setdefault(way_id, []).append({
                        "name": tags.get("name"),
                        "colour": tags.get("colour"),
                    })

    ways = [way_lookup[wid] for wid in candidate_way_ids if wid in way_lookup]
    return nodes, ways, relation_index


def is_mtb_way(tags: dict) -> bool:
    if not tags:
        return False
    if tags.get("route") == "mtb":
        return True
    if tags.get("highway") in {"path", "track", "cycleway"} and "mtb:scale" in tags:
        return True
    if tags.get("sport") == "mountain_bike":
        return True
    return False


def filter_ways(
    ways: List[dict],
    relation_index: Dict[int, List[dict]],
    loop_filters: Iterable[str] | None,
) -> Tuple[List[dict], Dict[int, List[dict]], List[str]]:
    filters = [flt.lower() for flt in loop_filters or [] if flt]
    if not filters:
        matched_names = sorted({rel.get("name") for rels in relation_index.values() for rel in rels if rel.get("name")})
        return ways, relation_index, reorder_loops(matched_names)

    def matches(rel: dict) -> bool:
        name = (rel.get("name") or "").lower()
        return any(f in name for f in filters)

    filtered_ways: List[dict] = []
    filtered_index: Dict[int, List[dict]] = {}
    matched_names: List[str] = []

    for way in ways:
        wid = way.get("id")
        rels = relation_index.get(wid, [])
        matched_rels = [rel for rel in rels if matches(rel)]
        if matched_rels:
            filtered_ways.append(way)
            filtered_index[wid] = matched_rels
            for rel in matched_rels:
                name = rel.get("name")
                if name and name not in matched_names:
                    matched_names.append(name)

    if not filtered_ways:
        raise SystemExit("No ways matched the requested loop filter(s)")

    return filtered_ways, filtered_index, reorder_loops(matched_names)


def build_loop_segments(
    ways: List[dict],
    nodes: Dict[int, Tuple[float, float]],
    relation_index: Dict[int, List[dict]],
    ordered_loop_names: Iterable[str],
) -> Tuple[Dict[str, List[Tuple[List[Tuple[float, float]], str]]], List[Tuple[List[Tuple[float, float]], str]]]:
    loops: Dict[str, List[Tuple[List[Tuple[float, float]], str]]] = {name: [] for name in ordered_loop_names}
    extras: List[Tuple[List[Tuple[float, float]], str]] = []

    for way in ways:
        tags = way.get("tags", {})
        coords = way_coordinates(way, nodes)
        if len(coords) < 2:
            continue
        rels = relation_index.get(way.get("id"), [])
        if rels:
            for rel in rels:
                name = rel.get("name")
                if not name:
                    continue
                colour = colour_for_way(tags, [rel])
                loops.setdefault(name, []).append((coords, colour))
        else:
            extras.append((coords, colour_for_way(tags, None)))

    # Remove loops without segments
    loops = {name: segments for name, segments in loops.items() if segments}
    return loops, extras


def way_coordinates(way: dict, nodes: Dict[int, Tuple[float, float]]) -> List[Tuple[float, float]]:
    if "geometry" in way:
        return [(pt["lat"], pt["lon"]) for pt in way["geometry"]]
    coords: List[Tuple[float, float]] = []
    for node_id in way.get("nodes", []):
        if node_id in nodes:
            lon, lat = nodes[node_id]
            coords.append((lat, lon))
    return coords


def colour_for_way(tags: dict, relations: List[dict] | None) -> str:
    if relations:
        for rel in relations:
            colour = rel.get("colour")
            if colour:
                norm = colour.lower()
                if norm in {"white", "#fff", "#ffffff"}:
                    name = (rel.get("name") or "").lower()
                    if name in LOOP_FALLBACK_COLOURS:
                        return LOOP_FALLBACK_COLOURS[name]
                    return DEFAULT_ROUTE_COLOUR
                return colour
            name = (rel.get("name") or "").lower()
            if name in LOOP_FALLBACK_COLOURS:
                return LOOP_FALLBACK_COLOURS[name]
    if tags.get("mtb:scale"):
        scale = tags["mtb:scale"]
        palette = {
            "0": "#1a9641",
            "1": "#a6d96a",
            "2": "#fdae61",
            "3": "#d7191c",
            "4": "#800026",
        }
        if scale in palette:
            return palette[scale]
    if tags.get("route") == "mtb":
        return DEFAULT_ROUTE_COLOUR
    return DEFAULT_PATH_COLOUR


def create_map(
    loop_segments: Dict[str, List[Tuple[List[Tuple[float, float]], str]]],
    extras: List[Tuple[List[Tuple[float, float]], str]],
    *,
    tiles: str,
    zoom: int,
    default_loop: str | None,
) -> Map:
    all_coords = [coord for segments in loop_segments.values() for seg in segments for coord in seg[0]]
    if not all_coords and extras:
        all_coords = [coord for seg in extras for coord in seg[0]]
    if not all_coords:
        raise SystemExit("No drawable coordinates available")

    avg_lat = sum(lat for lat, _ in all_coords) / len(all_coords)
    avg_lon = sum(lon for _, lon in all_coords) / len(all_coords)

    fmap = folium.Map(location=(avg_lat, avg_lon), zoom_start=zoom, tiles=tiles, control_scale=True)
    Fullscreen(position="topright").add_to(fmap)
    MiniMap(toggle_display=True).add_to(fmap)
    MeasureControl(position="topright", primary_length_unit="meters").add_to(fmap)
    MousePosition(prefix="Lat/Lon:", separator=" | ", position="bottomright").add_to(fmap)

    groups: Dict[str, List[folium.FeatureGroup]] = {"Loops": []}
    default_key = default_loop.lower() if default_loop else None

    for idx, (loop_name, segments) in enumerate(loop_segments.items()):
        show = False
        if default_key:
            if loop_name.lower() == default_key:
                show = True
        elif idx == 0:
            show = True
        feature_group = folium.FeatureGroup(name=loop_name, show=show)
        for coords, colour in segments:
            folium.PolyLine(coords, color=colour, weight=4, opacity=0.9).add_to(feature_group)
        feature_group.add_to(fmap)
        groups["Loops"].append(feature_group)

    if extras:
        extras_group = folium.FeatureGroup(name="Shared segments", show=False)
        for coords, colour in extras:
            folium.PolyLine(coords, color=colour, weight=2.5, opacity=0.6).add_to(extras_group)
        extras_group.add_to(fmap)
        groups.setdefault("Extras", []).append(extras_group)

    GroupedLayerControl(groups=groups, exclusive_groups=["Loops"], collapsed=False).add_to(fmap)

    return fmap


def main() -> None:
    args = parse_args()
    if not args.input.exists():
        raise SystemExit(f"Input file not found: {args.input}")

    nodes, ways, relations = load_overpass(args.input)
    ways, relations, matched_names = filter_ways(ways, relations, args.loops)

    default_loop = choose_default_loop(matched_names, args.loops)
    loop_segments, extras = build_loop_segments(ways, nodes, relations, matched_names or [])
    if default_loop and default_loop not in loop_segments:
        default_loop = next(iter(loop_segments.keys()), None)

    fmap = create_map(
        loop_segments,
        extras,
        tiles=args.tiles,
        zoom=args.zoom,
        default_loop=default_loop,
    )

    if default_loop:
        title = default_loop
    elif matched_names:
        title = ", ".join(matched_names)
    elif args.loops:
        title = ", ".join(args.loops)
    else:
        title = "Ballyhoura MTB Trails"

    folium.map.Marker(
        location=fmap.location,
        icon=DivIcon(
            icon_size=(250, 36),
            icon_anchor=(0, 0),
            html=f'<div style="font-size:16px;font-weight:bold;background:rgba(255,255,255,0.8);padding:4px 6px;border-radius:4px;">{title}</div>',
        ),
    ).add_to(fmap)

    fmap.save(args.output)
    print(f"Saved map to {args.output}")


def choose_default_loop(loop_names: List[str], requested: Iterable[str] | None) -> str | None:
    if not loop_names:
        return None
    if not requested:
        return loop_names[0]
    request_lower = [req.lower() for req in requested if req]
    for req in request_lower:
        for name in loop_names:
            if req in name.lower():
                return name
    return loop_names[0]


def reorder_loops(loop_names: Iterable[str]) -> List[str]:
    loop_set = list(loop_names)
    ordered = []
    seen = set()
    for preferred in ORDERED_LOOPS:
        for name in loop_set:
            if name.lower() == preferred.lower() and name not in seen:
                ordered.append(name)
                seen.add(name)
    for name in loop_set:
        if name not in seen:
            ordered.append(name)
            seen.add(name)
    return ordered


if __name__ == "__main__":
    main()
