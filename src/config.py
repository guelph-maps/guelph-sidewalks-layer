"""Configuration constants for the Guelph sidewalks tile layer build.

Single source of truth. No logic here.

Sibling of guelph-buildings-layer, and cloned from it: the same pipeline
(download -> slim -> compare -> vector + raster -> site -> publish), pointed at
the City of Guelph Sidewalks layer. What changes is the geometry: these are
lines, the first line layer in the family, so the comparison samples each City
sidewalk every few metres and asks how much of it has an OSM line beside it,
rather than testing one polygon against another.
"""

import os

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_DIR, "data")
BUILD_DIR = os.path.join(PROJECT_DIR, "build")
SITE_DIR = os.path.join(BUILD_DIR, "site")
ASSETS_DIR = os.path.join(PROJECT_DIR, "assets")
LOGS_DIR = os.path.join(PROJECT_DIR, "logs")

MBTILES_PATH = os.path.join(BUILD_DIR, "sidewalks.mbtiles")
SLIM_PATH = os.path.join(DATA_DIR, "sidewalks-slim.geojsonl")
COUNT_PATH = os.path.join(DATA_DIR, "sidewalks.count.json")
LAST_DOWNLOAD_PATH = os.path.join(DATA_DIR, ".last-download.json")

VECTOR_TILE_DIR = os.path.join(SITE_DIR, "tiles", "vector")
RASTER_TILE_DIR = os.path.join(SITE_DIR, "tiles", "raster")

# Data source: City of Guelph Sidewalks (OpenData2 layer 0), ~5,245 lines,
# ~720 km. maxRecordCount is 1,000, so unlike the buildings layer the download
# pages through it. The service stores geometry in EPSG:26917, so outSR=4326
# is not optional -- without it every downstream lon/lat is UTM metres.
FEATURE_SERVER = (
    "https://gismaps.guelph.ca/hosting/rest/services/OpenData/OpenData2"
    "/FeatureServer/0"
)
QUERY_URL = f"{FEATURE_SERVER}/query"
PAGE_SIZE = 1000
# The catalogue entry on explore.guelph.ca has no stable item page for this
# layer, so the FeatureServer URL is what a reader is pointed at.
DATASET_PAGE = FEATURE_SERVER
# This layer REDISTRIBUTES City data as published tiles, and the licence
# attaches a required attribution to that. It is carried on every page. See
# the README section "Licence"; it is a condition, not a courtesy.
LICENSE_URL = "https://gismaps.guelph.ca/Images/OpenDataLicenceVersion2.pdf"
LICENSE_NOTICE = (
    "Contains information licensed under the Open Government Licence "
    "– City of Guelph."
)
# Plain-ASCII attribution embedded in tile metadata (it is passed through a
# shell, where the en dash in LICENSE_NOTICE is not safe).
ATTRIBUTION = (
    "Contains information licensed under the Open Government Licence "
    "- City of Guelph"
)

# GitHub Pages target. PAGES_URL carries NO trailing slash: every tile URL on
# the site is built as f"{PAGES_URL}/tiles/...", and that string is the one
# thing on the page a reader copies verbatim into their editor.
GITHUB_REPO = "guelph-maps/guelph-sidewalks-layer"
PAGES_URL = "https://guelph-maps.github.io/guelph-sidewalks-layer"

# WSL distro that has tippecanoe installed (see wsl-setup.md). Used on Windows
# only; elsewhere tippecanoe is run directly.
WSL_DISTRO = "Ubuntu"

# Vector tiles. iD requests tiles at (map zoom - 1) and does NOT overzoom, so
# tiles must be generated natively through the zooms used for mapping.
VECTOR_MINZOOM = 14
VECTOR_MAXZOOM = 19
VECTOR_LAYER_NAME = "sidewalks"

# Raster tiles. Editors overzoom the top zoom further. z18 is where a sidewalk
# and its curb are far enough apart on imagery to trace one and not the other.
RASTER_ZOOMS = [15, 16, 17, 18]

# Source property keys read from the Sidewalks GeoJSON.
ID_KEY = "SIDEWALKID"        # the City's sidewalk id
STATUS_KEY = "STATUS"        # BUILT / PROPOSED / REQUIRED
SURFACE_KEY = "SURFTYPE"     # CONCRETE on ~99% of the built length
WIDTH_KEY = "WIDTH"          # e.g. "1.52m (5'-0\")"
INSTALL_KEY = "INSTALLDATE"  # epoch milliseconds, often null
ACCURACY_KEY = "DATACAPTUREACCURACY"  # "1 TO 5 METRES" on nearly every row

