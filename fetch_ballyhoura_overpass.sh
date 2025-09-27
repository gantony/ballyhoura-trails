#!/usr/bin/env bash
set -euo pipefail

OUTPUT="${1:-ballyhoura-overpass.json}"

read -r -d '' QUERY <<'QL' || true
[out:json][timeout:25];
(
  way["route"="mtb"](52.25,-8.62,52.34,-8.47);
  relation["route"="mtb"](52.25,-8.62,52.34,-8.47);
  way["highway"~"path|track"]["mtb:scale"](52.25,-8.62,52.34,-8.47);
);
(._;>;);
out geom;
QL

curl -sS -G "https://overpass-api.de/api/interpreter" \
  --data-urlencode "data=$QUERY" \
  -o "$OUTPUT"

echo "Saved Overpass response to $OUTPUT"
