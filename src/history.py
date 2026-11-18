"""Keep the gap counts of every compare run.

Each live comparison appends one line to data/history.jsonl: the date, which
OSM data it saw, and the counts from the summary. That is the whole record of
the observer -- a handful of numbers a day -- and gaps/history.html charts it.
A run that fell back to cached OSM observed nothing new and is not recorded,
so the chart never shows a false plateau.

The parks layer can rebuild its past from Overpass attic queries; this one
deliberately cannot. An attic query for every road and footway in Guelph is a heavy
ask of a shared instance for each week reconstructed, so the record starts
with the first build and grows from there.
"""


import requests

from layerkit import history as _kit

from src import config

FIELDS = ("untraced_km", "missing", "partial", "far", "road_says_no", "untagged",
          "on_road_tag", "streets", "city_km")


def record(summary, day=None):
    """Append the run's row (replacing an earlier row from the same day)."""
    return _kit.record(summary, FIELDS, path=config.HISTORY_PATH, seed=seed,
                       day=day)


def load():
    """All rows, oldest first. An absent file is an empty history."""
    return _kit.load(config.HISTORY_PATH)


def seed():
    """Start a checkout that has no history from the published copy."""
    return _kit.seed(config.HISTORY_PATH, config.HISTORY_URL, config.USER_AGENT)


def _write(rows):
    _kit.write(config.HISTORY_PATH, rows)
