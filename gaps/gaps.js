// Gap-review page: City raster layer + gap segments, driven by a street table.
//
// The buildings layer's page, for lines. Two things change. The segments are
// drawn as polylines coloured by status (on a canvas, preferCanvas, since the
// off-by-default statuses add a few thousand of them). And the table leads
// with streets, not segments: a mapper walks or traces a street at a time,
// and 3,000 rows of 80 m segments say nothing about where the work is. A
// street's row expands into its segment rows.

(function () {
  "use strict";

  // Order is the checkbox / count-chip order. Default-on statuses first.
  var STATUSES = ["missing", "partial", "far", "road_says_no", "untagged",
                  "on_road_tag", "proposed", "nosurface"];
  var COLORS = {
    missing: "#c2255c", partial: "#f08c00", far: "#7048e8",
    road_says_no: "#e03131", untagged: "#1c7ed6", on_road_tag: "#868e96",
    proposed: "#e8590c", nosurface: "#ced4da",
  };
  var LABELS = {
    missing: "missing", partial: "partial", far: "far",
    road_says_no: "road says no", untagged: "untagged", on_road_tag: "on road tag",
    proposed: "proposed", nosurface: "no surface",
  };
  // A street's total counts only these: untraced length a mapper can add.
  // "untagged" etc. are covered already, so their `untraced` is not work.
  var GAP = { missing: 1, partial: 1, far: 1, road_says_no: 1 };
  var NONAME = "(no named road nearby)";
  var WEIGHT = 4, SELECTED_WEIGHT = 8;

  var map;
  var features = [];
  var lines = [];        // L.Polyline per feature, indexed like features
  var streets = [];      // {name, km, counts, idx: [feature index...]}
  var streetOf = [];     // feature index -> street index
  var expanded = {};     // street index -> true while its segments are listed
  var hits = [];         // feature index -> passes the status + text filter
  var selectedRow = null, selectedStreetRow = null;
  var selectedIndex = null;  // feature index of the current selection
  var apply = function () {};

  function init() {
    var el = document.getElementById("gap-map");
    if (!el || typeof L === "undefined" || !el.dataset.geojson) {
      return;
    }
    // A wider hit tolerance than the canvas default: a 4 px line is a hard
    // target, and most clicks here are meant for one.
    map = L.map(el, { preferCanvas: true, renderer: L.canvas({ tolerance: 6 }) })
      .setView([43.5448, -80.2482], 13);
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
      var g = f.geometry;
      f._mid = midpoint(g);
      var latlngs = L.GeoJSON.coordsToLatLngs(g.coordinates,
        g.type === "MultiLineString" ? 1 : 0);
      var line = L.polyline(latlngs, {
        color: COLORS[f.properties.status] || "#888",
        weight: WEIGHT,
        opacity: 0.9,
      });
      line.bindPopup(function () { return popupHtml(f); });
      line.on("click", function (ev) {
        L.DomEvent.stopPropagation(ev);
        select(i, false, ev.latlng);
      });
      lines[i] = line;
    });

    groupStreets();
    buildTable();
    wireControls();
  }

  // One group per OSM road name. Its km is the untraced length of its gap
  // segments only; the per-status counts cover every listed segment, so a
  // street that only has off-by-default segments still has a row to show
  // when those statuses are switched on.
  function groupStreets() {
    var byName = {};
    features.forEach(function (f, i) {
      var p = f.properties;
      var name = p.street || NONAME;
      var s = byName[name];
      if (!s) {
        s = byName[name] = { name: name, km: 0, counts: {}, idx: [] };
        streets.push(s);
      }
      s.idx.push(i);
      s.counts[p.status] = (s.counts[p.status] || 0) + 1;
      if (GAP[p.status]) { s.km += (p.untraced || 0) / 1000; }
    });
    streets.sort(function (a, b) {
      // The unnamed bucket is a remainder, not a street: always last.
      if ((a.name === NONAME) !== (b.name === NONAME)) {
        return a.name === NONAME ? 1 : -1;
      }
      return (b.km - a.km) || (b.idx.length - a.idx.length) ||
        (a.name < b.name ? -1 : a.name > b.name ? 1 : 0);
    });
    streets.forEach(function (s, si) {
      s.idx.sort(function (a, b) {
        var pa = features[a].properties, pb = features[b].properties;
        return ((pb.untraced || 0) - (pa.untraced || 0)) ||
          ((pb.length || 0) - (pa.length || 0));
      });
      s.idx.forEach(function (i) { streetOf[i] = si; });
    });
  }

  // Point halfway along the longest part, as [lat, lng]: where O / I and the
  // link pair send the mapper. A vertex average lands off a curved sidewalk.
  function midpoint(geom) {
    var parts = geom.type === "MultiLineString"
      ? geom.coordinates : [geom.coordinates];
    var best = null, bestLen = -1;
    parts.forEach(function (pts) {
      var len = partLength(pts);
      if (len > bestLen) { bestLen = len; best = pts; }
    });
    if (!best || !best.length) { return null; }
    var half = bestLen / 2, walked = 0;
    for (var k = 1; k < best.length; k++) {
      var d = segLength(best[k - 1], best[k]);
      if (walked + d >= half && d > 0) {
        var t = (half - walked) / d;
        return [best[k - 1][1] + t * (best[k][1] - best[k - 1][1]),
                best[k - 1][0] + t * (best[k][0] - best[k - 1][0])];
      }
      walked += d;
    }
    return [best[0][1], best[0][0]];
  }

  function partLength(pts) {
    var len = 0;
    for (var k = 1; k < pts.length; k++) { len += segLength(pts[k - 1], pts[k]); }
    return len;
  }

  // Equirectangular length in degrees of latitude: only ever compared with
  // itself, so the unit does not matter, but longitude must be shrunk by
  // cos(lat) or an east-west part reads 1.4x longer than it is at Guelph.
  function segLength(a, b) {
    var dx = (b[0] - a[0]) * Math.cos(a[1] * Math.PI / 180);
    var dy = b[1] - a[1];
    return Math.sqrt(dx * dx + dy * dy);
  }

  // Where to send the user for a segment: the openstreetmap.org view and the
  // iD editor, centred on its midpoint.
  function osmTargets(f) {
    var c = f._mid;
    if (!c) { return null; }
    var ll = c[0].toFixed(6) + "/" + c[1].toFixed(6);
    return {
      osm: "https://www.openstreetmap.org/#map=19/" + ll,
      id: "https://www.openstreetmap.org/edit#map=20/" + ll,
    };
  }

  // "OSM · iD" link pair shared by the table and the popups, plus a link to
  // the OSM way the status is about, when there is one: the footway to
  // retag, the road saying sidewalk=no, the road carrying sidewalk=*.
  function osmPairHtml(f) {
    var t = osmTargets(f);
    var h = "";
    if (t) {
      h = '<a href="' + t.osm + '" target="_blank" rel="noopener" ' +
        'title="View on openstreetmap.org (hotkey: O)">OSM</a> &middot; ' +
        '<a href="' + t.id + '" target="_blank" rel="noopener" ' +
        'title="Open in the iD editor (hotkey: I)">iD</a>';
    }
    var way = f.properties.way;
    if (way != null) {
      h += (h ? " &middot; " : "") +
        '<a href="https://www.openstreetmap.org/way/' + encodeURIComponent(way) +
        '" target="_blank" rel="noopener" title="' + esc(wayTitle(f)) +
        '">OSM way</a>';
    }
    return h;
  }

  function wayTitle(f) {
    switch (f.properties.status) {
      case "untagged": return "The plain footway to tag footway=sidewalk";
      case "road_says_no": return "The road tagged sidewalk=no";
      case "on_road_tag": return "The road carrying sidewalk=*";
      case "nosurface": return "The sidewalk way with no surface=*";
      default: return "The OSM way this status is about";
    }
  }

  function segLabel(p) {
    return p.id != null ? "#" + p.id : "(no id)";
  }

  function popupHtml(f) {
    var p = f.properties;
    var bits = [];
    if (p.length != null) { bits.push(p.length.toLocaleString() + " m"); }
    if (GAP[p.status] && p.untraced) {
      bits.push(p.untraced.toLocaleString() + " m untraced");
    }
    var more = [];
    if (p.surface) { more.push(p.surface); }
    if (p.width != null) { more.push(p.width + " m wide"); }
    if (p.installed != null) { more.push("installed " + p.installed); }
    var h = "<strong>" + esc(p.street || NONAME) + "</strong><br>" +
      "Sidewalk " + esc(segLabel(p)) + " &middot; " +
      '<span class="status-' + p.status + '">' + LABELS[p.status] + "</span>" +
      (bits.length ? "<br>" + bits.join(" &middot; ") : "") +
      (more.length ? "<br>" + esc(more.join(" · ")) : "");
    var links = osmPairHtml(f);
    if (links) { h += "<br>" + links; }
    return h;
  }

  function fmtKm(km) {
    return km >= 0.05 ? km.toFixed(1) + " km" : "&mdash;";
  }

  function chipsHtml(counts) {
    return STATUSES.filter(function (s) { return counts[s]; }).map(function (s) {
      return '<span class="chip" data-status="' + s + '" title="' +
        counts[s] + " " + LABELS[s] + '"><i style="background:' + COLORS[s] +
        '"></i>' + counts[s] + "</span>";
    }).join("");
  }

  function buildTable() {
    var tbody = document.querySelector("#gap-table tbody");
    var html = [];
    streets.forEach(function (s, si) {
      html.push('<tr class="street" data-s="' + si + '">' +
        '<td class="col-name"><span class="caret"></span>' + esc(s.name) +
        ' <span class="seg-n"></span></td>' +
        '<td class="num">' + fmtKm(s.km) + "</td>" +
        '<td class="chips">' + chipsHtml(s.counts) + "</td><td></td></tr>");
      s.idx.forEach(function (i) {
        var p = features[i].properties;
        var links = osmPairHtml(features[i]);
        html.push('<tr class="seg" data-i="' + i + '" data-status="' +
          p.status + '">' +
          '<td class="col-name">' + esc(segLabel(p)) +
          (p.length != null ? ' <span class="len">&middot; ' +
            p.length.toLocaleString() + " m</span>" : "") + "</td>" +
          '<td class="num">' + (p.untraced ? p.untraced.toLocaleString() +
            " m" : "&mdash;") + "</td>" +
          '<td class="status-' + p.status + '">' + LABELS[p.status] + "</td>" +
          "<td>" + (links ? '<span class="osm-links">' + links + "</span>"
                          : '<span class="osm-blank">&mdash;</span>') +
          "</td></tr>");
      });
    });
    tbody.innerHTML = html.join("");

    tbody.addEventListener("click", function (ev) {
      if (ev.target.closest("a")) { return; }  // let OSM links work
      var tr = ev.target.closest("tr");
      if (!tr) { return; }
      if (tr.classList.contains("street")) {
        toggleStreet(parseInt(tr.dataset.s, 10));
      } else {
        select(parseInt(tr.dataset.i, 10), true);
      }
    });
  }

  // Expand a street (and fit the map to its visible segments), or collapse it.
  function toggleStreet(si) {
    var s = streets[si];
    expanded[si] = !expanded[si];
    if (selectedStreetRow) { selectedStreetRow.classList.remove("selected"); }
    selectedStreetRow = null;
    if (expanded[si]) {
      // Collapsing is putting a street away, not choosing it: only an
      // expanded street is marked as the current one.
      selectedStreetRow = document.querySelector('#gap-table tr.street[data-s="' + si + '"]');
      if (selectedStreetRow) { selectedStreetRow.classList.add("selected"); }
      var shown = s.idx.filter(function (i) { return hits[i]; });
      var group = L.featureGroup((shown.length ? shown : s.idx).map(function (i) {
        return lines[i];
      }));
      map.fitBounds(group.getBounds(), { maxZoom: 18, padding: [40, 40] });
    }
    apply();
  }

  // Select a segment: thicken it, fit the map, open its popup, mark its row.
  function select(i, fromTable, at) {
    var f = features[i];
    if (!f) { return; }
    if (selectedIndex != null && lines[selectedIndex]) {
      lines[selectedIndex].setStyle({ weight: WEIGHT });
    }
    selectedIndex = i;
    var line = lines[i];
    line.setStyle({ weight: SELECTED_WEIGHT });
    line.bringToFront();
    if (fromTable) {
      map.fitBounds(line.getBounds(), { maxZoom: 19, padding: [60, 60] });
    }
    if (map.hasLayer(line)) {
      line.openPopup(at || (f._mid ? L.latLng(f._mid[0], f._mid[1]) : undefined));
    }

    // A segment picked on the map may belong to a collapsed street: open it
    // so the row exists to highlight.
    var si = streetOf[i];
    if (!expanded[si]) { expanded[si] = true; apply(); }
    if (selectedRow) { selectedRow.classList.remove("selected"); }
    var tr = document.querySelector('#gap-table tr.seg[data-i="' + i + '"]');
    if (tr) {
      tr.classList.add("selected");
      selectedRow = tr;
      if (!fromTable) { tr.scrollIntoView({ block: "center" }); }
    }
  }

  function wireControls() {
    var boxes = {};
    STATUSES.forEach(function (s) {
      boxes[s] = document.getElementById("show-" + s);
    });
    var filter = document.getElementById("filter");
    var streetRows = document.querySelectorAll("#gap-table tr.street");
    var segRows = {};
    Array.prototype.forEach.call(
      document.querySelectorAll("#gap-table tr.seg"),
      function (tr) { segRows[tr.dataset.i] = tr; });

    function statusOn(status) {
      var b = boxes[status];
      return b ? b.checked : true;
    }

    // A segment passes when its status is on and the filter text is empty,
    // in its street's name, or in its City id / OSM way id. A street row
    // shows while any of its segments pass; a segment row shows when its
    // street is expanded, or when the filter found it by id (so typing an id
    // lands on the row without hunting for its street).
    apply = function () {
      var q = filter.value.trim().toLowerCase();
      streets.forEach(function (s, si) {
        var nameHit = !q || s.name.toLowerCase().indexOf(q) > -1;
        var any = 0;
        s.idx.forEach(function (i) {
          var p = features[i].properties;
          var idHit = !!q && (String(p.id == null ? "" : p.id).indexOf(q) > -1 ||
            String(p.way == null ? "" : p.way).indexOf(q) > -1);
          var hit = statusOn(p.status) && (nameHit || idHit);
          hits[i] = hit;
          if (hit) { any++; }
          segRows[i].style.display =
            hit && (expanded[si] || (idHit && !nameHit)) ? "" : "none";
          var m = lines[i];
          if (hit && !map.hasLayer(m)) { map.addLayer(m); }
          else if (!hit && map.hasLayer(m)) { map.removeLayer(m); }
        });
        var tr = streetRows[si];
        tr.style.display = any ? "" : "none";
        tr.classList.toggle("open", !!expanded[si]);
        // How many of its segments the current filter shows, beside the name.
        tr.querySelector(".seg-n").textContent =
          any ? "(" + any + ")" : "";
      });
      if (selectedIndex != null && map.hasLayer(lines[selectedIndex])) {
        lines[selectedIndex].bringToFront();
      }
    };

    STATUSES.forEach(function (k) {
      if (boxes[k]) { boxes[k].addEventListener("change", apply); }
    });
    filter.addEventListener("input", apply);
    apply();

    // O / I open the selected segment on openstreetmap.org / in the iD editor.
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
