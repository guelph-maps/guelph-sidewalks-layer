# Guelph Buildings Layer

Turns the City of Guelph [Buildings](https://gismaps.guelph.ca/hosting/rest/services/OpenData/OpenData1/FeatureServer/10)
layer (~38,900 building outlines) into map-tile layers that OpenStreetMap
mappers can add to the **JOSM** and **iD** editors as a reference overlay.

**Live layer and how to add it: https://guelph-maps.github.io/guelph-buildings-layer/**

Part of the [guelph-maps](https://github.com/guelph-maps) organisation, which
indexes every Guelph project. Its siblings are
[guelph-parks-layer](https://github.com/guelph-maps/guelph-parks-layer), the
template this was cloned from, and
[guelph-address-layer](https://github.com/guelph-maps/guelph-address-layer).

## Why this layer

Guelph's building outlines are often better than what OSM has for the city.
The City's base mapping is traced from aerial photography it commissions (the
most recent survey found, May 2020, is at a 10 cm ground resolution), and the
layer says so itself: every row carries a `SOURCE`, and 38,621 of 38,944
(2026-10-08) read `Aerial photography`. Most of the rest were drawn from a submitted site plan
(317 `PLAN`), plus a handful of `ORTHO`, `CAD` and memo-sourced ones.

## What it produces

- **Vector tiles** (MVT), z14–19 &mdash; interactive in iD; click an outline
  to read its `bldid`, `source` and `area` (m²) tags.
- **Raster tiles** (PNG), z15–18 &mdash; translucent fills under solid
  outlines: violet for an imagery-traced building, orange for one drawn from a
  site plan. A tracing backdrop over aerial imagery in JOSM.
- A **landing page** with copy-paste "add this layer" instructions for both
  editors.
- A **gap-review page** ([`/gaps/`](https://guelph-maps.github.io/guelph-buildings-layer/gaps/))
  listing every City building with **no OSM building on it**, largest first,
  on an interactive map and a filterable table, in three categories:
  - **missing** &mdash; add it;
  - **small** &mdash; the same, under 30 m²: sheds and garages, off by default
    so they do not bury the houses and plazas;
  - **site plan** &mdash; the same, for an outline drawn from a plan: check it
    has been built before adding it.

  Its [history page](https://guelph-maps.github.io/guelph-buildings-layer/gaps/history.html)
  charts those counts, one point per weekly run.

All of it is published to GitHub Pages and rebuilt weekly.

## What is kept

**Every polygon.** Unlike the parks layer there is no status or class field to
filter on, and nothing in the layer is anything but a building. The slim step
keeps four properties (see [`src/slim.py`](src/slim.py)): `BLDID` as
`bldid`, `SOURCE` as `source` (as the City spells it — one row reads
`Aerial photgraphy`), `Shape__Area` rounded as `area`, and `NAME` on the four
rows that have one. `GPID`, `MUN` (always `GUELPH`) and `OBJECTID` are dropped.

The City captures down to ~5 m², so sheds and garages are in the layer. Whether
they belong in OSM is the mapper's call; the overlay draws them and the gap page
files them separately.

## How the gap page matches

Overpass returns every `building=*` way and relation in the Guelph bbox
(excluding `building=no`). A City building counts as present in OSM when an OSM
building overlaps it: either one's centroid lies inside the other, or one of
the City outline's corners, pulled 25% of the way toward its centre, lies
inside the OSM one. The corner test is what catches L- and U-shaped buildings,
whose centroid sits outside both outlines. A grid index keeps the ~39,000 ×
~40,000 comparison to a few seconds in pure Python
([`src/compare.py`](src/compare.py)).

It runs one way only. An OSM building with no City building under it is not
reported: OSM holds outbuildings, demolitions and new builds the City lags
on, and telling those apart needs imagery, not a diff.

A test run on 2026-10-08, against a partial and stale OSM sample (Overpass
could not be reached from the build machine), left 1,626 of 24,605 City
buildings in the sampled area unmatched; median size 47 m². Of the six
largest, five were absent from OSM when checked by hand, and the sixth had been
mapped after the sample was taken.

The Overpass handling (mirror list, retries, refusing stale or implausibly
small replies, the cache fallback, exit code 70) is the parks layer's,
unchanged; its README explains each piece. `OSM_MIN_ELEMENTS` in
[`src/config.py`](src/config.py) is 25,000, about half the first live reply
(49,695 elements on 2026-10-09).

The parks layer can reconstruct its gap history from Overpass attic queries;
this one has no `backfill` command. An attic query for every building in the
city is a heavy ask of a shared instance for each week reconstructed, so the
history starts with the first weekly build.

## Setup

1. Install Python dependencies:
   ```
   pip install -r requirements.txt
   ```
   `addressvault` is the local link-gate package the sibling projects share; it
   is not on PyPI.
2. On Windows, set up WSL2 + tippecanoe once &mdash; see
   [wsl-setup.md](wsl-setup.md). Elsewhere, install tippecanoe (2.49+) and the
   vector step runs it directly.

## Usage

```
python run.py download   # fetch the latest Buildings GeoJSON (smart-cached)
python run.py slim       # reduce to a slim GeoJSONL
python run.py compare    # diff City outlines against OSM -> data/gaps.geojson
python run.py vector     # build vector (MVT) tiles via tippecanoe
python run.py raster     # build outline raster (PNG) tiles
python run.py site       # render the landing page (+ the gap-review page)
python run.py publish    # force-push the site to the gh-pages branch

python run.py build      # download + slim + compare + vector + raster + site
python run.py update     # build + publish  (the scheduled entry point)
```

Build output lands in `build/site/`; that directory is what gets published.
A full build on 2026-10-08: 23,769 vector tiles in ~10 s, 7,151 raster tiles
in ~90 s, 142 MB of site.

## Scheduling (Windows)

```powershell
.\schedule-add.ps1       # registers a weekly task "kk-GuelphBuildingsLayer", Wednesdays 16:30
.\schedule-remove.ps1    # unregisters it
```

## Tests

```
pytest
```

## Licence / attribution

Building data is &copy; City of Guelph, published under the
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
bulk-import it. Copying outlines one at a time into OSM, by a mapper checking
imagery, is ordinary mapping with an approved source. A scripted import of the
missing buildings would be an import, and would need its own proposal under
the [import guidelines](https://wiki.openstreetmap.org/wiki/Import/Guidelines).
