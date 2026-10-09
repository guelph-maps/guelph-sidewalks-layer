"""Unit tests for the paged ArcGIS download and its hash cache.

The buildings layer fetched one reply; this layer's service caps a reply at
maxRecordCount rows, so the download pages through it and checks the total
against the service's own returnCountOnly answer. What these pin is that the
pages are assembled in order, that a lost or extra page stops the run before
anything is saved, and that an ArcGIS error -- which comes back as HTTP 200 --
is never written as data.
"""

import json
import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from addressvault import net  # noqa: E402
from src import config, download as dl  # noqa: E402

PAGE = 3


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Server Error")

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def _feature(oid):
    return {"type": "Feature",
            "geometry": {"type": "LineString",
                         "coordinates": [[-80.25, 43.54], [-80.249, 43.54]]},
            "properties": {"OBJECTID": oid, "STATUS": "BUILT"}}


def service(rows=7, count=None, page_override=None):
    """A requests.get for a layer of ``rows`` features.

    ``count`` is what returnCountOnly claims (default: the truth);
    ``page_override`` maps an offset to the payload served for that page.
    """
    calls = []

    def get(url, params=None, headers=None, timeout=None):
        calls.append(dict(params))
        if params.get("returnCountOnly") == "true":
            return FakeResponse({"count": rows if count is None else count})
        offset = int(params["resultOffset"])
        size = int(params["resultRecordCount"])
        if page_override and offset in page_override:
            return FakeResponse(page_override[offset])
        feats = [_feature(i) for i in range(offset, min(offset + size, rows))]
        return FakeResponse({"type": "FeatureCollection", "features": feats})

    get.calls = calls
    return get


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "LAST_DOWNLOAD_PATH",
                        str(tmp_path / ".last-download.json"))
    monkeypatch.setattr(config, "PAGE_SIZE", PAGE)
    monkeypatch.setattr(dl, "RETRY_WAIT", 0)
    monkeypatch.setattr(dl.time, "sleep", lambda _s: None)
    monkeypatch.setattr("src.download.net.wait_for_link", lambda **k: None)
    return tmp_path


def _saved(tmp_path):
    return sorted(tmp_path.glob("sidewalks-*.geojson"))


def test_pages_are_assembled_in_order(setup, monkeypatch):
    get = service(rows=7)
    monkeypatch.setattr("src.download.requests.get", get)
    status, path = dl.download()
    assert status == "DOWNLOADED"
    assert os.path.basename(path).startswith("sidewalks-")
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    assert doc["type"] == "FeatureCollection"
    assert [f["properties"]["OBJECTID"] for f in doc["features"]] == list(range(7))
    # The count, then 3 + 3 + 1: the short page ends it.
    pages = [c for c in get.calls if "resultOffset" in c]
    assert [c["resultOffset"] for c in pages] == ["0", "3", "6"]
    assert get.calls[0]["returnCountOnly"] == "true"


def test_every_page_asks_for_wgs84_in_a_stable_order(setup, monkeypatch):
    # The service stores UTM metres; without outSR=4326 every lon/lat
    # downstream is wrong. Without an order, pages can overlap or skip.
    get = service(rows=4)
    monkeypatch.setattr("src.download.requests.get", get)
    dl.download()
    for c in get.calls[1:]:
        assert c["outSR"] == "4326"
        assert c["orderByFields"] == "OBJECTID"
        assert c["f"] == "geojson"
        assert c["resultRecordCount"] == str(PAGE)


def test_an_exact_multiple_stops_without_an_empty_page(setup, monkeypatch):
    get = service(rows=6)
    monkeypatch.setattr("src.download.requests.get", get)
    status, _path = dl.download()
    assert status == "DOWNLOADED"
    assert [c.get("resultOffset") for c in get.calls[1:]] == ["0", "3"]


def test_a_lost_page_stops_the_run_and_saves_nothing(setup, monkeypatch):
    # The count says 7; the second page comes back short with only 2.
    short = {"type": "FeatureCollection", "features": [_feature(3), _feature(4)]}
    monkeypatch.setattr("src.download.requests.get",
                        service(rows=7, page_override={3: short}))
    with pytest.raises(RuntimeError, match="Not saving"):
        dl.download()
    assert _saved(setup) == []
    assert not os.path.exists(config.LAST_DOWNLOAD_PATH)


def test_more_rows_than_the_count_stops_the_run(setup, monkeypatch):
    monkeypatch.setattr("src.download.requests.get", service(rows=7, count=5))
    with pytest.raises(RuntimeError, match="counts 5"):
        dl.download()
    assert _saved(setup) == []


def test_an_arcgis_error_on_a_page_is_not_written_as_data(setup, monkeypatch):
    # ArcGIS answers an error with HTTP 200, so raise_for_status proves nothing.
    err = {"error": {"code": 400, "message": "Invalid or missing input parameters"}}
    monkeypatch.setattr("src.download.requests.get",
                        service(rows=7, page_override={3: err}))
    with pytest.raises(RuntimeError, match="returned an error"):
        dl.download()
    assert _saved(setup) == []


