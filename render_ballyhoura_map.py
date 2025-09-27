#!/usr/bin/env python3
"""Render Ballyhoura MTB trails from an Overpass JSON dump to a PNG map.

Requires matplotlib (install via `pip install matplotlib`). The script reads
the Overpass export, extracts MTB-tagged ways, and plots them coloured by
`mtb:scale` where available. Optionally filter to one or more named loops.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import matplotlib.pyplot as plt

# Palette keyed by mtb:scale value. Fallback colours used when scale is absent.
MTB_SCALE_COLOURS: Dict[str, str] = {
    "0": "#1a9641",  # easiest
    "1": "#a6d96a",
    "2": "#fdae61",
    "3": "#d7191c",
    "4": "#800026",  # hardest in common Ballyhoura data
}
DEFAULT_ROUTE_COLOUR = "#2b83ba"
DEFAULT_PATH_COLOUR = "#7570b3"
LOOP_FALLBACK_COLOURS = {
    "garrane loop": "#d95f02",  # orange accent to avoid white-on-white
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input_json",
        nargs="?",
        default="ballyhoura-overpass.json",
        help="Path to Overpass JSON exported file (default: %(default)s)",
    )
    parser.add_argument(
        "output_png",
        nargs="?",
        default="ballyhoura-map.png",
        help="Filename for the rendered PNG map (default: %(default)s)",
    )
    parser.add_argument(
        "--figsize",
        type=float,
        nargs=2,
        metavar=("WIDTH", "HEIGHT"),
        default=(9.0, 9.0),
        help="Figure size in inches (default: %(default)s)",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=200,
        help="Image resolution in dots per inch (default: %(default)s)",
    )
    parser.add_argument(
        "--loop",
        action="append",
        dest="loops",
        metavar="NAME",
        help="Filter to loops whose name contains NAME (case-insensitive). Repeatable.",
    )
    return parser.parse_args()


def load_overpass(
    path: Path,
) -> Tuple[Dict[int, Tuple[float, float]], List[dict], Dict[int, List[dict]]]:
    """Return nodes, relevant ways, and relation metadata keyed by way id."""
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    nodes: Dict[int, Tuple[float, float]] = {}
    way_lookup: Dict[int, dict] = {}
    candidate_way_ids: set[int] = set()
    way_relations: Dict[int, List[dict]] = {}
    mtb_relations: Dict[int, dict] = {}

    for element in data.get("elements", []):
        element_type = element.get("type")
        if element_type == "node":
            nodes[element["id"]] = (element["lon"], element["lat"])
        elif element_type == "way":
            way_lookup[element["id"]] = element
            if is_mtb_way(element.get("tags", {})):
                candidate_way_ids.add(element["id"])
        elif element_type == "relation":
            tags = element.get("tags", {})
            if tags.get("route") == "mtb":
                mtb_relations[element["id"]] = tags
                for member in element.get("members", []):
                    if member.get("type") == "way":
                        way_id = member["ref"]
                        candidate_way_ids.add(way_id)
                        way_relations.setdefault(way_id, []).append({
                            "relation_id": element["id"],
                            "name": tags.get("name"),
                            "colour": tags.get("colour"),
                            "operator": tags.get("operator"),
                        })

    candidate_ways = [way_lookup[way_id] for way_id in candidate_way_ids if way_id in way_lookup]

    return nodes, candidate_ways, way_relations


def is_mtb_way(tags: dict) -> bool:
    """Check whether a way should be drawn."""
    if not tags:
        return False
    if tags.get("route") == "mtb":
        return True
    highway = tags.get("highway")
    if highway in {"path", "track", "cycleway"} and "mtb:scale" in tags:
        return True
    # Some trail segments are tagged as mtb trails without highway metadata.
    if tags.get("sport") == "mountain_bike":
        return True
    return False


def extract_geometry(way: dict, nodes: Dict[int, Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Return list of (lon, lat) tuples for the way, if available."""
    if "geometry" in way:
        return [(pt["lon"], pt["lat"]) for pt in way["geometry"]]
    coords: List[Tuple[float, float]] = []
    for node_id in way.get("nodes", []):
        if node_id in nodes:
            coords.append(nodes[node_id])
    return coords


def colour_for_way(tags: dict, relations: List[dict] | None) -> str:
    if relations:
        for rel in relations:
            colour = rel.get("colour")
            if colour:
                norm = colour.lower()
                if norm in {"#fff", "#ffffff", "white"}:
                    name = (rel.get("name") or "").lower()
                    if name in LOOP_FALLBACK_COLOURS:
                        return LOOP_FALLBACK_COLOURS[name]
                    return DEFAULT_ROUTE_COLOUR
                return colour
            name = (rel.get("name") or "").lower()
            if name in LOOP_FALLBACK_COLOURS:
                return LOOP_FALLBACK_COLOURS[name]
    scale = tags.get("mtb:scale")
    if scale and scale in MTB_SCALE_COLOURS:
        return MTB_SCALE_COLOURS[scale]
    colour = tags.get("colour") or tags.get("colour:mtb")
    if colour:
        return colour
    if tags.get("route") == "mtb":
        return DEFAULT_ROUTE_COLOUR
    if tags.get("highway") == "cycleway":
        return "#ff7f00"
    return DEFAULT_PATH_COLOUR


