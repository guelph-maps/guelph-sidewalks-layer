"""Tests for the gap history record."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import config, history  # noqa: E402


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "HISTORY_PATH", str(tmp_path / "history.jsonl"))
    # Never reach for the published copy from a test.
    monkeypatch.setattr(history, "seed", lambda: False)
    return tmp_path


SUMMARY = {"untraced_km": 41.3, "missing": 520, "partial": 140, "far": 60,
           "road_says_no": 12, "untagged": 230, "streets": 88, "city_km": 712.4,
           "covered_km": 671.1, "on_road_tag": 4, "osm_sidewalk_ways": 9000,
           "osm_date": "2026-10-08 12:00Z", "osm_stale": False}


def test_a_live_run_is_recorded_with_every_field(paths):
    row = history.record(SUMMARY, day="2026-10-08")
    assert row == {"date": "2026-10-08", "osm_date": "2026-10-08 12:00Z",
                   "source": "live", "untraced_km": 41.3, "missing": 520,
                   "partial": 140, "far": 60, "road_says_no": 12,
                   "untagged": 230, "streets": 88, "city_km": 712.4}
    assert history.load() == [row]


def test_the_row_carries_exactly_the_fields():
    assert history.FIELDS == ("untraced_km", "missing", "partial", "far",
                              "road_says_no", "untagged", "streets", "city_km")


def test_an_absent_field_is_recorded_as_zero(paths):
    row = history.record({"missing": 3, "osm_date": "2026-10-08 12:00Z"},
                         day="2026-10-08")
    assert row["missing"] == 3
    assert all(row[k] == 0 for k in history.FIELDS if k != "missing")


def test_a_run_without_an_osm_date_is_dated_by_the_day(paths):
    row = history.record({**SUMMARY, "osm_date": None}, day="2026-10-08")
    assert row["osm_date"] == "2026-10-08"


def test_a_rerun_the_same_day_replaces_the_row(paths):
    history.record(SUMMARY, day="2026-10-01")
    history.record(SUMMARY, day="2026-10-08")
    history.record({**SUMMARY, "missing": 500}, day="2026-10-08")
    rows = history.load()
    assert [r["date"] for r in rows] == ["2026-10-01", "2026-10-08"]
    assert rows[-1]["missing"] == 500


def test_rows_are_kept_oldest_first(paths):
    history.record(SUMMARY, day="2026-10-08")
    history.record(SUMMARY, day="2026-09-24")
    history.record(SUMMARY, day="2026-10-01")
    assert [r["date"] for r in history.load()] == \
        ["2026-09-24", "2026-10-01", "2026-10-08"]
    with open(config.HISTORY_PATH, encoding="utf-8") as f:
        assert [l[:22] for l in f] == [
            '{"date": "2026-09-24",', '{"date": "2026-10-01",',
            '{"date": "2026-10-08",']


def test_a_stale_run_is_not_recorded(paths):
    assert history.record({**SUMMARY, "osm_stale": True}) is None
    assert history.load() == []


def test_an_absent_file_is_an_empty_history(paths):
    assert history.load() == []


# --- seeding from the published copy ----------------------------------------
# These call the real seed() with requests.get replaced: no network.

class _Resp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


@pytest.fixture
def seed_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(config, "HISTORY_PATH",
                        str(tmp_path / "data" / "history.jsonl"))
    return tmp_path


def test_seed_starts_an_empty_checkout_from_the_published_copy(seed_paths, monkeypatch):
    published = '{"date": "2026-10-01", "missing": 600}'
    monkeypatch.setattr(history.requests, "get",
                        lambda *a, **k: _Resp(200, published))
    assert history.seed() is True
    assert history.load() == [{"date": "2026-10-01", "missing": 600}]
    with open(config.HISTORY_PATH, encoding="utf-8") as f:
        assert f.read().endswith("\n")


def test_seed_leaves_an_existing_history_alone(seed_paths, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("fetched although a history exists")

    os.makedirs(config.DATA_DIR)
    with open(config.HISTORY_PATH, "w", encoding="utf-8") as f:
        f.write('{"date": "2026-10-01"}\n')
    monkeypatch.setattr(history.requests, "get", boom)
    assert history.seed() is False


@pytest.mark.parametrize("resp", [_Resp(404, "Not Found"), _Resp(200, "  \n")])
def test_seed_before_the_first_publish_does_nothing(seed_paths, monkeypatch, resp):
    monkeypatch.setattr(history.requests, "get", lambda *a, **k: resp)
    assert history.seed() is False
    assert not os.path.exists(config.HISTORY_PATH)


def test_seed_survives_no_network(seed_paths, monkeypatch):
    def offline(*a, **k):
        raise history.requests.ConnectionError("offline")

    monkeypatch.setattr(history.requests, "get", offline)
    assert history.seed() is False
