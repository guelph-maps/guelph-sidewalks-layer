# Guelph Sidewalks Layer

Turns the City of Guelph [Sidewalks](https://gismaps.guelph.ca/hosting/rest/services/OpenData/OpenData2/FeatureServer/0)
layer (~5,245 lines, 716 km built) into map-tile layers that OpenStreetMap
mappers can add to the **JOSM** and **iD** editors as a reference overlay, and
a weekly page listing the City sidewalks OSM has no line for, street by
street.

**Live layer and how to add it: https://guelph-maps.github.io/guelph-sidewalks-layer/**

Part of the [guelph-maps](https://github.com/guelph-maps) organisation, which
indexes every Guelph project. Cloned from
[guelph-buildings-layer](https://github.com/guelph-maps/guelph-buildings-layer),
itself cloned from [guelph-parks-layer](https://github.com/guelph-maps/guelph-parks-layer).
This is the family's first **line** layer: the others match points or
polygons.

## Why this layer

An audit of the City's open data for OSM rated sidewalks low because "OSM has
92%". That figure divided OSM's total sidewalk length by the City's (663 / 716
km). It never asked whether they are the same sidewalks, and 42 km of OSM's
are on campus and private sites the City does not inventory, which pads the
ratio. Measured by location, on the first run here (2026-10-09):

- **70.5 km** of the City's 715.8 km of built sidewalk has no OSM pedestrian
  line within 15 m and no road tag claiming one; **50.7 km** has nothing
  within 50 m.
- It runs along streets whose roads are mapped. 100 streets have at least
  0.2 km each, and the worst cluster by neighbourhood: Kortright Hills
  (Deerpath Dr 3.2 km, Woodland Glen Dr 2.2, Pinnacle Cres 1.8), then West
  Acres Dr 2.7 and Freshmeadow Way 1.9.
- The City states 1–5 m accuracy on 5,241 of its 5,245 rows, and 8.8 km of the
  untraced length sits 15–25 m from an OSM line, where an offset is a likelier
  story than a missing way. So the honest headline is **~60–75 km**.

Guelph already maps sidewalks as separate ways (4,512 `footway=sidewalk` ways,
against 237 roads with a positive `sidewalk=*`), so tracing them follows the
local convention; there is no tagging question to settle first.

## What it produces

- **Vector tiles** (MVT), z14–19 &mdash; interactive in iD; click a line to
  read its `id` (the City's SIDEWALKID), `status`, `surface`, `width` (m),
  `installed` (year, on about a third of rows) and `length` (m).
- **Raster tiles** (PNG), z15–18 &mdash; cyan lines for built sidewalks,
  orange for the City's proposed ones. A tracing backdrop over aerial imagery
  in JOSM.
- A **landing page** with copy-paste "add this layer" instructions for both
  editors.
- A **gap-review page** ([`/gaps/`](https://guelph-maps.github.io/guelph-sidewalks-layer/gaps/))
  that leads with **streets**, by untraced length, and opens each into its
  segments on an interactive map. Segment statuses:
  - **missing** &mdash; under 20% covered: add it;
  - **partial** &mdash; 20–70% covered: finish it;
  - **far** &mdash; an OSM line 15–25 m away: check the alignment before
    adding anything;
  - **road_says_no** &mdash; the OSM road says `sidewalk=no` where the City
    has one (2.7 km, most of it on Whitelaw Rd);
  - **untagged** &mdash; covered by a plain `highway=footway`: tag it
    `footway=sidewalk`;
  - off by default: **on_road_tag** (mapped as `sidewalk=*` on the road, a
    valid way to map it), **proposed** (City status PROPOSED, may not be
    poured), **nosurface** (a matched sidewalk way with no `surface=*`; the
    City's are 99% concrete, so low value).

  Its [history page](https://guelph-maps.github.io/guelph-sidewalks-layer/gaps/history.html)
  charts the untraced length and the counts, one point per weekly run.

All of it is published to GitHub Pages and rebuilt weekly.

## What is kept

**Every line**, built or proposed. The slim step keeps six properties (see
[`src/slim.py`](src/slim.py)): `SIDEWALKID` as `id`, `STATUS` as `status`
(`built`, or `proposed` for PROPOSED and REQUIRED), `SURFTYPE` lower-cased as
`surface`, the metres out of `WIDTH`, the year of `INSTALLDATE`, and
`Shape__Length` rounded. Condition, inspection and plow-route fields are
dropped: they are maintenance records, not anything a mapper can check.

## How the gap page matches

Overpass returns every road and every pedestrian way in the Guelph bbox (one
query, `out tags geom`, ~30,000 ways). Each City sidewalk is cut into pieces
of ~5 m and each piece's midpoint asks, in this order:

1. a `footway=sidewalk` way within 15 m &mdash; covered;
2. any other pedestrian line within 15 m (a plain footway, path, cycleway, a
   crossing) &mdash; covered;
3. the nearest road within 20 m with a positive `sidewalk=*` &mdash; covered;
4. that road saying `sidewalk=no` &mdash; a contradiction;
5. a pedestrian line 15–25 m away &mdash; far;
6. nothing.

A segment is matched at ≥70% covered (by length), partial at 20–70%, and a
gap below that, named by what most of its uncovered length has near it. The
full rule is in [`src/compare.py`](src/compare.py)'s docstring. Matching runs
in UTM 17N metres with shapely's STRtree; a full compare takes ~10 s.

Each gap segment is named after the OSM road nearest most of its untraced
length, within 40 m. The audit named them after the City's street centrelines
instead, which is why it counted 97 streets with ≥0.2 km and this counts 100;
the ranking is the same.

The first run reproduced the audit's recalibration exactly: 362 segments under
20% covered (318 missing, 23 far, 21 road_says_no), and 67.8 km untraced once
`road_says_no` is set aside (the recalibration: 67.7).

It runs one way only. An OSM sidewalk with no City sidewalk beside it is not
reported: campus, private sites and plazas are in OSM and not in the City's
inventory, and that is not a gap.

The Overpass handling (mirror list, retries, refusing stale or implausibly
small replies, the cache fallback, exit code 70) is the parks layer's; its
README explains each piece. One tightening: a reply that carries no
`timestamp_osm_base` at all, or one with no time zone, is refused too, rather than dated today.
`OSM_MIN_ELEMENTS` in [`src/config.py`](src/config.py) is 15,000, about half
the first live reply (30,494 ways on 2026-10-09).

The City's service caps a reply at 1,000 rows, so unlike its siblings the
download pages through the layer, and refuses to save if the total differs
from the service's own count.

There is no `backfill` command; the history starts with the first weekly
build.

## Setup

1. Install Python dependencies:
   ```
   pip install -r requirements.txt
   ```
   `addressvault` is the local link-gate package the sibling projects share; it
   is not on PyPI. Unlike its siblings this project needs **shapely 2, numpy
   and pyproj**: matching ~143,000 five-metre samples against ~30,000 OSM ways
   wants a spatial index and a metric projection, which the polygon siblings
   could hand-roll and a line matcher cannot cheaply.
2. On Windows, set up WSL2 + tippecanoe once &mdash; see
   [wsl-setup.md](wsl-setup.md). Elsewhere, install tippecanoe (2.49+) and the
   vector step runs it directly.

## Usage

```
python run.py download   # fetch the Sidewalks layer, page by page (smart-cached)
python run.py slim       # reduce to a slim GeoJSONL
python run.py compare    # diff City sidewalks against OSM -> data/gaps.geojson
python run.py vector     # build vector (MVT) tiles via tippecanoe
python run.py raster     # build line raster (PNG) tiles
python run.py site       # render the landing page (+ the gap-review page)
python run.py publish    # force-push the site to the gh-pages branch

python run.py build      # download + slim + compare + vector + raster + site
python run.py update     # build + publish  (the scheduled entry point)
```

Build output lands in `build/site/`; that directory is what gets published.
A full build on 2026-10-09: 15,394 vector tiles in ~2 min, 4,964 raster tiles
in ~20 s.

## Scheduling (Windows)

```powershell
.\schedule-add.ps1       # registers a weekly task "kk-GuelphSidewalksLayer", Saturdays 16:30
.\schedule-remove.ps1    # unregisters it
```

## Tests

```
pytest
```

## Licence / attribution

Sidewalk data is &copy; City of Guelph, published under the
[Open Government Licence &ndash; City of Guelph](https://gismaps.guelph.ca/Images/OpenDataLicenceVersion2.pdf).
The OSMF Licence Working Group approved that licence as `compatible` on
2024-09-09.

**This project redistributes City data** — as published tiles on a public URL
— and the licence attaches a required attribution to that. The landing page,
the gap page and the history page all carry, visibly:

> Contains information licensed under the Open Government Licence – City of Guelph.

linked to the licence text. The same attribution is embedded in the vector tile
metadata. This is a condition of use, not a courtesy: if you fork this, the
notice travels with it.

Being able to trace from this layer is not the same as being cleared to
bulk-import it. Tracing sidewalks one at a time into OSM, by a mapper checking
imagery, is ordinary mapping with an approved source. A scripted import of the
missing segments would be an import, and would need its own proposal under the
[import guidelines](https://wiki.openstreetmap.org/wiki/Import/Guidelines).
