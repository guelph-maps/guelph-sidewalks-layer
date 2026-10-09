"""Keep the gap counts of every compare run.

Each live comparison appends one line to data/history.jsonl: the date, which
OSM data it saw, and the counts from the summary. That is the whole record of
the observer -- a handful of numbers a week -- and gaps/history.html charts it.
A run that fell back to cached OSM observed nothing new and is not recorded,
so the chart never shows a false plateau.

The parks layer can rebuild its past from Overpass attic queries; this one
deliberately cannot. An attic query for every road and footway in Guelph is a heavy
ask of a shared instance for each week reconstructed, so the record starts
with the first weekly build and grows from there.
"""

import json
import os
from datetime import date

import requests

from src import config

FIELDS = ("untraced_km", "missing", "partial", "far", "road_says_no", "untagged",
          "streets", "city_km")


def record(summary, day=None):
    """Append the run's row (replacing an earlier row from the same day)."""
    if summary.get("osm_stale"):
        print("History: not recorded (compared against cached OSM).")
        return None
    day = day or date.today().isoformat()
    row = {"date": day, "osm_date": summary.get("osm_date") or day,
           "source": "live", **{k: summary.get(k, 0) for k in FIELDS}}
    seed()
    rows = [r for r in load() if r["date"] != day] + [row]
    _write(rows)
    print(f"History: recorded {day} ({len(rows):,} runs on record).")
    return row


def load():
    """All rows, oldest first. An absent file is an empty history."""
    if not os.path.isfile(config.HISTORY_PATH):
        return []
    with open(config.HISTORY_PATH, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    return sorted(rows, key=lambda r: r["date"])


def seed():
    """Start a checkout that has no history from the published copy.

    data/ is not in git, so a fresh clone (or a wiped data dir) would otherwise
    begin the record again from this week. Best-effort: before the first
    publish there is nothing to fetch, and that is fine.
    """
    if os.path.isfile(config.HISTORY_PATH):
        return False
    try:
        resp = requests.get(config.HISTORY_URL,
                            headers={"User-Agent": config.USER_AGENT}, timeout=30)
    except requests.RequestException as e:
        print(f"History: could not fetch the published copy ({e}).")
        return False
    if resp.status_code != 200 or not resp.text.strip():
        return False
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(config.HISTORY_PATH, "w", encoding="utf-8") as f:
        f.write(resp.text if resp.text.endswith("\n") else resp.text + "\n")
    print(f"History: seeded from {config.HISTORY_URL}.")
    return True


def _write(rows):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    rows = sorted(rows, key=lambda r: r["date"])
    with open(config.HISTORY_PATH, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
