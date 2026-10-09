"""Download the Guelph Sidewalks GeoJSON from the City's ArcGIS service.

The buildings layer's download, made to page. The Sidewalks service caps a
reply at maxRecordCount = 1,000 rows and the layer has ~5,245, so the query is
issued page by page (resultOffset / resultRecordCount, ordered by OBJECTID so
the pages cannot overlap or skip) and the pages are assembled into one
FeatureCollection. The total is checked against the service's own
returnCountOnly answer before anything is saved: a page lost in transit would
otherwise drop a stretch of sidewalk from the overlay and add it to nothing.

An ArcGIS `/query` endpoint has no Last-Modified worth reading -- it is
generated per request -- so what is compared between runs is a **sha256 of
the assembled features**. Identical hash means the City has not touched the
layer, and the existing dated file is kept, which keeps DATA_DATE meaning
"when the City data last changed" rather than "when the build last ran".

Every request is gated on a usable link first (``addressvault.net``, the same
gate the sibling layers pull behind).
"""

import hashlib
import json
import os
import time
from datetime import date

import requests
from addressvault import net

from src import config

# The layer is ~6 pages of ~1 MB each -- a retry costs seconds when the link
# is healthy and buys back a week when it is not.
RETRIES = 3
RETRY_WAIT = 30


def download(force=False):
    """Download the latest Sidewalks GeoJSON.

    Returns (status, filepath) where status is "DOWNLOADED" or "SKIPPED".
    Raises ``net.LinkUnavailable`` when the host has no usable link: nothing was
    attempted, so the caller exits 75 rather than reporting a build failure.
    """
    os.makedirs(config.DATA_DIR, exist_ok=True)

    # Ask before doing anything. Offline or metered, nothing below can work, and
    # finding out here costs a second instead of a 600 s read timeout. Never
    # block waiting for the link: the task carries a hard ExecutionTimeLimit, so
    # a wait would be killed mid-sleep -- retrying is the scheduler's job.
    net.wait_for_link(wait=False)

    print("Querying the Sidewalks FeatureServer ...")
    expected = _count()
    features = []
    while True:
        page = _page(len(features))
        features.extend(page)
        print(f"  {len(features):,} of {expected:,} features")
        if len(page) < config.PAGE_SIZE or len(features) >= expected:
            break
    if len(features) != expected:
        raise RuntimeError(
            f"Paged download returned {len(features):,} features, but the "
            f"service counts {expected:,}. Not saving a partial extract."
        )

    body = json.dumps({"type": "FeatureCollection", "features": features},
                      separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()

    sidecar = _load_sidecar()
    if not force and sidecar and sidecar.get("sha256") == digest:
        existing = os.path.join(config.DATA_DIR, sidecar.get("filename", ""))
        if os.path.isfile(existing):
            print("Unchanged since the last download.")
            return "SKIPPED", existing

    # No server-side edit date exists on this service (editingInfo is null),
    # so the file is dated by the day the change was first seen here.
    filename = f"sidewalks-{date.today().isoformat()}.geojson"
    filepath = os.path.join(config.DATA_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(body)
    print(f"Done: {filepath} ({len(body) // 1024:,} KB)")

    _save_sidecar(digest, len(body), filename)
    return "DOWNLOADED", filepath


def _count():
    doc = _query({"where": "1=1", "returnCountOnly": "true", "f": "json"})
    n = doc.get("count")
    if not isinstance(n, int) or n <= 0:
        raise RuntimeError(f"The service returned no usable count: {doc!r}")
    return n


def _page(offset):
    doc = _query({
        "where": "1=1", "outFields": "*", "outSR": "4326", "f": "geojson",
        "orderByFields": "OBJECTID", "resultOffset": str(offset),
        "resultRecordCount": str(config.PAGE_SIZE),
    })
    _check_page(doc)
    return doc.get("features") or []


def _query(params):
    """GET the layer's /query, retrying a dropped connection. Returns JSON."""
    for attempt in range(RETRIES + 1):
        try:
            resp = requests.get(
                config.QUERY_URL, params=params,
                headers={"User-Agent": config.USER_AGENT},
                timeout=600,
            )
            resp.raise_for_status()
            try:
                return resp.json()
            except ValueError as e:
                raise RuntimeError(f"The service did not return JSON: {e}")
        except requests.RequestException as e:
            if attempt == RETRIES:
                # Out of retries. If the link itself is down then this was never
                # a City failure -- say so, and the run exits 75 instead of
                # logging a build error against a service never reached at all.
                net.wait_for_link(wait=False)
                raise
            print(f"  query failed ({e}); retrying in {RETRY_WAIT}s")
            time.sleep(RETRY_WAIT)


def _check_page(doc):
    """Reject a page that is not a FeatureCollection.

    An ArcGIS service answers an error with HTTP 200 and a JSON body carrying
    an ``error`` object, so raise_for_status proves nothing.
    """
    if "error" in doc:
        raise RuntimeError(f"The service returned an error: {doc['error']}")
    if doc.get("type") != "FeatureCollection":
        raise RuntimeError(f"Expected a FeatureCollection, got {doc.get('type')!r}")


def _load_sidecar():
    if os.path.isfile(config.LAST_DOWNLOAD_PATH):
        with open(config.LAST_DOWNLOAD_PATH, encoding="utf-8") as f:
            return json.load(f)
    return None


def _save_sidecar(digest, size, filename):
    with open(config.LAST_DOWNLOAD_PATH, "w", encoding="utf-8") as f:
        json.dump(
            {"sha256": digest, "bytes": size, "filename": filename}, f, indent=2
        )
