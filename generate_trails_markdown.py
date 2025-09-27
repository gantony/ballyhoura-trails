#!/usr/bin/env python3
"""Generate trails.md summarising Ballyhoura loop segments with lengths."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

ORDER = [
    "Greenwood Loop",
    "Mountrussell Loop",
    "Garrane Loop",
    "Castlepook Loop",
    "Streamhill Loop",
]
INFO_TAGS = ["highway", "mtb:scale"]
INPUT = Path("ballyhoura-overpass.json")
OUTPUT = Path("trails.md")

Point = Tuple[float, float]


def haversine(p1: Point, p2: Point) -> float:
    lat1, lon1 = map(math.radians, p1)
    lat2, lon2 = map(math.radians, p2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return 6371000.0 * c


def way_coordinates(way: dict, nodes: Dict[int, dict]) -> List[Point]:
    if "geometry" in way:
        return [(pt["lat"], pt["lon"]) for pt in way["geometry"]]
    coords: List[Point] = []
    for node_id in way.get("nodes", []):
        node = nodes.get(node_id)
        if node:
            coords.append((node["lat"], node["lon"]))
    return coords


def compute_length(coords: List[Point]) -> float:
    if len(coords) < 2:
        return 0.0
    return sum(haversine(a, b) for a, b in zip(coords, coords[1:]))


def way_label(way: dict) -> str:
    tags = way.get("tags", {})
    return tags.get("name") or tags.get("ref") or f"way {way['id']}"


def main() -> None:
    if not INPUT.exists():
        raise SystemExit(f"Input file not found: {INPUT}")
    with INPUT.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    nodes = {el["id"]: el for el in data.get("elements", []) if el.get("type") == "node"}
    ways = {el["id"]: el for el in data.get("elements", []) if el.get("type") == "way"}
    relations = [el for el in data.get("elements", []) if el.get("type") == "relation"]

    relation_lookup = {
        rel.get("tags", {}).get("name"): rel
        for rel in relations
        if rel.get("tags", {}).get("route") == "mtb"
    }

    emitted: set[str] = set()
    lines: List[str] = ["# Ballyhoura Trails Overview", ""]

    for loop_name in ORDER:
        relation = relation_lookup.get(loop_name)
        if not relation:
            continue
        lines.append(f"## {loop_name}")
        index = 1
        for member in relation.get("members", []):
            if member.get("type") != "way":
                continue
            way = ways.get(member.get("ref"))
            if not way:
                continue
            label = way_label(way)
            if label in emitted:
                continue
            emitted.add(label)
            coords = way_coordinates(way, nodes)
            length_m = compute_length(coords)
            tags = way.get("tags", {})
            annotations = [f"{length_m:.0f} m"]
            for tag in INFO_TAGS:
                if tag in tags:
                    value = tags[tag]
                    annotations.append(f"{tag}={value}")
            formatted = f"{index}. {label} ({', '.join(annotations)})"
            lines.append(formatted)
            index += 1
        lines.append("")

    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
