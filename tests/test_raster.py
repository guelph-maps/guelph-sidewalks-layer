"""Tests for the raster tile renderer."""

import json
import math
import os
import sys

import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import config  # noqa: E402
from src.raster import (  # noqa: E402
    BUILT_COLOUR, CLEAR, PROPOSED_COLOUR, _render_tile, _tiles_touching,
    build_raster,
)
from src.tilemath import TILE_SIZE, pixel_in_tile  # noqa: E402

LAT = 43.5448
BUILT = [(-80.2490, LAT), (-80.2470, LAT)]
PROPOSED = [(-80.2490, LAT + 0.001), (-80.2470, LAT + 0.001)]


@pytest.fixture
def tiles(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RASTER_TILE_DIR", str(tmp_path / "raster"))
    monkeypatch.setattr(config, "RASTER_ZOOMS", [16, 18])
    slim_path = tmp_path / "slim.geojsonl"
    with open(slim_path, "w", encoding="utf-8") as f:
        for status, pts in (("built", BUILT), ("proposed", PROPOSED)):
            f.write(json.dumps({
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": pts},
                "properties": {"status": status},
            }) + "\n")
        # A MultiLineString part and a degenerate one-point part.
        f.write(json.dumps({
            "type": "Feature",
            "geometry": {"type": "MultiLineString",
                         "coordinates": [[(-80.2490, LAT - 0.001),
                                          (-80.2470, LAT - 0.001)],
                                         [(-80.2480, LAT - 0.002)]]},
            "properties": {"status": "built"},
        }) + "\n")
    return tmp_path, str(slim_path)


def _pixel(root, lon, lat, zoom):
    tx, ty, ox, oy = pixel_in_tile(lon, lat, zoom)
    path = root / "raster" / str(zoom) / str(tx) / f"{ty}.png"
    assert path.is_file(), f"no tile at z{zoom}/{tx}/{ty}"
    with Image.open(path) as img:
        return img.convert("RGBA").getpixel((int(ox), int(oy)))


def test_tiles_are_written_for_every_zoom(tiles):
    root, slim_path = tiles
    counts = build_raster(slim_path)
    assert set(counts) == {16, 18}
    assert all(n > 0 for n in counts.values())
    for z, n in counts.items():
        assert len(list((root / "raster" / str(z)).rglob("*.png"))) == n


@pytest.mark.parametrize("zoom", [16, 18])
def test_a_built_line_is_drawn_in_the_built_colour(tiles, zoom):
    root, slim_path = tiles
    build_raster(slim_path)
    mid = (-80.2480, LAT)
    assert _pixel(root, *mid, zoom) == BUILT_COLOUR


def test_a_proposed_line_is_drawn_in_the_proposed_colour(tiles):
    root, slim_path = tiles
    build_raster(slim_path)
    assert _pixel(root, -80.2480, LAT + 0.001, 18) == PROPOSED_COLOUR


def test_every_part_of_a_multiline_is_drawn(tiles):
    root, slim_path = tiles
    build_raster(slim_path)
    assert _pixel(root, -80.2480, LAT - 0.001, 18) == BUILT_COLOUR


def test_off_the_line_is_clear(tiles):
    root, slim_path = tiles
    build_raster(slim_path)
    # ~30 m north of the built line at z18: well clear of a 3 px stroke.
    assert _pixel(root, -80.2480, LAT + 0.0003, 18) == CLEAR


def test_a_proposed_line_over_a_built_one_stays_visible():
    built = (BUILT_COLOUR, [(10, 100), (200, 100)])
    proposed = (PROPOSED_COLOUR, [(100, 100), (200, 100)])
    for parts in ([proposed, built], [built, proposed]):
        img = _render_tile(0, 0, parts, 3)
        assert img.getpixel((150, 100)) == PROPOSED_COLOUR
        assert img.getpixel((50, 100)) == BUILT_COLOUR


def test_tile_coordinates_are_local():
    a = _render_tile(0, 0, [(BUILT_COLOUR, [(10, 10), (60, 40)])], 2)
    b = _render_tile(1, 1, [(BUILT_COLOUR, [(266, 266), (316, 296)])], 2)
    assert a.tobytes() == b.tobytes()


def test_tiles_touching_includes_the_stroke_overhang():
    # A line ending exactly on a tile edge still paints its stroke there.
    assert set(_tiles_touching((10, 10, 256, 100), 1)) == {(0, 0), (1, 0)}
    assert set(_tiles_touching((10, 10, 200, 100), 1)) == {(0, 0)}


def _lonlat(px, py, zoom):
    """Global pixel -> lon/lat, the inverse of tilemath.lonlat_to_pixel."""
    scale = TILE_SIZE * 2 ** zoom
    lon = px / scale * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * py / scale))))
    return [lon, lat]


def test_a_tile_the_line_never_entered_is_not_written(tmp_path, monkeypatch):
    # The diagonal's bbox spans a 2 x 2 block of tiles, but it crosses the
    # vertical tile edge below the horizontal one, so it never enters the
    # top-right tile. That tile is rendered blank and must not be saved.
    monkeypatch.setattr(config, "RASTER_TILE_DIR", str(tmp_path / "raster"))
    monkeypatch.setattr(config, "RASTER_ZOOMS", [18])
    tx, ty, _ox, _oy = pixel_in_tile(-80.2480, LAT, 18)
    x0, y0 = tx * TILE_SIZE, ty * TILE_SIZE
    a = _lonlat(x0 + 200, y0 + 100, 18)
    b = _lonlat(x0 + 300, y0 + 400, 18)
    slim_path = tmp_path / "slim.geojsonl"
    slim_path.write_text(json.dumps({
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [a, b]},
        "properties": {"status": "built"},
    }) + "\n")
    assert build_raster(str(slim_path)) == {18: 3}
    written = {(int(p.parent.name), int(p.stem))
               for p in (tmp_path / "raster" / "18").rglob("*.png")}
    assert written == {(tx, ty), (tx, ty + 1), (tx + 1, ty + 1)}
