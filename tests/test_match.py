"""Unit tests for matching City sidewalk lines against OSM's lines.

Everything here is synthetic and metric: City lines and OSM ways are shapely
LineStrings in UTM metres, laid out along y = Y0 so an offset is just a change
of y. What these pin is the rule in compare's docstring -- how each sample is
classed, and how the classes add up to one status per City segment -- and the
one thing that is easy to get wrong when writing such tests: the 15 m match
radius reaches past the END of a way as well as across it, so a way along half
of a short City line covers rather more than half of it. The share tests use a
1 km line so that overrun is small next to the bands.
"""

import os
import sys

import pytest
import shapely

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import compare, config  # noqa: E402

X0, Y0 = 500000.0, 4820000.0


def _hline(x0, x1, dy=0.0):
    """A metric line along y = Y0 + dy, from X0 + x0 to X0 + x1."""
    return shapely.LineString([(X0 + x0, Y0 + dy), (X0 + x1, Y0 + dy)])


def _city(length=100.0, status="built", dy=0.0, **props):
    """A City sidewalk ``length`` metres long, starting at X0."""
    return {
        "props": {"id": 7, "status": status, **props},
        # Not reprojected by match(), only rounded into the gap feature.
        "geom": {"type": "LineString",
                 "coordinates": [[-80.25, 43.54], [-80.249, 43.54]]},
        "lines": [_hline(0, length, dy)],
    }


def _way(wid, g, **tags):
    return {"id": wid, "tags": tags, "g": g}


def _osm(sidewalk=(), other=(), road=()):
    return {"sidewalk": list(sidewalk), "other": list(other), "road": list(road)}


def _beside(dy, length=100.0, overhang=5.0):
    """A line parallel to a City line of ``length``, ``dy`` m off it.

    It runs a few metres past both ends, so an end sample sees the
    perpendicular distance rather than a corner.
    """
    return _hline(-overhang, length + overhang, dy)


def _status(city_item, osm):
    gaps, _summary = compare.match([city_item], osm)
    assert len(gaps) <= 1
    return gaps[0]["properties"]["status"] if gaps else None


# --- whole segments ---------------------------------------------------------

def test_a_surfaced_sidewalk_beside_it_is_not_listed():
    osm = _osm(sidewalk=[_way(1, _beside(5), highway="footway",
                              footway="sidewalk", surface="concrete")])
    gaps, summary = compare.match([_city()], osm)
    assert gaps == []
    assert summary["untraced_km"] == 0 and summary["covered_km"] == 0.1


def test_a_sidewalk_without_surface_is_listed_as_nosurface():
    osm = _osm(sidewalk=[_way(11, _beside(5), highway="footway",
                              footway="sidewalk")])
    gaps, _summary = compare.match([_city()], osm)
    assert [g["properties"]["status"] for g in gaps] == ["nosurface"]
    assert gaps[0]["properties"]["way"] == 11
    assert gaps[0]["properties"]["untraced"] == 0


def test_nothing_nearby_is_missing():
    assert _status(_city(), _osm()) == "missing"


def test_a_line_20_m_off_is_far():
    # Outside MATCH_M, inside FAR_M: the City alignment, not OSM, is suspect.
    osm = _osm(sidewalk=[_way(1, _beside(20), highway="footway",
                              footway="sidewalk", surface="concrete")])
    assert _status(_city(), osm) == "far"


def test_a_road_saying_sidewalk_no_is_road_says_no():
    road = _way(42, _beside(10), highway="residential", sidewalk="no")
    gaps, summary = compare.match([_city()], _osm(road=[road]))
    assert [g["properties"]["status"] for g in gaps] == ["road_says_no"]
    assert gaps[0]["properties"]["way"] == 42
    assert summary["says_no_km"] == 0.1


def test_a_road_saying_sidewalk_both_is_on_road_tag():
    road = _way(43, _beside(10), highway="residential", sidewalk="both")
    gaps, _summary = compare.match([_city()], _osm(road=[road]))
    assert [g["properties"]["status"] for g in gaps] == ["on_road_tag"]
    assert gaps[0]["properties"]["way"] == 43
    assert gaps[0]["properties"]["untraced"] == 0


