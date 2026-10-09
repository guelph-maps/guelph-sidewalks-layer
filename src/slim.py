"""Reduce the Sidewalks GeoJSON to a slim newline-delimited GeoJSON.

Every line is kept, built or proposed, and its properties are cut down to the
ones a mapper can use:

  id         the City's SIDEWALKID
  status     "built" or "proposed" (REQUIRED, never seen yet, reads as proposed)
  surface    as the City writes it, lower-cased ("concrete" on ~99%)
  width      metres, from the City's "1.52m (5'-0\")" style value
  installed  the install year, on the ~35% of rows that carry one
  length     metres, rounded

The one file, data/sidewalks-slim.geojsonl, feeds the tile builders and the
gap tool alike; data/sidewalks.count.json holds the counts the pages quote.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone

import ijson

from src import config

# Sanity bounds for the slimmed feature count (5,245 lines on 2026-10-09).
MIN_EXPECTED = 4_500
MAX_EXPECTED = 6_500

_WIDTH = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*m")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def slim(src_path):
    """Stream the GeoJSON into data/sidewalks-slim.geojsonl.

    Returns the slim file path. Raises if the feature count is implausible.
    """
    print(f"Slimming {src_path} ...")
    os.makedirs(config.DATA_DIR, exist_ok=True)

    count = built = 0
    built_m = 0.0
    skipped = 0
    with open(src_path, "rb") as src, \
            open(config.SLIM_PATH, "w", encoding="utf-8") as out:
        for feature in ijson.items(src, "features.item"):
            geom = feature.get("geometry") or {}
            if geom.get("type") not in ("LineString", "MultiLineString") \
                    or not geom.get("coordinates"):
                skipped += 1
                continue
            props = slim_props(feature.get("properties") or {})
            out.write(json.dumps({
                "type": "Feature",
                "geometry": {
                    "type": geom["type"],
                    "coordinates": _plain(geom["coordinates"]),
                },
                "properties": props,
            }) + "\n")
            count += 1
            if props["status"] == "built":
                built += 1
                built_m += props.get("length", 0)

    with open(config.COUNT_PATH, "w", encoding="utf-8") as f:
        json.dump({"lines": count, "built": built,
                   "built_km": round(built_m / 1000, 1)}, f)

    print(f"Done: {config.SLIM_PATH} ({count:,} lines, {built:,} built, "
          f"{built_m / 1000:,.1f} km built, {skipped:,} skipped)")
    if not MIN_EXPECTED <= count <= MAX_EXPECTED:
        raise RuntimeError(
            f"Slim feature count {count:,} is outside the expected range "
            f"{MIN_EXPECTED:,}-{MAX_EXPECTED:,} -- aborting."
        )
    return config.SLIM_PATH


def slim_props(props_in):
    """The kept properties of one City sidewalk."""
    props = {}
    sid = props_in.get(config.ID_KEY)
    if sid is not None:
        props["id"] = int(sid)
    status = str(props_in.get(config.STATUS_KEY) or "").strip().upper()
    props["status"] = "built" if status == config.BUILT_STATUS else "proposed"
    surface = props_in.get(config.SURFACE_KEY)
    if surface and str(surface).strip().upper() != "UNKNOWN":
        props["surface"] = str(surface).strip().lower()
    m = _WIDTH.match(str(props_in.get(config.WIDTH_KEY) or ""))
    if m:
        props["width"] = float(m.group(1))
    year = install_year(props_in.get(config.INSTALL_KEY))
    if year:
        props["installed"] = year
    length = props_in.get("Shape__Length")
    if length is not None:
        props["length"] = int(round(float(length)))
    return props


def install_year(ms):
    """The year of an ArcGIS epoch-millisecond date, or None."""
    if ms is None:
        return None
    return (_EPOCH + timedelta(milliseconds=int(ms))).year


def _plain(coords):
    """Recursively convert ijson Decimals to floats for compact json output."""
    if isinstance(coords, list):
        return [_plain(c) for c in coords]
    return float(coords)
