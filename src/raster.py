"""Render the City's sidewalk lines into raster (PNG) map tiles using Pillow.

Pure Python -- no native dependencies, runs the same on Windows and Linux.

The buildings layer's renderer, for lines: no fill, no labels, one stroke per
line. Built sidewalks are cyan, which reads over both the OSM carto basemap
and aerial imagery and is neither the grey dashes iD draws a footway=sidewalk
in nor the violet of the buildings overlay, so a mapper running both can tell
them apart. A proposed sidewalk is orange (the gap page's "proposed" colour):
it may not have been poured.
"""

import json
import os
from collections import defaultdict

from PIL import Image, ImageDraw

from src import config
from src.tilemath import TILE_SIZE, lonlat_to_pixel

BUILT_COLOUR = (21, 170, 191, 255)      # #15aabf
PROPOSED_COLOUR = (232, 89, 12, 255)    # #e8590c
CLEAR = (0, 0, 0, 0)

# Stroke width by zoom: a 1.5 m sidewalk is under a pixel wide until z18, so
# the stroke is a marker, not a footprint. Two pixels keeps it visible over
# roof and tree texture; three at z17+ where the imagery is busier.
WIDE_STROKE_MIN_ZOOM = 17


def build_raster(slim_path=None):
    """Render PNG tiles for every zoom in config.RASTER_ZOOMS.

    Returns a dict {zoom: tile_count}.
    """
    slim_path = slim_path or config.SLIM_PATH
    lines = _read_lines(slim_path)
    return {z: _render_zoom(lines, z) for z in config.RASTER_ZOOMS}


def _read_lines(slim_path):
    """Return [(colour, [(lon, lat), ...]), ...]: one entry per line part."""
    lines = []
    with open(slim_path, encoding="utf-8") as f:
        for line in f:
            feat = json.loads(line)
            geom = feat["geometry"]
            colour = (BUILT_COLOUR if feat["properties"].get("status") == "built"
                      else PROPOSED_COLOUR)
            parts = (geom["coordinates"] if geom["type"] == "MultiLineString"
                     else [geom["coordinates"]])
            for part in parts:
                if len(part) >= 2:
                    lines.append((colour, part))
    return lines


def _render_zoom(lines, zoom):
    print(f"Raster z{zoom}: bucketing {len(lines):,} lines into tiles ...")
    width = 3 if zoom >= WIDE_STROKE_MIN_ZOOM else 2
    tiles = defaultdict(list)
    for colour, part in lines:
        projected = [lonlat_to_pixel(lon, lat, zoom) for lon, lat in part]
        xs = [p[0] for p in projected]
        ys = [p[1] for p in projected]
        for key in _tiles_touching((min(xs), min(ys), max(xs), max(ys)), width):
            tiles[key].append((colour, projected))

    out_dir = os.path.join(config.RASTER_TILE_DIR, str(zoom))
    print(f"Raster z{zoom}: rendering {len(tiles):,} tiles ...")
    made_dirs = set()
    written = 0
    for (tx, ty), parts in tiles.items():
        img = _render_tile(tx, ty, parts, width)
        if img.getbbox() is None:
            continue  # bbox over-approximation: the line never entered this tile
        tdir = os.path.join(out_dir, str(tx))
        if tdir not in made_dirs:
            os.makedirs(tdir, exist_ok=True)
            made_dirs.add(tdir)
        img.save(os.path.join(tdir, f"{ty}.png"), optimize=True)
        written += 1
    return written


def _tiles_touching(bbox, pad):
    x0, y0, x1, y1 = bbox
    for tx in range(int((x0 - pad) // TILE_SIZE), int((x1 + pad) // TILE_SIZE) + 1):
        for ty in range(int((y0 - pad) // TILE_SIZE), int((y1 + pad) // TILE_SIZE) + 1):
            yield (tx, ty)


def _render_tile(tx, ty, parts, width):
    img = Image.new("RGBA", (TILE_SIZE, TILE_SIZE), CLEAR)
    draw = ImageDraw.Draw(img)
    ox, oy = tx * TILE_SIZE, ty * TILE_SIZE
    # Built first, so a proposed extension drawn over its end stays visible.
    for colour, pts in sorted(parts, key=lambda p: p[0] == PROPOSED_COLOUR):
        draw.line([(x - ox, y - oy) for x, y in pts], fill=colour,
                  width=width, joint="curve")
    return img