def test_sidewalk_separate_says_nothing():
    # "separate" means the sidewalk is its own way; with no such way in OSM
    # the City sidewalk is simply missing.
    road = _way(44, _beside(10), highway="residential", sidewalk="separate")
    assert _status(_city(), _osm(road=[road])) == "missing"


def test_a_plain_footway_is_untagged():
    fw = _way(5, _beside(5), highway="footway")
    gaps, _summary = compare.match([_city()], _osm(other=[fw]))
    assert [g["properties"]["status"] for g in gaps] == ["untagged"]
    assert gaps[0]["properties"]["way"] == 5


def test_a_crossing_is_a_path_not_an_untagged_sidewalk():
    xing = _way(6, _beside(5), highway="footway", footway="crossing")
    assert _status(_city(), _osm(other=[xing])) is None


def test_a_path_or_cycleway_covers_it():
    for tags in ({"highway": "path"}, {"highway": "cycleway"}):
        assert _status(_city(), _osm(other=[_way(8, _beside(5), **tags)])) is None


# --- shares -----------------------------------------------------------------

def _cover(covered, length=1000.0):
    """A surfaced sidewalk 5 m beside the first ``covered`` m of the line."""
    return _osm(sidewalk=[_way(1, _hline(-5, covered, 5), highway="footway",
                               footway="sidewalk", surface="concrete")])


def test_a_short_partial_way_reaches_past_its_end():
    # The trap the shares below avoid: on a 100 m line, a way along the first
    # 50 m covers every sample within 15 m of its end too -- 65%, not 50%.
    assert _status(_city(100), _cover(50, 100)) == "partial"


@pytest.mark.parametrize("covered, expected", [
    (500, "partial"),   # ~51%
    (100, "missing"),   # ~11%
    (750, None),        # ~76%: matched
])
def test_the_covered_share_decides(covered, expected):
    assert _status(_city(1000), _cover(covered)) == expected


def test_a_partial_segment_reports_its_untraced_length():
    gaps, _summary = compare.match([_city(1000)], _cover(500))
    p = gaps[0]["properties"]
    assert p["length"] == 1000
    assert 470 <= p["untraced"] <= 500


# --- proposed ---------------------------------------------------------------

def test_a_proposed_line_with_nothing_is_proposed():
    gaps, summary = compare.match([_city(status="proposed")], _osm())
    assert [g["properties"]["status"] for g in gaps] == ["proposed"]
    # Not a gap in the built network: none of the built totals move.
    assert summary["city"] == 0 and summary["city_km"] == 0
    assert summary["untraced_km"] == 0 and summary["missing"] == 0


def test_a_proposed_line_already_in_osm_is_not_listed():
    osm = _osm(sidewalk=[_way(1, _beside(5), highway="footway",
                              footway="sidewalk")])
    assert _status(_city(status="proposed"), osm) is None


# --- streets ----------------------------------------------------------------

def test_a_gap_carries_the_nearest_named_road():
    road = _way(9, _beside(10), highway="residential", name="Deerpath Drive")
    gaps, _summary = compare.match([_city()], _osm(road=[road]))
    assert gaps[0]["properties"]["status"] == "missing"
    assert gaps[0]["properties"]["street"] == "Deerpath Drive"


def test_a_gap_with_no_named_road_near_has_an_empty_street():
    gaps, _summary = compare.match([_city()], _osm())
    assert gaps[0]["properties"]["street"] == ""


def test_a_street_is_counted_only_from_the_threshold():
    # 300 m untraced on Deerpath, 100 m on Elm: only Deerpath reaches the
    # 0.2 km headline threshold. The lines are 1 km apart so nothing crosses.
    long_ = _city(300)
    short = _city(100, dy=1000)
    roads = [
        _way(9, _beside(10, 300), highway="residential", name="Deerpath Drive"),
        _way(10, _hline(-5, 105, 1010), highway="residential", name="Elm Street"),
    ]
    gaps, summary = compare.match([long_, short], _osm(road=roads))
    assert [g["properties"]["street"] for g in gaps] == \
        ["Deerpath Drive", "Elm Street"]
    assert summary["streets"] == 1
    assert summary["gap_km"] == 0.4


