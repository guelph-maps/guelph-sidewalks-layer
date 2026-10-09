"""Render the GitHub Pages landing page into the build output."""

import json
import os
import re
import shutil
from datetime import date

from PIL import Image

from src import config, history

# Screenshots shown on the landing page, copied from the project root when
# present (they are added manually after the first publish).
SCREENSHOTS = ["iD.png", "JOSM.png"]
SCREENSHOT_MAX_WIDTH = 1500


def build_site():
    """Render index.html, copy assets and screenshots into build/site/."""
    os.makedirs(config.SITE_DIR, exist_ok=True)

    # data/sidewalks.count.json is written by slim; before the first slim the
    # page still renders, with the figures from the 2026-10-09 download.
    counts = _load_json(config.COUNT_PATH)
    summary = _load_json(config.GAPS_COUNT_PATH)

    build_date = date.today().isoformat()

    with open(os.path.join(config.ASSETS_DIR, "index.html.tmpl"),
              encoding="utf-8") as f:
        html = f.read()

    replacements = {
        "{{PAGES_URL}}": config.PAGES_URL,
        "{{VECTOR_URL}}": f"{config.PAGES_URL}/tiles/vector/{{z}}/{{x}}/{{y}}.pbf",
        "{{RASTER_URL}}": f"{config.PAGES_URL}/tiles/raster/{{z}}/{{x}}/{{y}}.png",
        "{{RASTER_URL_JOSM}}": (
            f"{config.PAGES_URL}/tiles/raster/{{zoom}}/{{x}}/{{y}}.png"
        ),
        "{{VECTOR_URL_JOSM}}": (
            f"{config.PAGES_URL}/tiles/vector/{{zoom}}/{{x}}/{{y}}.pbf"
        ),
        "{{BUILD_DATE}}": build_date,
        "{{DATA_DATE}}": _data_date(build_date),
        "{{LINE_COUNT}}": f"{counts.get('lines', 5245):,}",
        "{{BUILT_KM}}": f"{counts.get('built_km', 715.8):,}",
        # The landing page links to the gap page by its headline figure; with
        # no comparison yet there is no figure, and the link says so instead.
        "{{UNTRACED_KM}}": (f"{summary['untraced_km']:,}"
                            if "untraced_km" in summary else "?"),
        "{{MATCH_M}}": f"{config.MATCH_M:.0f}",
        "{{RASTER_MINZOOM}}": str(min(config.RASTER_ZOOMS)),
        "{{RASTER_MAXZOOM}}": str(max(config.RASTER_ZOOMS)),
        "{{VECTOR_MINZOOM}}": str(config.VECTOR_MINZOOM),
        "{{VECTOR_MAXZOOM}}": str(config.VECTOR_MAXZOOM),
        "{{VECTOR_LAYER_NAME}}": config.VECTOR_LAYER_NAME,
        "{{GITHUB_REPO}}": config.GITHUB_REPO,
        "{{DATASET_PAGE}}": config.DATASET_PAGE,
        "{{LICENSE_URL}}": config.LICENSE_URL,
        "{{LICENSE_NOTICE}}": config.LICENSE_NOTICE,
    }
    for key, value in replacements.items():
        html = html.replace(key, value)

    with open(os.path.join(config.SITE_DIR, "index.html"), "w",
              encoding="utf-8") as f:
        f.write(html)
    for name in ("index.css", "index.js"):
        shutil.copy(
            os.path.join(config.ASSETS_DIR, name),
            os.path.join(config.SITE_DIR, name),
        )
    for name in SCREENSHOTS:
        src = os.path.join(config.PROJECT_DIR, name)
        if os.path.isfile(src):
            _copy_image(src, os.path.join(config.SITE_DIR, name),
                        SCREENSHOT_MAX_WIDTH)
    _build_gaps_page(build_date)
    # .nojekyll stops GitHub Pages running Jekyll over the tile directories.
    open(os.path.join(config.SITE_DIR, ".nojekyll"), "w").close()
    print(f"Site rendered: {config.SITE_DIR}")


