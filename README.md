# Ballyhoura trails

In this repo, we extract MTB tracks data from cyclOSM (OpenStreetMap database) to generate various visualisations.

The main focus is around the official trails in Ballyhoura, next to Ardpatrick.

Area: https://www.cyclosm.org/#map=13/52.2979/-8.5451/cyclosm

## Scripts

- `fetch_ballyhoura_overpass.sh` – downloads the latest Ballyhoura MTB data via Overpass. Run `./fetch_ballyhoura_overpass.sh` (optional output filename as first argument).
- `render_ballyhoura_map.py` – renders the full trail network, or filtered loops via `--loop`. Requires `matplotlib`; e.g. `python render_ballyhoura_map.py --loop Garrane --loop Greenwood`.
- `render_loop_map.py` – convenience wrapper that outputs a PNG map for a single loop. Example: `python render_loop_map.py "Greenwood"`.
- `list_ballyhoura_trails.py` – prints the trail segments that make up each MTB loop with basic tagging. Run `python list_ballyhoura_trails.py`.

All scripts assume the Overpass export file is named `ballyhoura-overpass.json` unless an explicit path is provided.

## Visualisations

![Ballyhoura network](ballyhoura-map.png)

### Loop maps

![Greenwood Loop](greenwood-loop-map.png)

![Mountrussell Loop](mountrussell-loop-map.png)

![Garrane Loop](garrane-loop-map.png)

![Streamhill Loop](streamhill-loop-map.png)

![Castlepook Loop](castlepook-loop-map.png)