# Only BUILT sidewalks are gaps in their own right. PROPOSED and REQUIRED rows
# (14 on 2026-10-09) are drawn dashed on the overlay and, when OSM has nothing
# there, filed under "proposed" on the gap page, off by default: a mapper
# should not trace a sidewalk that may not have been poured.
BUILT_STATUS = "BUILT"

# --- OSM comparison (gap-review page) ---
# Every City sidewalk is sampled every SAMPLE_STEP_M metres. A sample is
# covered when, within MATCH_M, OSM has a footway=sidewalk way, else any other
# pedestrian line (a plain footway, path, cycleway, ...), or, within
# ROAD_TAG_M, a road carrying a positive sidewalk=* tag. compare.py turns the
# samples into one status per City segment; its docstring has the rule.
#
# 15 m is the recalibration's headline radius (2026-10-09): the City states
# 1-5 m accuracy on 5,241 of its 5,245 rows, and a 15 m radius absorbs that
# and an OSM sidewalk traced a few metres off. FAR_M is the band beyond it
# where a City offset, not a missing way, is the likelier explanation.
SAMPLE_STEP_M = 5.0
MATCH_M = 15.0
FAR_M = 25.0
ROAD_TAG_M = 20.0
# Beyond this, nothing in OSM is anywhere near: reported as its own figure,
# the part of the gap no alignment argument can explain away.
REMOTE_M = 50.0
# A segment is matched at >=70% covered, partial at 20-70%, and the rest is a
# gap -- "missing", "far" or "road_says_no" by what the uncovered part has
# near it.
MATCHED_SHARE = 0.70
PARTIAL_SHARE = 0.20
# Gap segments are attributed to the nearest named OSM road within this far,
# so the page can lead with streets. The recalibration found every metre of
# the missing length within 25 m of a mapped road.
STREET_M = 40.0
# A street is listed in the headline count when at least this much of its
# City sidewalk has no OSM line within MATCH_M.
STREET_MIN_KM = 0.2

# OSM highway values read as roads (a positive or negative sidewalk=* on one
# of these speaks for the sidewalk beside it) and as pedestrian lines.
ROAD_HIGHWAYS = (
    "motorway", "trunk", "primary", "secondary", "tertiary", "unclassified",
    "residential", "living_street", "service", "road", "busway",
    "motorway_link", "trunk_link", "primary_link", "secondary_link",
    "tertiary_link",
)
PATH_HIGHWAYS = (
    "footway", "path", "cycleway", "pedestrian", "steps", "track",
    "bridleway", "corridor",
)

# The mirror list is the parks layer's, for the parks layer's reasons:
# overpass.kumi.systems and overpass.private.coffee have both been caught
# serving months-stale data, which looks exactly like success (private.coffee
# answered the recalibration with a 2026-05-31 base). OSM_MAX_AGE_DAYS refuses
# such a reply from any mirror: the gap page asserts timestamp_osm_base, not
# merely that the request came back.
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)
OVERPASS_TIMEOUT = 600
OVERPASS_ROUNDS = 3
OVERPASS_ROUND_WAIT = 60
# Floor on a reply's element count. A regional instance answers an
# out-of-region bbox with a valid EMPTY result, and taking that at face value
# reports every City sidewalk as missing from OSM. About half the first live
# reply: 30,494 ways from overpass-api.de on 2026-10-09.
OSM_MIN_ELEMENTS = 15000
OSM_MAX_AGE_DAYS = 3
# Overpass rejects the default requests User-Agent (HTTP 406); identify the tool.
USER_AGENT = "guelph-sidewalks-layer/0.1 (toronto@comentality.com)"
# Guelph bounding box (S, W, N, E), verbatim from guelph-beholder's config.toml
# -- a touch larger than the city. OSM ways outside the city simply match
# nothing, so a loose box is harmless.
GUELPH_BBOX = (43.46, -80.34, 43.60, -80.14)
# Guelph city centre; where the preview and gap maps open.
GUELPH_CENTER = (43.5448, -80.2482)
# UTM zone 17N: the metric frame the matching runs in.
METRIC_EPSG = 32617
OSM_CACHE_PATH = os.path.join(DATA_DIR, "osm-ways.json")
# When the cached Overpass reply was fetched, from where, and the OSM database
# timestamp the reply declares about itself.
OSM_FETCH_PATH = os.path.join(DATA_DIR, ".osm-fetch.json")
GAPS_GEOJSON_PATH = os.path.join(DATA_DIR, "gaps.geojson")
GAPS_COUNT_PATH = os.path.join(DATA_DIR, "gaps.count.json")

# --- Gap history (trend page) ---
# One JSON line per live compare run. It lives in data/ like the rest of the
# observer's state; its published copy re-seeds a checkout that has none.
HISTORY_PATH = os.path.join(DATA_DIR, "history.jsonl")
HISTORY_URL = f"{PAGES_URL}/gaps/history.jsonl"