def _build_gaps_page(build_date):
    """Render gaps/ (map + table) when a comparison has produced gaps.geojson."""
    if not os.path.isfile(config.GAPS_GEOJSON_PATH):
        print("  (no gaps.geojson -- run 'compare'; skipping gap page)")
        return

    gaps_dir = os.path.join(config.SITE_DIR, "gaps")
    os.makedirs(gaps_dir, exist_ok=True)

    summary = _load_json(config.GAPS_COUNT_PATH)
    untraced = summary.get("untraced_km", 0)
    far = summary.get("far_km", 0)

    with open(os.path.join(config.ASSETS_DIR, "gaps.html.tmpl"),
              encoding="utf-8") as f:
        html = f.read()
    replacements = {
        "{{PAGES_URL}}": config.PAGES_URL,
        "{{BUILD_DATE}}": build_date,
        "{{DATA_DATE}}": _data_date(build_date),
        "{{UNTRACED_KM}}": f"{untraced:,}",
        "{{FAR_KM}}": f"{far:,}",
        "{{REMOTE_KM}}": f"{summary.get('remote_km', 0):,}",
        # The honest range: the untraced figure less the part that sits just
        # beyond the match radius (most likely a City offset, not a missing
        # sidewalk), rounded out to 5 km so the page claims no false precision.
        "{{HONEST_LOW_KM}}": str(int(max(untraced - far, 0) // 5 * 5)),
        "{{HONEST_HIGH_KM}}": str(int(-(-untraced // 5) * 5)),
        "{{STREETS}}": f"{summary.get('streets', 0):,}",
        "{{STREET_MIN_KM}}": f"{config.STREET_MIN_KM:g}",
        "{{MISSING_COUNT}}": f"{summary.get('missing', 0):,}",
        "{{PARTIAL_COUNT}}": f"{summary.get('partial', 0):,}",
        "{{FAR_COUNT}}": f"{summary.get('far', 0):,}",
        "{{SAYS_NO_COUNT}}": f"{summary.get('road_says_no', 0):,}",
        "{{UNTAGGED_COUNT}}": f"{summary.get('untagged', 0):,}",
        "{{ON_ROAD_COUNT}}": f"{summary.get('on_road_tag', 0):,}",
        "{{PROPOSED_COUNT}}": f"{summary.get('proposed', 0):,}",
        "{{NOSURFACE_COUNT}}": f"{summary.get('nosurface', 0):,}",
        # The City's own length (Shape__Length, as the landing page quotes it),
        # not the sampled total, which reprojection moves by ~0.1 km.
        "{{CITY_KM}}": f"{_load_json(config.COUNT_PATH).get('built_km', summary.get('city_km', 0)):,}",
        "{{CITY_COUNT}}": f"{summary.get('city', 0):,}",
        "{{OSM_COUNT}}": f"{summary.get('osm_sidewalk_ways', 0):,}",
        # The matching rule, stated on the page from the values that ran it.
        "{{SAMPLE_STEP_M}}": f"{config.SAMPLE_STEP_M:g}",
        "{{MATCH_M}}": f"{config.MATCH_M:.0f}",
        "{{FAR_M}}": f"{config.FAR_M:.0f}",
        "{{REMOTE_M}}": f"{config.REMOTE_M:.0f}",
        "{{MATCHED_PCT}}": f"{config.MATCHED_SHARE:.0%}",
        "{{PARTIAL_PCT}}": f"{config.PARTIAL_SHARE:.0%}",
        "{{RASTER_MAXZOOM}}": str(max(config.RASTER_ZOOMS)),
        "{{OSM_DATE}}": summary.get("osm_date", build_date),
        "{{OSM_NOTE}}": _osm_note(summary),
        "{{GITHUB_REPO}}": config.GITHUB_REPO,
        # The gap page is published separately from the landing page and is
        # often the only page a mapper opens, so it carries the City licence
        # notice in its own right. Both pages must show it: publishing these
        # tiles is a redistribution, and the notice is the licence condition
        # attached to that, not a footer nicety.
        "{{LICENSE_URL}}": config.LICENSE_URL,
        "{{LICENSE_NOTICE}}": config.LICENSE_NOTICE,
        "{{DATASET_PAGE}}": config.DATASET_PAGE,
    }
    for key, value in replacements.items():
        html = html.replace(key, value)

    with open(os.path.join(gaps_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    shutil.copy(config.GAPS_GEOJSON_PATH, os.path.join(gaps_dir, "gaps.geojson"))
    for name in ("gaps.css", "gaps.js"):
        shutil.copy(os.path.join(config.ASSETS_DIR, name),
                    os.path.join(gaps_dir, name))
    print(f"Gap page rendered: {gaps_dir}")
    _build_history_page(gaps_dir, build_date)


def _build_history_page(gaps_dir, build_date):
    """Render gaps/history.html: every recorded run's gap counts, charted.

    The rows are inlined into the page (the chart needs no second request) and
    published beside it as history.jsonl, which is both the raw data for anyone
    who wants it and what re-seeds a checkout without a history file.
    """
    rows = history.load()
    with open(os.path.join(config.ASSETS_DIR, "history.html.tmpl"),
              encoding="utf-8") as f:
        html = f.read()
    replacements = {
        "{{PAGES_URL}}": config.PAGES_URL,
        "{{BUILD_DATE}}": build_date,
        "{{GITHUB_REPO}}": config.GITHUB_REPO,
        "{{RUN_COUNT}}": f"{len(rows):,}",
        "{{FIRST_DATE}}": rows[0]["date"] if rows else build_date,
        "{{MATCH_M}}": f"{config.MATCH_M:.0f}",
        "{{STREET_MIN_KM}}": f"{config.STREET_MIN_KM:g}",
        "{{HISTORY_JSON}}": json.dumps(rows),
        "{{HISTORY_ROWS}}": "\n".join(_history_row(r) for r in reversed(rows)),
        # Only counts, but counts of the City's sidewalks, and like the gap page
        # this can be the one page a reader opens: it carries the notice too.
        "{{LICENSE_URL}}": config.LICENSE_URL,
        "{{LICENSE_NOTICE}}": config.LICENSE_NOTICE,
    }
    for key, value in replacements.items():
        html = html.replace(key, value)
    with open(os.path.join(gaps_dir, "history.html"), "w", encoding="utf-8") as f:
        f.write(html)
    shutil.copy(os.path.join(config.ASSETS_DIR, "history.js"),
                os.path.join(gaps_dir, "history.js"))
    if os.path.isfile(config.HISTORY_PATH):
        shutil.copy(config.HISTORY_PATH, os.path.join(gaps_dir, "history.jsonl"))
    print(f"History page rendered: {len(rows):,} runs")


def _history_row(r):
    source = "reconstructed" if r.get("source") == "attic" else "live"
    cells = [r["date"]] + [f"{r.get(k, 0):,}" for k in history.FIELDS] + [source]
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


def _osm_note(summary):
    """Warn on the page itself when the OSM half of the diff is not current.

    The gap list is only worth acting on if both sides are fresh, and the OSM
    side is the one that moves daily. A page dated today whose OSM data is two
    weeks old sends a mapper to trace a sidewalk somebody else already traced.
    """
    if not summary.get("osm_stale"):
        return ""
    when = summary.get("osm_date", "an earlier run")
    return (
        '<p class="stale">Overpass could not be reached for this build, so '
        f'these gaps were computed against OSM data from <strong>{when}</strong>. '
        'Anything mapped since then still shows as a gap.</p>'
    )


def _load_json(path):
    """A JSON file's contents, or {} when the step that writes it has not run."""
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _data_date(fallback):
    """Return the City data's date (YYYY-MM-DD) from the download sidecar."""
    if os.path.isfile(config.LAST_DOWNLOAD_PATH):
        with open(config.LAST_DOWNLOAD_PATH, encoding="utf-8") as f:
            sidecar = json.load(f)
        match = re.search(r"\d{4}-\d{2}-\d{2}", sidecar.get("filename", ""))
        if match:
            return match.group(0)
    return fallback


def _copy_image(src, dst, max_width):
    """Copy an image into the site, downscaling it if wider than max_width."""
    with Image.open(src) as img:
        if img.width > max_width:
            height = round(img.height * max_width / img.width)
            resized = img.resize((max_width, height), Image.LANCZOS)
            resized.save(dst, optimize=True)
            print(f"  {os.path.basename(src)}: {img.width}px -> {max_width}px")
        else:
            shutil.copy(src, dst)
