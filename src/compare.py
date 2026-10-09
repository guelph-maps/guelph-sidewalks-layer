"""Compare the City sidewalks against OSM's pedestrian lines.

Pulls every road and every pedestrian way in the Guelph bbox from Overpass,
then walks each City sidewalk in steps of config.SAMPLE_STEP_M and asks what
OSM has beside each sample. The first answer that applies wins:

  sidewalk  a footway=sidewalk (or path=sidewalk) way within MATCH_M
  footway   a plain highway=footway, no footway=* tag, within MATCH_M
  path      any other pedestrian line within MATCH_M (path, cycleway,
            footway=crossing, ...)
  road_tag  the nearest road within ROAD_TAG_M says sidewalk=yes|both|left|right
  says_no   the nearest road within ROAD_TAG_M says sidewalk=no|none
  far       nothing above, but a pedestrian line within FAR_M
  none      nothing at all

The first four are "covered". A City segment's status is then decided by how
much of its length is covered (length-weighted, so a long segment counts for
what it is):

  covered >= MATCHED_SHARE   matched. Listed only when the cover is the wrong
                             kind -- mostly a plain footway ("untagged": tag
                             it footway=sidewalk), mostly a road tag
                             ("on_road_tag", off by default: a valid way to map
                             it), or mostly sidewalk ways with no surface=*
                             ("nosurface", off by default: low value).
  covered >= PARTIAL_SHARE   "partial": part of it is traced.
  less                       a gap, named by what most of its uncovered length
                             has near it: "road_says_no" (the road claims there
                             is no sidewalk), "far" (a line 15-25 m off: check
                             the alignment before adding one), else "missing".

A City line that is not BUILT is "proposed" (off by default) if it is not
matched, and is not listed otherwise. Only City -> OSM is compared: OSM's
campus and private-site sidewalks, which the City does not inventory, are not
reported.

Listed segments carry the name of the OSM road nearest most of their
untraced length (or of their whole length, when all of it is covered), so the page can lead with streets rather than with segments. Matching
runs in UTM 17N metres with shapely's STRtree; a flaky Overpass falls back to
the cached reply, but only after every mirror has been tried several times --
see _load_osm.
"""

import json
import os
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit

import numpy as np
import requests
import shapely
from addressvault import net
from pyproj import Transformer

from src import config, history

STATUSES = ("missing", "partial", "far", "road_says_no", "untagged",
            "on_road_tag", "proposed", "nosurface")
# Statuses that are a gap in the City's built sidewalk, i.e. untraced length a
# mapper can add. These make up a street's total on the page.
GAP_STATUSES = ("missing", "partial", "far", "road_says_no")
COVERED = ("sidewalk", "footway", "path", "road_tag")
SAMPLE_CLASSES = COVERED + ("says_no", "far", "none")

_POSITIVE = {"yes", "both", "left", "right"}
_NEGATIVE = {"no", "none"}

_TO_METRIC = Transformer.from_crs(4326, config.METRIC_EPSG, always_xy=True)


def compare():
    """Build data/gaps.geojson + summary from City vs OSM. Returns the summary."""
    data, osm_date, from_cache = _load_osm()
    osm = _osm_ways(data)
    print(f"OSM ways: {len(osm['sidewalk']):,} sidewalk, "
          f"{len(osm['other']):,} other pedestrian, {len(osm['road']):,} road")
    city = list(_city_features())
    print(f"City lines: {len(city):,}")

    gaps, summary = match(city, osm)
    _write_gaps(gaps)

    summary.update({"osm_sidewalk_ways": len(osm["sidewalk"]),
                    "osm_date": osm_date, "osm_stale": from_cache})
    with open(config.GAPS_COUNT_PATH, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"Untraced: {summary['untraced_km']} km of {summary['city_km']} km "
          f"built ({summary['far_km']} km of it 15-25 m from an OSM line, "
          f"{summary['remote_km']} km with nothing within "
          f"{config.REMOTE_M:.0f} m); {summary['streets']} streets with "
          f">= {config.STREET_MIN_KM} km")
    print("Segments: " + ", ".join(f"{summary[s]:,} {s}" for s in STATUSES))
    if from_cache:
        print(f"NOTE: compared against cached OSM data from {osm_date}, "
              f"not a live Overpass fetch.")
    history.record(summary)
    return summary


# --- OSM source -------------------------------------------------------------

