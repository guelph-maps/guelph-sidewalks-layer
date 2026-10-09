"""Tests for the slim step: which properties survive, and the count guard."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import config  # noqa: E402
import src.slim as slim_mod  # noqa: E402
from src.slim import install_year, slim, slim_props  # noqa: E402

# 2004-06-15 00:00 UTC, in ArcGIS epoch milliseconds.
JUNE_2004_MS = 1087257600000


def test_slim_props_keeps_the_useful_fields():
    props = slim_props({
        "OBJECTID": 12, "SIDEWALKID": 4431.0, "STATUS": "BUILT",
        "SURFTYPE": "CONCRETE", "WIDTH": "1.52m (5'-0\")",
        "INSTALLDATE": JUNE_2004_MS, "DATACAPTUREACCURACY": "1 TO 5 METRES",
        "Shape__Length": 84.6,
    })
    assert props == {"id": 4431, "status": "built", "surface": "concrete",
                     "width": 1.52, "installed": 2004, "length": 85}


@pytest.mark.parametrize("raw, expected", [
    ("BUILT", "built"), (" built ", "built"),
    ("PROPOSED", "proposed"), ("REQUIRED", "proposed"),
    ("", "proposed"), (None, "proposed"),
])
def test_status(raw, expected):
    assert slim_props({"STATUS": raw})["status"] == expected


@pytest.mark.parametrize("raw, expected", [
    ("1.52m (5'-0\")", 1.52),
    ("1.8m (6'-0\")", 1.8),
    ("2m", 2.0),
    (" 1.5 m", 1.5),
    ("OTHER", None),
    ("", None),
    (None, None),
    ("5'-0\"", None),
])
def test_width(raw, expected):
    assert slim_props({"WIDTH": raw}).get("width") == expected


@pytest.mark.parametrize("raw, expected", [
    ("CONCRETE", "concrete"), (" Asphalt ", "asphalt"),
    ("UNKNOWN", None), ("unknown", None), ("", None), (None, None),
])
def test_surface(raw, expected):
    assert slim_props({"SURFTYPE": raw}).get("surface") == expected


def test_install_year():
    assert install_year(JUNE_2004_MS) == 2004
    assert install_year(None) is None
    # Before 1970 is a negative epoch; old sidewalks have them.
    assert install_year(-315619200000) == 1960
    assert install_year(0) == 1970


def test_a_missing_install_date_leaves_no_field():
    assert "installed" not in slim_props({"STATUS": "BUILT", "INSTALLDATE": None})


def test_slim_props_tolerates_an_empty_row():
    assert slim_props({}) == {"status": "proposed"}


# --- the whole file ---------------------------------------------------------

def _line(sid, status="BUILT", length=100.0, multi=False):
    a = [[-80.25, 43.54], [-80.249, 43.54]]
    geom = ({"type": "MultiLineString", "coordinates": [a, a]} if multi
            else {"type": "LineString", "coordinates": a})
    return {"type": "Feature", "geometry": geom,
            "properties": {"SIDEWALKID": sid, "STATUS": status,
                           "SURFTYPE": "CONCRETE", "Shape__Length": length}}


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "SLIM_PATH", str(tmp_path / "slim.geojsonl"))
    monkeypatch.setattr(config, "COUNT_PATH", str(tmp_path / "count.json"))
    monkeypatch.setattr(slim_mod, "MIN_EXPECTED", 2)
    monkeypatch.setattr(slim_mod, "MAX_EXPECTED", 10)
    return tmp_path


def _write_source(path, features):
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))


def test_slim_writes_one_line_per_line_and_skips_the_rest(paths):
    src = paths / "src.geojson"
    _write_source(src, [
        _line(1, length=1200.4),
        _line(2, "PROPOSED", length=50),
        _line(3, multi=True, length=300),
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]},
         "properties": {}},
        {"type": "Feature",
         "geometry": {"type": "Polygon",
                      "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
         "properties": {}},
        {"type": "Feature", "geometry": None, "properties": {}},
        {"type": "Feature", "geometry": {"type": "LineString", "coordinates": []},
         "properties": {}},
    ])
    assert slim(str(src)) == config.SLIM_PATH
    with open(config.SLIM_PATH, encoding="utf-8") as f:
        lines = [json.loads(l) for l in f]
    assert [l["properties"]["id"] for l in lines] == [1, 2, 3]
    assert [l["properties"]["status"] for l in lines] == \
        ["built", "proposed", "built"]
    assert lines[2]["geometry"]["type"] == "MultiLineString"
    # Plain floats, not ijson's Decimals.
    assert lines[0]["geometry"]["coordinates"] == [[-80.25, 43.54], [-80.249, 43.54]]
    with open(config.COUNT_PATH, encoding="utf-8") as f:
        assert json.load(f) == {"lines": 3, "built": 2, "built_km": 1.5}


def test_an_implausible_count_aborts(paths):
    src = paths / "src.geojson"
    _write_source(src, [_line(1)])
    with pytest.raises(RuntimeError, match="outside the expected range"):
        slim(str(src))
