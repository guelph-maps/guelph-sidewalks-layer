"""Unit tests for the Overpass fetch: mirrors, retries, plausibility, the cache.

Carried over from the buildings layer, whose tests pin the failure that
actually happened there: overpass-api.de answered 504 two weeks running, the
single attempt gave up, and the run silently compared against a stale cache.
This layer adds two refusals on top. A reply under OSM_MIN_ELEMENTS is a
regional instance answering an out-of-region bbox, and believing it would
report every City sidewalk as missing. A reply whose timestamp_osm_base is
older than OSM_MAX_AGE_DAYS -- or that does not state one at all -- is a
lagging mirror, and believing it would print months-old OSM as this week's.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from addressvault import net  # noqa: E402
from src import compare, config  # noqa: E402

# A small floor keeps the fixtures light; the floor logic is what is tested.
FLOOR = 10


def _stamp(age):
    return (datetime.now(timezone.utc) - age).strftime("%Y-%m-%dT%H:%M:%SZ")


# Minutes old, as a healthy instance's reply is.
OSM_BASE = _stamp(timedelta(minutes=2))


def _reply(n=FLOOR + 1, stamp=OSM_BASE):
    data = {"elements": [{"type": "way", "id": i, "tags": {}, "geometry": []}
                         for i in range(n)]}
    if stamp is not None:
        data["osm3s"] = {"timestamp_osm_base": stamp}
    return data


GOOD = _reply()


class FakeResponse:
    def __init__(self, payload=None, status=200):
        self.payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Server Error")

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def _post(by_host):
    """A requests.post that answers per hostname from ``by_host``."""
    calls = []
    sent = []

    def post(url, data=None, headers=None, timeout=None):
        calls.append(url)
        sent.append({"data": data, "headers": headers, "timeout": timeout})
        for host, reply in by_host.items():
            if host in url:
                if isinstance(reply, Exception):
                    raise reply
                return reply
        raise requests.ConnectionError("no route")

    post.calls = calls
    post.sent = sent
    return post


def _hosts(post):
    return [u.split("/")[2] for u in post.calls]


@pytest.fixture
def paths(tmp_path, monkeypatch):
    """Point the cache + sidecar at a temp dir, and never really sleep."""
    monkeypatch.setattr(config, "OSM_CACHE_PATH", str(tmp_path / "osm-ways.json"))
    monkeypatch.setattr(config, "OSM_FETCH_PATH", str(tmp_path / ".osm-fetch.json"))
    monkeypatch.setattr(config, "OSM_MIN_ELEMENTS", FLOOR)
    monkeypatch.setattr(compare.time, "sleep", lambda _s: None)
    return tmp_path


@pytest.fixture
def online(monkeypatch):
    monkeypatch.setattr(compare.net, "wait_for_link", lambda **k: None)


def _write_cache(data=GOOD, sidecar=None):
    with open(config.OSM_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f)
    if sidecar is not None:
        with open(config.OSM_FETCH_PATH, "w", encoding="utf-8") as f:
            json.dump(sidecar, f)


# --- mirrors and rounds -----------------------------------------------------

def test_a_dead_primary_falls_through_to_the_next_mirror(paths, monkeypatch):
    monkeypatch.setattr(config, "OVERPASS_URLS",
                        ("https://a.example/api", "https://b.example/api"))
    post = _post({"a.example": FakeResponse(status=504),
                  "b.example": FakeResponse(GOOD)})
    monkeypatch.setattr(compare.requests, "post", post)

    data, _date, from_cache = compare._load_osm()
    assert from_cache is False
    assert len(data["elements"]) == len(GOOD["elements"])
    assert _hosts(post) == ["a.example", "b.example"]


def test_the_first_good_mirror_ends_the_search(paths, monkeypatch):
    monkeypatch.setattr(config, "OVERPASS_URLS",
                        ("https://a.example/api", "https://b.example/api"))
    post = _post({"a.example": FakeResponse(GOOD), "b.example": FakeResponse(GOOD)})
    monkeypatch.setattr(compare.requests, "post", post)
    compare._load_osm()
    assert _hosts(post) == ["a.example"]


def test_the_mirror_list_is_retried_before_giving_up(paths, monkeypatch):
    monkeypatch.setattr(config, "OVERPASS_URLS", ("https://a.example/api",))
    monkeypatch.setattr(config, "OVERPASS_ROUNDS", 3)
    replies = [FakeResponse(status=504), FakeResponse(status=504),
               FakeResponse(GOOD)]
    slept = []
    monkeypatch.setattr(compare.time, "sleep", slept.append)
    monkeypatch.setattr(compare.requests, "post", lambda *a, **k: replies.pop(0))

    _data, _date, from_cache = compare._load_osm()
    assert from_cache is False
    assert replies == [], "every round should have been used"
    # A wait between rounds, none before the first.
    assert slept == [config.OVERPASS_ROUND_WAIT] * 2


def test_a_reply_that_is_not_json_is_skipped(paths, monkeypatch):
    monkeypatch.setattr(config, "OVERPASS_URLS",
                        ("https://html.example/api", "https://good.example/api"))
    post = _post({"html.example": FakeResponse(ValueError("Expecting value")),
                  "good.example": FakeResponse(GOOD)})
    monkeypatch.setattr(compare.requests, "post", post)
    _data, _date, from_cache = compare._load_osm()
    assert from_cache is False
    assert _hosts(post) == ["html.example", "good.example"]


# --- the element floor ------------------------------------------------------

def test_an_implausibly_small_reply_is_not_accepted(paths, monkeypatch):
    # A regional instance answers a Guelph bbox with HTTP 200 and nothing in
    # it. Believing it would report every City sidewalk as missing from OSM.
    monkeypatch.setattr(config, "OVERPASS_URLS",
                        ("https://regional.example/api", "https://good.example/api"))
    post = _post({"regional.example": FakeResponse(_reply(n=0)),
                  "good.example": FakeResponse(GOOD)})
    monkeypatch.setattr(compare.requests, "post", post)

    data, _date, from_cache = compare._load_osm()
    assert from_cache is False
    assert len(data["elements"]) == len(GOOD["elements"])
    assert _hosts(post) == ["regional.example", "good.example"]


@pytest.mark.parametrize("n, accepted", [(FLOOR - 1, False), (FLOOR, True)])
def test_the_floor_itself_is_enough(paths, monkeypatch, n, accepted):
    monkeypatch.setattr(compare.requests, "post",
                        _post({"m.example": FakeResponse(_reply(n=n))}))
    got = compare._try_overpass("https://m.example/api", "q")
    assert (got is not None) is accepted


def test_the_real_floor_is_set():
    # About half the first live reply (30,494 ways); zero would wave the empty
    # regional answer straight through.
    assert config.OSM_MIN_ELEMENTS >= 10000


# --- the age check ----------------------------------------------------------

def test_a_reply_from_months_old_data_is_not_accepted(paths, monkeypatch):
    # overpass.private.coffee answered the recalibration in full, with OSM as
    # of 2026-05-31.
    monkeypatch.setattr(config, "OVERPASS_URLS",
                        ("https://lagging.example/api", "https://good.example/api"))
    stale = _reply(stamp="2026-05-31T03:25:00Z")
    post = _post({"lagging.example": FakeResponse(stale),
                  "good.example": FakeResponse(GOOD)})
    monkeypatch.setattr(compare.requests, "post", post)

    _data, osm_date, from_cache = compare._load_osm()
    assert from_cache is False
    assert osm_date == f"{OSM_BASE[:10]} {OSM_BASE[11:16]}Z"
    assert _hosts(post) == ["lagging.example", "good.example"]


@pytest.mark.parametrize("age, accepted", [
    (timedelta(days=config.OSM_MAX_AGE_DAYS) - timedelta(hours=1), True),
    (timedelta(days=config.OSM_MAX_AGE_DAYS) + timedelta(hours=1), False),
])
def test_the_age_limit(paths, monkeypatch, age, accepted):
    monkeypatch.setattr(compare.requests, "post",
                        _post({"m.example": FakeResponse(_reply(stamp=_stamp(age)))}))
    got = compare._try_overpass("https://m.example/api", "q")
    assert (got is not None) is accepted


def test_a_reply_that_does_not_say_how_old_it_is_is_refused(paths, monkeypatch):
    # No osm3s block at all: no benefit of the doubt, the next mirror is asked.
    monkeypatch.setattr(config, "OVERPASS_URLS",
                        ("https://terse.example/api", "https://good.example/api"))
    post = _post({"terse.example": FakeResponse(_reply(stamp=None)),
                  "good.example": FakeResponse(GOOD)})
    monkeypatch.setattr(compare.requests, "post", post)

    _data, osm_date, from_cache = compare._load_osm()
    assert from_cache is False
    assert osm_date.startswith(OSM_BASE[:10])
    assert _hosts(post) == ["terse.example", "good.example"]


def test_only_undated_replies_and_no_cache_is_an_outage(paths, monkeypatch, online):
    monkeypatch.setattr(config, "OVERPASS_URLS", ("https://terse.example/api",))
    monkeypatch.setattr(config, "OVERPASS_ROUNDS", 2)
    post = _post({"terse.example": FakeResponse(_reply(stamp=None))})
    monkeypatch.setattr(compare.requests, "post", post)
    with pytest.raises(compare.OsmUnavailable):
        compare._load_osm()
    assert len(post.calls) == 2
    assert not os.path.exists(config.OSM_CACHE_PATH), "a refused reply is not cached"


@pytest.mark.parametrize("osm3s", [
    {"timestamp_osm_base": ""},
    {"timestamp_osm_base": "   "},
    {"timestamp_osm_base": "last tuesday"},
    {"timestamp_osm_base": None},
    {},
    None,
])
def test_an_empty_or_garbled_timestamp_is_refused(paths, monkeypatch, osm3s):
    reply = {**_reply(stamp=None), "osm3s": osm3s}
    monkeypatch.setattr(compare.requests, "post",
                        _post({"m.example": FakeResponse(reply)}))
    assert compare._try_overpass("https://m.example/api", "q") is None


# A stamp with no zone cannot be aged against UTC: refused, not a TypeError
# that aborts the whole fetch before the next mirror is asked.
def test_a_timestamp_without_a_zone_is_refused_not_fatal(paths, monkeypatch):
    naive = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    monkeypatch.setattr(compare.requests, "post",
                        _post({"m.example": FakeResponse(_reply(stamp=naive))}))
    assert compare._try_overpass("https://m.example/api", "q") is None


# --- what a good fetch leaves behind ----------------------------------------

def test_a_good_fetch_records_when_and_where_it_came_from(paths, monkeypatch):
    monkeypatch.setattr(config, "OVERPASS_URLS", ("https://good.example/api",))
    monkeypatch.setattr(compare.requests, "post",
                        _post({"good.example": FakeResponse(GOOD)}))

    _data, osm_date, _from_cache = compare._load_osm()
    with open(config.OSM_FETCH_PATH, encoding="utf-8") as f:
        sidecar = json.load(f)
    # The page prints the OSM database timestamp the mirror states about its
    # own reply, not the day of the fetch.
    assert osm_date == f"{OSM_BASE[:10]} {OSM_BASE[11:16]}Z"
    assert sidecar["mirror"] == "good.example"
    assert sidecar["timestamp_osm_base"] == OSM_BASE
    assert sidecar["osm_base"] == osm_date
    with open(config.OSM_CACHE_PATH, encoding="utf-8") as f:
        assert json.load(f) == GOOD


# --- the cache --------------------------------------------------------------

def test_the_cache_is_used_only_after_every_mirror_fails(paths, monkeypatch, online):
    _write_cache(sidecar={"fetched": "2026-10-01", "mirror": "old.example",
                          "osm_base": "2026-09-30 22:10Z"})
    monkeypatch.setattr(config, "OVERPASS_URLS",
                        ("https://a.example/api", "https://b.example/api"))
    monkeypatch.setattr(config, "OVERPASS_ROUNDS", 2)
    post = _post({"a.example": FakeResponse(status=504),
                  "b.example": FakeResponse(status=504)})
    monkeypatch.setattr(compare.requests, "post", post)

    data, when, from_cache = compare._load_osm()
    assert len(post.calls) == 4, "both mirrors, both rounds"
    # The date carried out is the cache's own OSM base, not today's.
    assert (when, from_cache) == ("2026-09-30 22:10Z", True)
    assert data == GOOD


def test_a_cache_sidecar_without_osm_base_uses_the_fetch_day(paths, monkeypatch, online):
    _write_cache(sidecar={"fetched": "2026-10-01", "mirror": "old.example"})
    monkeypatch.setattr(config, "OVERPASS_URLS", ("https://a.example/api",))
    monkeypatch.setattr(config, "OVERPASS_ROUNDS", 1)
    monkeypatch.setattr(compare.requests, "post",
                        _post({"a.example": FakeResponse(status=504)}))
    _data, when, from_cache = compare._load_osm()
    assert (when, from_cache) == ("2026-10-01", True)


def test_a_stale_mirror_does_not_overwrite_the_cache(paths, monkeypatch, online):
    old = _reply(n=FLOOR + 3)
    _write_cache(old, sidecar={"osm_base": "2026-10-05 10:00Z"})
    monkeypatch.setattr(config, "OVERPASS_URLS", ("https://lagging.example/api",))
    monkeypatch.setattr(config, "OVERPASS_ROUNDS", 1)
    monkeypatch.setattr(compare.requests, "post", _post(
        {"lagging.example": FakeResponse(_reply(stamp="2026-05-31T03:25:00Z"))}))
    data, when, from_cache = compare._load_osm()
    assert (when, from_cache) == ("2026-10-05 10:00Z", True)
    assert data == old


def test_with_no_cache_and_no_mirror_the_run_is_told_to_fail(paths, monkeypatch, online):
    monkeypatch.setattr(config, "OVERPASS_URLS", ("https://a.example/api",))
    monkeypatch.setattr(config, "OVERPASS_ROUNDS", 1)
    monkeypatch.setattr(compare.requests, "post",
                        _post({"a.example": FakeResponse(status=504)}))
    with pytest.raises(compare.OsmUnavailable):
        compare._load_osm()


def _offline(monkeypatch):
    def no_link(**k):
        raise net.Offline("no usable link")
    monkeypatch.setattr(compare.net, "wait_for_link", no_link)


def test_being_offline_falls_back_to_the_cache(paths, monkeypatch):
    # No link means the fetch never happened; that is not a broken Overpass.
    _write_cache(sidecar={"osm_base": "2026-10-05 10:00Z"})
    monkeypatch.setattr(config, "OVERPASS_URLS", ("https://a.example/api",))
    monkeypatch.setattr(config, "OVERPASS_ROUNDS", 1)
    monkeypatch.setattr(compare.requests, "post",
                        _post({"a.example": requests.ConnectionError("offline")}))
    _offline(monkeypatch)
    _data, when, from_cache = compare._load_osm()
    assert (when, from_cache) == ("2026-10-05 10:00Z", True)


def test_being_offline_with_no_cache_is_link_unavailable(paths, monkeypatch):
    # Not OsmUnavailable: the run exits 75 (try later), not as a failed build.
    monkeypatch.setattr(config, "OVERPASS_URLS", ("https://a.example/api",))
    monkeypatch.setattr(config, "OVERPASS_ROUNDS", 1)
    monkeypatch.setattr(compare.requests, "post",
                        _post({"a.example": requests.ConnectionError("offline")}))
    _offline(monkeypatch)
    with pytest.raises(net.LinkUnavailable):
        compare._load_osm()


def test_the_cache_date_falls_back_to_the_file_mtime(paths):
    # A cache written before the sidecar existed still has to date itself.
    _write_cache()
    os.utime(config.OSM_CACHE_PATH, (1759500000, 1759500000))  # 2025-10-03
    assert compare._cache_date() in ("2025-10-03", "2025-10-04")


# --- the query --------------------------------------------------------------

def test_the_query_asks_for_every_road_and_pedestrian_way_in_the_bbox():
    q = compare._overpass_query()
    s, w, n, e = config.GUELPH_BBOX
    assert q.startswith("[out:json]")
    assert f"[timeout:{config.OVERPASS_TIMEOUT - 60}]" in q
    assert f"({s},{w},{n},{e})" in q
    assert q.rstrip().endswith("out tags geom;")
    # Ways only: a sidewalk is never a relation, nor a node.
    assert "way[" in q and "relation" not in q and "node" not in q
    regex = q.split('"highway"~"')[1].split('"')[0]
    assert regex.startswith("^(") and regex.endswith(")$")
    values = set(regex[2:-2].split("|"))
    assert values == set(config.ROAD_HIGHWAYS) | set(config.PATH_HIGHWAYS)
    assert {"footway", "residential", "cycleway", "service"} <= values


def test_the_query_is_what_is_posted(paths, monkeypatch):
    monkeypatch.setattr(config, "OVERPASS_URLS", ("https://good.example/api",))
    post = _post({"good.example": FakeResponse(GOOD)})
    monkeypatch.setattr(compare.requests, "post", post)
    compare._load_osm()
    sent = post.sent[0]
    assert sent["data"] == {"data": compare._overpass_query()}
    # Overpass answers the default requests User-Agent with a 406.
    assert sent["headers"]["User-Agent"] == config.USER_AGENT
    assert sent["timeout"] == config.OVERPASS_TIMEOUT


# --- splitting the reply ----------------------------------------------------

def _el(eid, tags, n=2, etype="way"):
    pts = [{"lat": 43.54, "lon": -80.25 + i * 0.001} for i in range(n)]
    return {"type": etype, "id": eid, "tags": tags, "geometry": pts}


def test_osm_ways_sorts_sidewalks_paths_and_roads():
    data = {"elements": [
        _el(1, {"highway": "footway", "footway": "sidewalk"}),
        _el(2, {"highway": "path", "path": "sidewalk"}),
        _el(3, {"highway": "footway"}),
        _el(4, {"highway": "footway", "footway": "crossing"}),
        _el(5, {"highway": "cycleway"}),
        _el(6, {"highway": "residential", "sidewalk": "both"}),
        _el(7, {"highway": "service"}),
        _el(8, {"highway": "pedestrian", "area": "yes"}),   # an outline, dropped
        _el(9, {"highway": "construction"}),                # neither, dropped
        _el(10, {"highway": "footway"}, n=1),               # one point, dropped
        _el(11, {"highway": "footway"}, etype="node"),      # not a way, dropped
        {"type": "way", "id": 12, "tags": {"highway": "footway"}},  # no geometry
        _el(13, {"highway": "footway", "footway": "sidewalk"}) | {"tags": None},
    ]}
    osm = compare._osm_ways(data)
    assert [w["id"] for w in osm["sidewalk"]] == [1, 2]
    assert [w["id"] for w in osm["other"]] == [3, 4, 5]
    assert [w["id"] for w in osm["road"]] == [6, 7]
    assert osm["road"][0]["tags"] == {"highway": "residential", "sidewalk": "both"}


def test_osm_ways_are_metric():
    # 0.001 deg of longitude at 43.54 N is ~80.7 m.
    osm = compare._osm_ways({"elements": [_el(1, {"highway": "footway"})]})
    g = osm["other"][0]["g"]
    assert 79 < g.length < 82
    x, y = g.coords[0]
    assert 500000 < x < 600000 and 4800000 < y < 4850000


def test_osm_ways_skip_null_geometry_points():
    el = _el(1, {"highway": "footway"}, n=3)
    el["geometry"][1] = None
    osm = compare._osm_ways({"elements": [el]})
    assert len(osm["other"][0]["g"].coords) == 2