def _overpass_query():
    """The Overpass QL for every road and pedestrian way in the Guelph bbox."""
    s, w, n, e = config.GUELPH_BBOX
    values = "|".join(config.ROAD_HIGHWAYS + config.PATH_HIGHWAYS)
    # Ways only: a sidewalk is never a relation. "tags geom" carries what the
    # match reads -- tags and coordinates -- without the node lists.
    return (
        f"[out:json][timeout:{config.OVERPASS_TIMEOUT - 60}];\n"
        f'way["highway"~"^({values})$"]({s},{w},{n},{e});\n'
        "out tags geom;"
    )


class OsmUnavailable(Exception):
    """Every mirror refused, on a machine that has a working link."""


def _load_osm():
    """Return (data, osm_date, from_cache) for the OSM ways.

    Every mirror is tried, and the whole list is retried: the thing being worked
    around is a loaded instance shedding one request, which clears in seconds.
    Falling back to the cache still beats no gap page, but it is reported rather
    than shrugged off -- ``from_cache`` reaches the published page, and an online
    machine that could not reach any mirror raises so the run can exit non-zero
    and be retried by the scheduler.
    """
    query = _overpass_query()
    print("Querying Overpass for OSM roads and pedestrian ways ...")
    for attempt in range(config.OVERPASS_ROUNDS):
        if attempt:
            print(f"  no mirror answered; retrying the list in "
                  f"{config.OVERPASS_ROUND_WAIT}s "
                  f"({attempt + 1} of {config.OVERPASS_ROUNDS})")
            time.sleep(config.OVERPASS_ROUND_WAIT)
        for url in config.OVERPASS_URLS:
            data = _try_overpass(url, query)
            if data is None:
                continue
            with open(config.OSM_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f)
            osm_base = _osm_base(data)
            _save_fetch(date.today().isoformat(), url, osm_base, data)
            return data, osm_base, False

    # Nothing answered. Ask why before deciding what it means: no link is not a
    # failed build -- the run may as well not have happened -- while a healthy
    # link and no mirror is a real outage worth retrying.
    cached = _load_cache()
    try:
        net.wait_for_link(wait=False)
    except net.LinkUnavailable as e:
        print(f"Warning: no usable link ({e}); Overpass was never reachable.")
        if cached:
            return (*cached, True)
        raise

    if cached:
        print(f"Warning: no Overpass mirror answered in "
              f"{config.OVERPASS_ROUNDS} rounds; falling back to OSM data "
              f"from {cached[1]}.")
        return (*cached, True)
    raise OsmUnavailable(
        f"no Overpass mirror answered in {config.OVERPASS_ROUNDS} rounds "
        f"and there is no cached OSM data"
    )