def test_a_page_that_is_not_a_feature_collection_raises(setup, monkeypatch):
    monkeypatch.setattr("src.download.requests.get",
                        service(rows=7, page_override={0: {"features": []}}))
    with pytest.raises(RuntimeError, match="FeatureCollection"):
        dl.download()


@pytest.mark.parametrize("reply", [
    {"error": {"code": 498, "message": "Invalid token"}},
    {"count": 0},
    {"count": "5245"},
    {},
])
def test_no_usable_count_raises_before_any_page(setup, monkeypatch, reply):
    calls = []

    def get(url, params=None, headers=None, timeout=None):
        calls.append(params)
        return FakeResponse(reply)

    monkeypatch.setattr("src.download.requests.get", get)
    with pytest.raises(RuntimeError, match="no usable count"):
        dl.download()
    assert len(calls) == 1


def test_a_reply_that_is_not_json_raises(setup, monkeypatch):
    monkeypatch.setattr("src.download.requests.get",
                        lambda *a, **k: FakeResponse(ValueError("Expecting value")))
    with pytest.raises(RuntimeError, match="did not return JSON"):
        dl.download()


def test_an_unchanged_layer_is_skipped(setup, monkeypatch):
    # The cache key is the hash of the assembled features: a /query reply has
    # no meaningful Last-Modified.
    monkeypatch.setattr("src.download.requests.get", service(rows=7))
    first_status, first_path = dl.download()
    assert first_status == "DOWNLOADED"
    second_status, second_path = dl.download()
    assert (second_status, second_path) == ("SKIPPED", first_path)
    assert len(_saved(setup)) == 1


def test_force_downloads_an_unchanged_layer(setup, monkeypatch):
    monkeypatch.setattr("src.download.requests.get", service(rows=7))
    dl.download()
    status, path = dl.download(force=True)
    assert status == "DOWNLOADED"
    assert os.path.isfile(path)


def test_a_changed_layer_is_written(setup, monkeypatch):
    monkeypatch.setattr("src.download.requests.get", service(rows=7))
    dl.download()
    with open(config.LAST_DOWNLOAD_PATH, encoding="utf-8") as f:
        before = json.load(f)
    monkeypatch.setattr("src.download.requests.get", service(rows=8))
    status, _path = dl.download()
    assert status == "DOWNLOADED"
    with open(config.LAST_DOWNLOAD_PATH, encoding="utf-8") as f:
        after = json.load(f)
    assert after["sha256"] != before["sha256"]


def test_a_hash_match_with_the_file_gone_downloads_again(setup, monkeypatch):
    monkeypatch.setattr("src.download.requests.get", service(rows=7))
    _status, path = dl.download()
    os.remove(path)
    status, path2 = dl.download()
    assert status == "DOWNLOADED" and os.path.isfile(path2)


def test_the_sidecar_records_the_hash_and_file(setup, monkeypatch):
    monkeypatch.setattr("src.download.requests.get", service(rows=7))
    _status, path = dl.download()
    with open(config.LAST_DOWNLOAD_PATH, encoding="utf-8") as f:
        side = json.load(f)
    assert side["filename"] == os.path.basename(path)
    assert side["bytes"] == os.path.getsize(path)
    assert len(side["sha256"]) == 64


# --- the link gate and retries ----------------------------------------------

def test_a_dead_link_stops_before_any_request(setup, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("made a request on a dead link")

    def offline(**k):
        raise net.Offline("link is offline")

    monkeypatch.setattr("src.download.net.wait_for_link", offline)
    monkeypatch.setattr("src.download.requests.get", boom)
    with pytest.raises(net.Offline):
        dl.download()


def test_a_dropped_query_is_retried(setup, monkeypatch):
    inner = service(rows=7)
    state = {"n": 0}

    def get(*a, **k):
        state["n"] += 1
        if state["n"] == 2:  # the first page
            raise requests.ConnectionError("connection dropped")
        return inner(*a, **k)

    monkeypatch.setattr("src.download.requests.get", get)
    status, _path = dl.download()
    assert status == "DOWNLOADED"
    assert state["n"] == 5  # count, dropped page, then three pages


def test_out_of_retries_on_a_dead_link_raises_link_unavailable(setup, monkeypatch):
    # On exhaustion the run exits 75, not as a build failure against a
    # service that was never reachable.
    monkeypatch.setattr(dl, "RETRIES", 1)
    state = {"n": 0}

    def get(*a, **k):
        state["n"] += 1
        raise requests.ConnectionError("getaddrinfo failed")

    gate = {"n": 0}

    def wait_for_link(**k):
        gate["n"] += 1
        if gate["n"] > 1:  # open at the start, shut by the time retries run out
            raise net.Offline("link is offline")

    monkeypatch.setattr("src.download.requests.get", get)
    monkeypatch.setattr("src.download.net.wait_for_link", wait_for_link)
    with pytest.raises(net.Offline):
        dl.download()
    assert state["n"] == 2  # the initial attempt plus RETRIES


def test_out_of_retries_on_a_good_link_reraises_the_request_error(setup, monkeypatch):
    monkeypatch.setattr(dl, "RETRIES", 2)

    def get(*a, **k):
        raise requests.ConnectionError("reset by peer")

    monkeypatch.setattr("src.download.requests.get", get)
    with pytest.raises(requests.ConnectionError):
        dl.download()
