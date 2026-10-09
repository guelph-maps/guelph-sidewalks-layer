// Gap-review page: City raster layer + gap markers, driven by a side table.
//
// The parks layer's page, for a list a hundred times longer: markers draw on
// a canvas (preferCanvas) rather than as thousands of SVG nodes, and the table
// is sorted largest building first, since a missing plaza is worth more than
// a missing garage.

(function () {
  "use strict";

  var COLORS = { missing: "#c2255c", small: "#868e96", plan: "#e8590c" };
  var map;
  var features = [];
  var markers = [];      // CircleMarker per feature, indexed like features
  var highlight = null;  // outline of the currently selected gap
  var selectedRow = null;
  var selectedIndex = null;  // feature index of the current selection

  function init() {
    var el = document.getElementById("gap-map");
    if (!el || typeof L === "undefined" || !el.dataset.geojson) {
      return;
    }
    map = L.map(el, { preferCanvas: true }).setView([43.5448, -80.2482], 13);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">' +
        "OpenStreetMap</a> contributors",
    }).addTo(map);
    if (el.dataset.raster) {
      L.tileLayer(el.dataset.raster, {
        minZoom: 15,
        maxNativeZoom: parseInt(el.dataset.rasterMaxzoom, 10),
        maxZoom: 19,
        opacity: 0.85,
      }).addTo(map);
    }

    fetch(el.dataset.geojson)
      .then(function (r) { return r.json(); })
      .then(render)
      .catch(function (e) {
        el.innerHTML = '<p style="padding:1rem">Could not load gaps.geojson.</p>';
        console.error(e);
      });
  }

  function render(data) {
    features = data.features || [];

    features.forEach(function (f, i) {
      f.properties._i = i;
      var center = centroid(f.geometry);
      f._center = center;
      var m = L.circleMarker(center, {
        radius: 5,
        color: "#fff",
        weight: 1,
        fillColor: COLORS[f.properties.status] || "#888",
        fillOpacity: 0.95,
      });
      m.bindPopup(function () { return popupHtml(f); });
      m.on("click", function () { select(i, false); });
      markers[i] = m;
    });

    buildTable();
    wireControls();
  }

  // Centroid of a (Multi)Polygon's largest outer ring -> [lat, lng]. Taken
  // relative to the ring's first vertex: summed raw at lon/lat, a building's
  // cross-products lose the centroid to float cancellation.
  function centroid(geom) {
    var rings = geom.type === "Polygon"
      ? [geom.coordinates[0]]
      : geom.coordinates.map(function (poly) { return poly[0]; });
    var best = rings[0], bestArea = -1;
    rings.forEach(function (r) {
      var a = Math.abs(ringArea(r));
      if (a > bestArea) { bestArea = a; best = r; }
    });
    var n = best.length, ox = best[0][0], oy = best[0][1];
    var area = 0, cx = 0, cy = 0;
    for (var i = 0; i < n; i++) {
      var x0 = best[i][0] - ox, y0 = best[i][1] - oy;
      var x1 = best[(i + 1) % n][0] - ox, y1 = best[(i + 1) % n][1] - oy;
      var cross = x0 * y1 - x1 * y0;
      area += cross;
      cx += (x0 + x1) * cross;
      cy += (y0 + y1) * cross;
    }
    if (area === 0) {
      var sx = 0, sy = 0;
      best.forEach(function (p) { sx += p[0]; sy += p[1]; });
      return [sy / n, sx / n];
    }
    area *= 0.5;
    return [cy / (6 * area) + oy, cx / (6 * area) + ox];
  }

  function ringArea(r) {
    var a = 0, n = r.length, ox = r[0][0], oy = r[0][1];
    for (var i = 0; i < n; i++) {
      var x0 = r[i][0] - ox, y0 = r[i][1] - oy;
      var x1 = r[(i + 1) % n][0] - ox, y1 = r[(i + 1) % n][1] - oy;
      a += x0 * y1 - x1 * y0;
    }
    return a / 2;
  }

  // Where to send the user for a gap: the openstreetmap.org view and the iD
  // editor, centred on the building. A gap has no OSM object to point at.
  function osmTargets(f) {
    var c = f._center;
    if (!c) { return null; }
    var ll = c[0].toFixed(6) + "/" + c[1].toFixed(6);
    return {
      osm: "https://www.openstreetmap.org/#map=19/" + ll,
      id: "https://www.openstreetmap.org/edit#map=20/" + ll,
    };
  }

  // "OSM · iD" link pair shared by the table and the popups.
  function osmPairHtml(f) {
    var t = osmTargets(f);
    if (!t) { return ""; }
    return '<a href="' + t.osm + '" target="_blank" rel="noopener" ' +
      'title="View on openstreetmap.org (hotkey: O)">OSM</a> &middot; ' +
      '<a href="' + t.id + '" target="_blank" rel="noopener" ' +
      'title="Open in the iD editor (hotkey: I)">iD</a>';
  }

  function label(p) {
    if (p.name) { return p.name; }
    return p.bldid != null ? "Building " + p.bldid : "(no id)";
  }

  function popupHtml(f) {
    var p = f.properties;
    var h = "<strong>" + esc(label(p)) + "</strong><br>" +
      (p.area != null ? p.area.toLocaleString() + " m&sup2; &middot; " : "") +
      esc(p.source || "no source") + " &middot; " + p.status;
    var links = osmPairHtml(f);
    if (links) { h += "<br>" + links; }
    return h;
  }

  function buildTable() {
    var tbody = document.querySelector("#gap-table tbody");
    var rows = features.slice().sort(function (a, b) {
      return (b.properties.area || 0) - (a.properties.area || 0);
    });
    tbody.innerHTML = rows.map(function (f) {
      var p = f.properties;
      var links = osmPairHtml(f);
      var key = [label(p), p.bldid, p.source].join(" ").toLowerCase();
      return '<tr data-i="' + p._i + '" data-status="' + p.status +
        '" data-key="' + esc(key) + '">' +
        "<td>" + esc(label(p)) + "</td>" +
        '<td class="num">' +
        (p.area != null ? p.area.toLocaleString() + " m&sup2;" : "") + "</td>" +
        "<td>" + esc(p.source || "") + "</td>" +
        '<td class="status-' + p.status + '">' + p.status + "</td>" +
        "<td>" + (links ? '<span class="osm-links">' + links + "</span>"
                        : '<span class="osm-blank">&mdash;</span>') +
        "</td></tr>";
    }).join("");

    tbody.addEventListener("click", function (ev) {
      if (ev.target.closest("a")) { return; }  // let OSM links work
      var tr = ev.target.closest("tr");
      if (tr) { select(parseInt(tr.dataset.i, 10), true); }
    });
  }

  // Select a gap: outline it, fit the map, open its popup, mark its row.
  function select(i, fromTable) {
    var f = features[i];
    if (!f) { return; }
    selectedIndex = i;

    if (highlight) { map.removeLayer(highlight); }
    highlight = L.geoJSON(f, {
      style: { color: "#0a7", weight: 3, fill: false },
    }).addTo(map);
    map.fitBounds(highlight.getBounds(), { maxZoom: 19, padding: [60, 60] });
    if (markers[i] && map.hasLayer(markers[i])) { markers[i].openPopup(); }

    if (selectedRow) { selectedRow.classList.remove("selected"); }
    var tr = document.querySelector('#gap-table tr[data-i="' + i + '"]');
    if (tr) {
      tr.classList.add("selected");
      selectedRow = tr;
      if (!fromTable) { tr.scrollIntoView({ block: "center" }); }
    }
  }

  function wireControls() {
    var boxes = {
      missing: document.getElementById("show-missing"),
      small: document.getElementById("show-small"),
      plan: document.getElementById("show-plan"),
    };
    var filter = document.getElementById("filter");

    function statusOn(status) {
      var b = boxes[status];
      return b ? b.checked : true;
    }

    function apply() {
      var q = filter.value.trim().toLowerCase();
      var rows = document.querySelectorAll("#gap-table tbody tr");
      Array.prototype.forEach.call(rows, function (tr) {
        var hit = statusOn(tr.dataset.status) &&
          (!q || tr.dataset.key.indexOf(q) > -1);
        tr.style.display = hit ? "" : "none";
        var m = markers[parseInt(tr.dataset.i, 10)];
        if (hit && !map.hasLayer(m)) { map.addLayer(m); }
        else if (!hit && map.hasLayer(m)) { map.removeLayer(m); }
      });
    }

    Object.keys(boxes).forEach(function (k) {
      if (boxes[k]) { boxes[k].addEventListener("change", apply); }
    });
    filter.addEventListener("input", apply);
    apply();

    // O / I open the selected gap on openstreetmap.org / in the iD editor.
    document.addEventListener("keydown", function (ev) {
      if (ev.ctrlKey || ev.metaKey || ev.altKey) { return; }
      if (/^(input|textarea|select)$/i.test(ev.target.tagName)) { return; }
      var key = ev.key.toLowerCase();
      if (key !== "o" && key !== "i") { return; }
      if (selectedIndex == null) { return; }
      var t = osmTargets(features[selectedIndex]);
      if (!t) { return; }
      ev.preventDefault();
      window.open(key === "o" ? t.osm : t.id, "_blank", "noopener");
    });
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  document.addEventListener("DOMContentLoaded", init);
})();