def test_a_proposed_line_never_counts_toward_a_street():
    proposed = _city(300, status="proposed")
    road = _way(9, _beside(10, 300), highway="residential", name="Deerpath Drive")
    _gaps, summary = compare.match([proposed], _osm(road=[road]))
    assert summary["streets"] == 0


# --- the summary ------------------------------------------------------------

def test_the_summary_adds_up():
    # Three built lines, 1 km apart: 1000 m with nothing (missing, and nothing
    # within REMOTE_M), 500 m with a sidewalk 20 m off (far), 300 m traced.
    # Plus a proposed line, which none of the km figures may see.
    missing = _city(1000)
    far = _city(500, dy=1000)
    traced = _city(300, dy=2000)
    proposed = _city(200, status="proposed", dy=3000)
    osm = _osm(sidewalk=[
        _way(1, _hline(-5, 505, 1020), highway="footway", footway="sidewalk",
             surface="concrete"),
        _way(2, _hline(-5, 305, 2005), highway="footway", footway="sidewalk",
             surface="concrete"),
    ])
    gaps, s = compare.match([missing, far, traced, proposed], osm)
    assert [g["properties"]["status"] for g in gaps] == \
        ["missing", "far", "proposed"]
    assert s["city"] == 3
    assert s["city_km"] == 1.8
    assert s["covered_km"] == 0.3
    assert s["untraced_km"] == 1.5
    assert s["far_km"] == 0.5
    assert s["remote_km"] == 1.0
    assert s["says_no_km"] == 0
    assert (s["missing"], s["far"], s["proposed"], s["partial"]) == (1, 1, 1, 0)
    assert set(compare.STATUSES) <= set(s)


def test_a_gap_feature_carries_the_city_attributes():
    c = _city(id=123, installed=1998, surface="concrete", width=1.52)
    gaps, _summary = compare.match([c], _osm())
    p = gaps[0]["properties"]
    assert p == {"status": "missing", "street": "", "length": 100,
                 "untraced": 100, "id": 123, "installed": 1998,
                 "surface": "concrete", "width": 1.52}
    assert gaps[0]["type"] == "Feature"
    assert gaps[0]["geometry"]["type"] == "LineString"


def test_a_multiline_counts_every_part():
    c = _city(100)
    c["lines"].append(_hline(0, 100, 1000))
    gaps, s = compare.match([c], _osm())
    assert gaps[0]["properties"]["length"] == 200
    assert s["city_km"] == 0.2


def test_no_city_lines_is_an_empty_report():
    gaps, s = compare.match([], _osm())
    assert gaps == [] and s["city"] == 0 and s["city_km"] == 0


# --- segment_status ---------------------------------------------------------

@pytest.mark.parametrize("lengths, built, surfaced, expected", [
    ({}, True, 1.0, None),
    ({"none": 0}, True, 1.0, None),
    # the matched boundary is inclusive
    ({"sidewalk": 70, "none": 30}, True, 1.0, None),
    ({"sidewalk": 69.9, "none": 30.1}, True, 1.0, "partial"),
    # the partial boundary is inclusive
    ({"sidewalk": 20, "none": 80}, True, 1.0, "partial"),
    ({"sidewalk": 19, "none": 81}, True, 1.0, "missing"),
    # the kind of cover, once matched
    ({"footway": 80, "none": 20}, True, 1.0, "untagged"),
    ({"road_tag": 80, "none": 20}, True, 1.0, "on_road_tag"),
    ({"path": 80, "none": 20}, True, 1.0, None),
    ({"sidewalk": 80, "none": 20}, True, 0.4, "nosurface"),
    ({"sidewalk": 80, "none": 20}, True, 0.5, None),
    ({"sidewalk": 50, "footway": 40, "none": 10}, True, 1.0, None),
    ({"sidewalk": 30, "footway": 50, "none": 20}, True, 1.0, "untagged"),
    # what the uncovered part has near it names the gap; half is enough
    ({"says_no": 50, "none": 50}, True, 1.0, "road_says_no"),
    ({"says_no": 49, "none": 51}, True, 1.0, "missing"),
    ({"far": 50, "none": 50}, True, 1.0, "far"),
    ({"far": 40, "none": 60}, True, 1.0, "missing"),
    ({"says_no": 50, "far": 50}, True, 1.0, "road_says_no"),
    ({"sidewalk": 10, "far": 50, "none": 40}, True, 1.0, "far"),
    # not built
    ({"sidewalk": 70, "none": 30}, False, 1.0, None),
    ({"sidewalk": 69, "none": 31}, False, 1.0, "proposed"),
    ({"footway": 100}, False, 1.0, None),
    ({"none": 100}, False, 1.0, "proposed"),
])
def test_segment_status(lengths, built, surfaced, expected):
    assert compare.segment_status(lengths, built, surfaced) == expected


