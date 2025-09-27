#!/usr/bin/env python3
"""List MTB trail segments grouped by loop from a Ballyhoura Overpass export."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input_json",
        nargs="?",
        default="ballyhoura-overpass.json",
        help="Path to the Overpass JSON file (default: %(default)s)",
    )
    return parser.parse_args()


def load_data(path: Path) -> Dict[str, Iterable[dict]]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    elements = data.get("elements", [])
    ways = {el["id"]: el for el in elements if el.get("type") == "way"}
    relations = [
        el for el in elements if el.get("type") == "relation" and el.get("tags", {}).get("route") == "mtb"
    ]
    return {"ways": ways, "relations": relations}


def summarize_loop(loop: dict, ways: Dict[int, dict]) -> Dict[str, dict]:
    segments = defaultdict(lambda: {"highways": set(), "mtb_scales": set(), "count": 0})
    for member in loop.get("members", []):
        if member.get("type") != "way":
            continue
        way = ways.get(member.get("ref"))
        if not way:
            continue
        tags = way.get("tags", {})
        name = tags.get("name") or tags.get("ref") or f"way {way['id']}"
        record = segments[name]
        record["count"] += 1
        if "highway" in tags:
            record["highways"].add(tags["highway"])
        if "mtb:scale" in tags:
            record["mtb_scales"].add(tags["mtb:scale"])
    return segments


def format_set(values: set[str]) -> str:
    return ",".join(sorted(values)) if values else "n/a"


def relation_order(name: str) -> int:
    order_map = {
        "GREENWOOD": 0,
        "MOUNT RUSSEL": 1,
        "MOUNTRUSSELL": 1,
        "GARANE": 2,
        "GARRANE": 2,
        "STREAMHILL": 3,
        "CASTLEPOOK": 4,
    }
    upper_name = name.upper()
    for key, order in order_map.items():
        if key in upper_name:
            return order
    return 99


def main() -> None:
    args = parse_args()
    input_path = Path(args.input_json)
    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")

    data = load_data(input_path)
    ways: Dict[int, dict] = data["ways"]
    relations = sorted(
        data["relations"],
        key=lambda rel: (
            relation_order(rel.get("tags", {}).get("name", "")),
            rel.get("tags", {}).get("name", ""),
        ),
    )

    if not relations:
        raise SystemExit("No MTB relations found in the input file")

    for relation in relations:
        tags = relation.get("tags", {})
        loop_name = tags.get("name", f"relation {relation['id']}")
        print(loop_name)
        segments = summarize_loop(relation, ways)
        for segment_name in sorted(segments):
            segment = segments[segment_name]
            highway = format_set(segment["highways"]) if segment["highways"] else "n/a"
            scale = format_set(segment["mtb_scales"]) if segment["mtb_scales"] else "n/a"
            count = segment["count"]
            print(f"  - {segment_name} (highway: {highway}; mtb:scale: {scale}; count: {count})")
        print()


if __name__ == "__main__":
    main()