def render_map(
    nodes: Dict[int, Tuple[float, float]],
    ways: Iterable[dict],
    relation_index: Dict[int, List[dict]],
    output_path: Path,
    figsize: Tuple[float, float],
    dpi: int,
    map_title: str,
) -> None:
    fig, ax = plt.subplots(figsize=figsize, constrained_layout=True)

    legend_items = {}
    all_coords: List[Tuple[float, float]] = []

    for way in ways:
        tags = way.get("tags", {})
        coords = extract_geometry(way, nodes)
        if len(coords) < 2:
            continue
        xs, ys = zip(*coords)
        relations = relation_index.get(way.get("id"))
        colour = colour_for_way(tags, relations)
        label = legend_label(tags, relations)
        if label not in legend_items:
            legend_items[label] = colour
        ax.plot(xs, ys, color=colour, linewidth=1.6, alpha=0.9)
        all_coords.extend(coords)

    if not all_coords:
        raise SystemExit("No drawable MTB geometries found in input file")

    xs, ys = zip(*all_coords)
    ax.set_xlim(min(xs), max(xs))
    ax.set_ylim(min(ys), max(ys))
    ax.set_aspect("equal", adjustable="box")
    ax.set_title(map_title, fontsize=14)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.grid(True, linestyle=":", linewidth=0.4, alpha=0.5)

    # Create a compact legend.
    from matplotlib.lines import Line2D  # local import keeps matplotlib dependency minimal

    legend_handles = [
        Line2D([0], [0], color=colour, lw=2.0, label=label)
        for label, colour in sorted(legend_items.items())
    ]
    if legend_handles:
        ax.legend(handles=legend_handles, title="Trail difficulty", loc="lower left")

    fig.savefig(output_path, dpi=dpi)
    print(f"Saved map to {output_path}")


def legend_label(tags: dict, relations: List[dict] | None) -> str:
    if relations:
        names = [rel.get("name") for rel in relations if rel.get("name")]
        if names:
            return names[0] if len(names) == 1 else ", ".join(sorted(set(names)))
    scale = tags.get("mtb:scale")
    if scale:
        return f"mtb:scale {scale}"
    if tags.get("route") == "mtb":
        return tags.get("name", "MTB route")
    return "Other trail"


def filter_ways_by_loops(
    ways: List[dict],
    relation_index: Dict[int, List[dict]],
    loop_filters: Iterable[str],
) -> Tuple[List[dict], Dict[int, List[dict]], List[str]]:
    filters = [flt.lower() for flt in loop_filters if flt]
    if not filters:
        return ways, relation_index, []

    def matches(rel: dict) -> bool:
        name = (rel.get("name") or "").lower()
        return any(flt in name for flt in filters)

    filtered_ways: List[dict] = []
    filtered_index: Dict[int, List[dict]] = {}
    matched_names: List[str] = []

    for way in ways:
        way_id = way.get("id")
        rels = relation_index.get(way_id, [])
        matched_rels = [rel for rel in rels if matches(rel)]
        if matched_rels:
            filtered_ways.append(way)
            filtered_index[way_id] = matched_rels
            for rel in matched_rels:
                name = rel.get("name")
                if name and name not in matched_names:
                    matched_names.append(name)

    if not filtered_ways:
        raise SystemExit("No ways matched the requested loop filter(s)")

    return filtered_ways, filtered_index, matched_names


def generate_map(
    input_json: Path | str,
    output_png: Path | str,
    loops: Iterable[str] | None = None,
    figsize: Tuple[float, float] = (9.0, 9.0),
    dpi: int = 200,
) -> None:
    input_path = Path(input_json)
    output_path = Path(output_png)

    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")

    nodes, ways, relation_index = load_overpass(input_path)
    if loops:
        ways, relation_index, matched_names = filter_ways_by_loops(ways, relation_index, loops)
        title_suffix = ", ".join(matched_names or list(loops))
        map_title = f"Ballyhoura MTB - {title_suffix}"
    else:
        map_title = "Ballyhoura MTB Trails"

    render_map(nodes, ways, relation_index, output_path, figsize, dpi, map_title)


def main() -> None:
    args = parse_args()
    generate_map(
        input_json=args.input_json,
        output_png=args.output_png,
        loops=args.loops,
        figsize=tuple(args.figsize),
        dpi=args.dpi,
    )


if __name__ == "__main__":
    main()