# --- tags -------------------------------------------------------------------

@pytest.mark.parametrize("tags, expected", [
    ({"sidewalk": "both"}, "yes"),
    ({"sidewalk": "left"}, "yes"),
    ({"sidewalk": "right"}, "yes"),
    ({"sidewalk": "yes"}, "yes"),
    ({"sidewalk:left": "yes"}, "yes"),
    ({"sidewalk:right": "yes", "sidewalk:left": "no"}, "yes"),
    ({"sidewalk": "separate"}, None),
    ({"sidewalk:both": "separate"}, None),
    ({"sidewalk:both": "no"}, "no"),
    ({"sidewalk": "none"}, "no"),
    ({"sidewalk": "no"}, "no"),
    ({}, None),
    ({"highway": "residential"}, None),
])
def test_road_sidewalk(tags, expected):
    assert compare.road_sidewalk(tags) == expected


@pytest.mark.parametrize("tags, expected", [
    ({"highway": "footway"}, True),
    ({"highway": "footway", "footway": "sidewalk"}, False),
    ({"highway": "footway", "footway": "crossing"}, False),
    ({"highway": "path"}, False),
    ({"highway": "cycleway"}, False),
])
def test_is_plain_footway(tags, expected):
    assert compare.is_plain_footway(tags) is expected


# --- classify ---------------------------------------------------------------

@pytest.mark.parametrize("osm, expected", [
    (_osm(), "none"),
    (_osm(sidewalk=[_way(1, _beside(14), footway="sidewalk")]), "sidewalk"),
    (_osm(sidewalk=[_way(1, _beside(16), footway="sidewalk")]), "far"),
    (_osm(sidewalk=[_way(1, _beside(26), footway="sidewalk")]), "none"),
    (_osm(other=[_way(2, _beside(5), highway="footway")]), "footway"),
    (_osm(other=[_way(2, _beside(5), highway="path")]), "path"),
    (_osm(road=[_way(3, _beside(19), highway="residential", sidewalk="yes")]),
     "road_tag"),
    (_osm(road=[_way(3, _beside(21), highway="residential", sidewalk="yes")]),
     "none"),
    (_osm(road=[_way(3, _beside(10), highway="residential", sidewalk="no")]),
     "says_no"),
])
def test_classify_one_sample(osm, expected):
    cls, _way_ids = compare.classify(
        shapely.points([[X0 + 50, Y0]]), osm)
    assert list(cls) == [expected]


def test_a_sidewalk_way_wins_over_a_nearer_footway_and_a_road():
    osm = _osm(
        sidewalk=[_way(1, _beside(12), footway="sidewalk")],
        other=[_way(2, _beside(2), highway="footway")],
        road=[_way(3, _beside(8), highway="residential", sidewalk="both")],
    )
    cls, ways = compare.classify(shapely.points([[X0 + 50, Y0]]), osm)
    assert (cls[0], ways[0]) == ("sidewalk", 1)


def test_a_pedestrian_line_wins_over_a_road_tag():
    osm = _osm(other=[_way(2, _beside(12), highway="path")],
               road=[_way(3, _beside(5), highway="residential", sidewalk="no")])
    cls, ways = compare.classify(shapely.points([[X0 + 50, Y0]]), osm)
    assert (cls[0], ways[0]) == ("path", 2)


def test_only_the_nearest_road_speaks():
    # A side street saying nothing sits between the sidewalk and the road
    # that says sidewalk=both: the nearest road is the one asked.
    osm = _osm(road=[
        _way(3, _beside(5), highway="service"),
        _way(4, _beside(15), highway="residential", sidewalk="both"),
    ])
    cls, _ways = compare.classify(shapely.points([[X0 + 50, Y0]]), osm)
    assert cls[0] == "none"