def _try_overpass(url, query):
    """One attempt at one mirror; None if it failed or answered implausibly."""
    host = urlsplit(url).netloc
    started = time.time()
    try:
        resp = requests.post(
            url,
            data={"data": query},
            headers={"User-Agent": config.USER_AGENT},
            timeout=config.OVERPASS_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        print(f"  {host}: {type(e).__name__}: {str(e).splitlines()[0][:120]}")
        return None

    count = len(data.get("elements", []))
    if count < config.OSM_MIN_ELEMENTS:
        # HTTP 200 is not proof of an answer. A regional instance replies to an
        # out-of-region bbox with a valid, empty element list, and taking that
        # at face value would report every City sidewalk as missing from OSM.
        print(f"  {host}: {count:,} elements, under the "
              f"{config.OSM_MIN_ELEMENTS:,} floor -- ignoring this reply")
        return None
    base = _osm_base_time(data)
    if base is None or datetime.now(timezone.utc) - base > timedelta(days=config.OSM_MAX_AGE_DAYS):
        # Nor is a full reply proof of a current one. A lagging mirror answers
        # the query in full from data months old, and the page would present
        # that as this week's OSM. A reply that does not say how old it is
        # gets no benefit of the doubt.
        print(f"  {host}: OSM data from {_osm_base(data)}, not within "
              f"{config.OSM_MAX_AGE_DAYS} days -- ignoring this reply")
        return None
    print(f"  {host}: {count:,} elements in {time.time() - started:.0f}s, "
          f"OSM base {_osm_base(data)}")
    return data


def _load_cache():
    """(data, osm_date) for the cached Overpass reply, or None."""
    if not os.path.isfile(config.OSM_CACHE_PATH):
        return None
    with open(config.OSM_CACHE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data, _cache_date()


def _osm_base(data):
    """How current the OSM half of the diff actually is.

    Overpass states this about its own reply, as ``osm3s.timestamp_osm_base``:
    the moment of the last minutely diff the answering mirror had applied. That
    is the honest date for the page, and it is not the same as "today" -- a
    mirror can answer instantly with data from last week. Trimmed to the minute
    for display; the raw value goes in the sidecar.
    """
    stamp = ((data.get("osm3s") or {}).get("timestamp_osm_base") or "").strip()
    if len(stamp) >= 16:
        return f"{stamp[:10]} {stamp[11:16]}Z"
    return "unknown"


def _osm_base_time(data):
    """The same timestamp as an aware datetime, or None if absent or garbled."""
    stamp = ((data.get("osm3s") or {}).get("timestamp_osm_base") or "").strip()
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    # A stamp with no zone cannot be aged against UTC; treat it as garbled.
    return when if when.tzinfo else None


def _cache_date():
    """How current the cached reply is: the sidecar, else the file's mtime."""
    if os.path.isfile(config.OSM_FETCH_PATH):
        with open(config.OSM_FETCH_PATH, encoding="utf-8") as f:
            sidecar = json.load(f)
        for key in ("osm_base", "fetched"):
            if sidecar.get(key):
                return sidecar[key]
    stamp = os.path.getmtime(config.OSM_CACHE_PATH)
    return date.fromtimestamp(stamp).isoformat()


def _save_fetch(fetched, url, osm_base, data):
    with open(config.OSM_FETCH_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "fetched": fetched,
            "mirror": urlsplit(url).netloc,
            "osm_base": osm_base,
            "timestamp_osm_base":
                (data.get("osm3s") or {}).get("timestamp_osm_base"),
        }, f, indent=2)


def _osm_ways(data):
    """Split the reply into sidewalk, other pedestrian and road ways.

    Each way is {"id", "tags", "g"} with g a metric LineString. Pedestrian
    areas (area=yes) are dropped: their outline is not a line anyone walks.
    """
    out = {"sidewalk": [], "other": [], "road": []}
    for el in data.get("elements", []):
        if el.get("type") != "way" or not el.get("geometry"):
            continue
        tags = el.get("tags") or {}
        hw = tags.get("highway")
        pts = [(p["lon"], p["lat"]) for p in el["geometry"] if p]
        if len(pts) < 2:
            continue
        way = {"id": el["id"], "tags": tags, "g": _line(pts)}
        if hw in config.ROAD_HIGHWAYS:
            out["road"].append(way)
        elif hw in config.PATH_HIGHWAYS and tags.get("area") != "yes":
            if tags.get("footway") == "sidewalk" or tags.get("path") == "sidewalk":
                out["sidewalk"].append(way)
            else:
                out["other"].append(way)
    return out


def road_sidewalk(tags):
    """What a road's sidewalk tags say: "yes", "no", or None.

    A positive value on any side counts as yes; "separate" says nothing
    either way (the sidewalk should be its own way, found or not).
    """
    vals = [tags.get(k) for k in ("sidewalk", "sidewalk:both",
                                  "sidewalk:left", "sidewalk:right")]
    if any(v in _POSITIVE for v in vals if v):
        return "yes"
    if any(v in _NEGATIVE for v in vals if v):
        return "no"
    return None


def is_plain_footway(tags):
    """A highway=footway with no footway=* subtype: likely an untagged sidewalk."""
    return tags.get("highway") == "footway" and not tags.get("footway")


# --- City source ------------------------------------------------------------

def _city_features():
    with open(config.SLIM_PATH, encoding="utf-8") as f:
        for line in f:
            feat = json.loads(line)
            geom = feat["geometry"]
            parts = (geom["coordinates"] if geom["type"] == "MultiLineString"
                     else [geom["coordinates"]])
            lines = [_line(p) for p in parts if len(p) >= 2]
            if lines:
                yield {"props": feat.get("properties") or {},
                       "geom": geom, "lines": lines}


def _line(pts):
    """A metric shapely LineString from (lon, lat) pairs."""
    xs, ys = _TO_METRIC.transform([p[0] for p in pts], [p[1] for p in pts])
    return shapely.linestrings(np.column_stack([xs, ys]))


# --- Matching ---------------------------------------------------------------

def match(city, osm):
    """Classify every City line. Returns (gap features, summary dict).

    ``city`` items are {"props", "geom", "lines": [metric LineString]};
    ``osm`` is {"sidewalk"|"other"|"road": [{"id", "tags", "g"}]}.
    """
    pts, weights, owner = _sample(city)
    cls, way = classify(pts, osm)

    # Every sample is attributed to the nearest named road, so each listed
    # segment can be filed under a street.
    named = [w for w in osm["road"] if w["tags"].get("name")]
    street_idx = _nearest(pts, [w["g"] for w in named], config.STREET_M)[0]

    per = [defaultdict(float) for _ in city]
    ways = [defaultdict(Counter) for _ in city]
    streets = [Counter() for _ in city]       # all samples
    streets_gap = [Counter() for _ in city]   # untraced samples only
    totals = Counter()
    remote_m = 0.0
    built = np.array([c["props"].get("status") == "built" for c in city])
    near_any = _nearest(pts, [w["g"] for w in osm["sidewalk"] + osm["other"]],
                        config.REMOTE_M)[0]
    for k in range(len(pts)):
        i, c, w = owner[k], cls[k], weights[k]
        per[i][c] += w
        if way[k] is not None:
            ways[i][c][way[k]] += w
        if built[i]:
            totals[c] += w
            if c not in COVERED and near_any[k] < 0:
                remote_m += w
        if street_idx[k] >= 0:
            name = named[street_idx[k]]["tags"]["name"]
            streets[i][name] += w
            if c not in COVERED:
                streets_gap[i][name] += w

    has_surface = {w["id"] for w in osm["sidewalk"] if w["tags"].get("surface")}
    gaps = []
    counts = Counter()
    street_m = Counter()
    for i, c in enumerate(city):
        status = segment_status(per[i], built[i],
                                _surfaced(ways[i]["sidewalk"], has_surface))
        if status is None:
            continue
        counts[status] += 1
        length = sum(per[i].values())
        untraced = length - sum(per[i][k] for k in COVERED)
        # Named for where the gap is when there is one, else for the whole
        # segment: an untagged footway still belongs to a street.
        named_by = streets_gap[i] or streets[i]
        street = named_by.most_common(1)[0][0] if named_by else ""
        if status in GAP_STATUSES:
            street_m[street] += untraced
        gaps.append(_gap_feature(c, status, length, untraced, street,
                                 _blame_way(status, ways[i])))

    city_m = sum(totals.values())
    untraced_m = sum(totals[k] for k in SAMPLE_CLASSES if k not in COVERED)
    summary = {
        "city": int(built.sum()),
        "city_km": _km(city_m),
        "covered_km": _km(sum(totals[k] for k in COVERED)),
        "untraced_km": _km(untraced_m),
        "far_km": _km(totals["far"]),
        "says_no_km": _km(totals["says_no"]),
        "remote_km": _km(remote_m),
        "gap_km": _km(sum(street_m.values())),
        "streets": sum(1 for s, m in street_m.items()
                       if s and m >= config.STREET_MIN_KM * 1000),
        **{s: counts[s] for s in STATUSES},
    }
    return gaps, summary


def _sample(city):
    """Points every ~SAMPLE_STEP_M along each City line.

    Returns (points, length each stands for, owning feature index). Each part
    is cut into equal pieces and sampled at their midpoints, so the weights of
    a line sum to its length.
    """
    pts, weights, owner = [], [], []
    for i, c in enumerate(city):
        for ln in c["lines"]:
            length = ln.length
            if length <= 0:
                continue
            n = max(1, int(round(length / config.SAMPLE_STEP_M)))
            d = (np.arange(n) + 0.5) * (length / n)
            pts.extend(shapely.line_interpolate_point(ln, d))
            weights.extend([length / n] * n)
            owner.extend([i] * n)
    return np.array(pts, dtype=object), np.array(weights), np.array(owner)


def classify(pts, osm):
    """One SAMPLE_CLASSES value per point, plus the OSM way id behind it."""
    sw = osm["sidewalk"]
    ot = osm["other"]
    rd = osm["road"]
    i_sw, _ = _nearest(pts, [w["g"] for w in sw], config.MATCH_M)
    i_ot, _ = _nearest(pts, [w["g"] for w in ot], config.MATCH_M)
    i_rd, _ = _nearest(pts, [w["g"] for w in rd], config.ROAD_TAG_M)
    i_far, _ = _nearest(pts, [w["g"] for w in sw + ot], config.FAR_M)
    rd_says = [road_sidewalk(w["tags"]) for w in rd]

    cls = np.empty(len(pts), dtype=object)
    way = np.full(len(pts), None, dtype=object)
    for k in range(len(pts)):
        if i_sw[k] >= 0:
            cls[k], way[k] = "sidewalk", sw[i_sw[k]]["id"]
        elif i_ot[k] >= 0:
            t = ot[i_ot[k]]["tags"]
            cls[k] = "footway" if is_plain_footway(t) else "path"
            way[k] = ot[i_ot[k]]["id"]
        elif i_rd[k] >= 0 and rd_says[i_rd[k]] == "yes":
            cls[k], way[k] = "road_tag", rd[i_rd[k]]["id"]
        elif i_rd[k] >= 0 and rd_says[i_rd[k]] == "no":
            cls[k], way[k] = "says_no", rd[i_rd[k]]["id"]
        elif i_far[k] >= 0:
            cls[k] = "far"
        else:
            cls[k] = "none"
    return cls, way


def segment_status(lengths, built, surfaced=1.0):
    """The status of one City segment from its per-class lengths (metres).

    ``lengths`` maps SAMPLE_CLASSES to metres; ``surfaced`` is the share of its
    sidewalk-way cover whose way carries surface=*. None means the segment is
    not listed.
    """
    total = sum(lengths.values())
    if total <= 0:
        return None
    covered = sum(lengths.get(k, 0) for k in COVERED)
    share = covered / total
    if not built:
        return None if share >= config.MATCHED_SHARE else "proposed"
    if share >= config.MATCHED_SHARE:
        top = max(COVERED, key=lambda k: lengths.get(k, 0))
        if top == "footway":
            return "untagged"
        if top == "road_tag":
            return "on_road_tag"
        if top == "sidewalk" and surfaced < 0.5:
            return "nosurface"
        return None
    if share >= config.PARTIAL_SHARE:
        return "partial"
    uncovered = total - covered
    if lengths.get("says_no", 0) * 2 >= uncovered:
        return "road_says_no"
    if lengths.get("far", 0) * 2 >= uncovered:
        return "far"
    return "missing"


def _surfaced(counter, has_surface):
    """Share of a segment's sidewalk-way cover whose way has surface=*."""
    total = sum(counter.values())
    if not total:
        return 1.0
    return sum(m for wid, m in counter.items() if wid in has_surface) / total


def _blame_way(status, ways):
    """The OSM way a matched-but-flagged or road_says_no segment points at."""
    cls = {"untagged": "footway", "on_road_tag": "road_tag",
           "nosurface": "sidewalk", "road_says_no": "says_no"}.get(status)
    if cls and ways.get(cls):
        return ways[cls].most_common(1)[0][0]
    return None


def _nearest(pts, geoms, maxd):
    """Index into geoms of each point's nearest within maxd (else -1), + distance."""
    idx = np.full(len(pts), -1)
    dist = np.full(len(pts), np.inf)
    if not geoms or not len(pts):
        return idx, dist
    tree = shapely.STRtree(geoms)
    (pi, ti), dd = tree.query_nearest(pts, max_distance=maxd,
                                      return_distance=True, all_matches=False)
    idx[pi] = ti
    dist[pi] = dd
    return idx, dist


def _gap_feature(c, status, length, untraced, street, way):
    p = c["props"]
    props = {"status": status, "street": street,
             "length": int(round(length)), "untraced": int(round(untraced))}
    for k in ("id", "installed", "surface", "width"):
        if p.get(k) is not None:
            props[k] = p[k]
    if way is not None:
        props["way"] = way
    return {"type": "Feature", "properties": props,
            "geometry": {"type": c["geom"]["type"],
                         "coordinates": _round(c["geom"]["coordinates"])}}


def _write_gaps(gaps):
    with open(config.GAPS_GEOJSON_PATH, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": gaps}, f,
                  separators=(",", ":"))


def _km(m):
    return round(float(m) / 1000, 1)


def _round(c):
    if isinstance(c, (int, float)):
        return round(c, 6)  # ~0.1 m
    return [_round(x) for x in c]
