/* Micro SVG chart library for the SiroQ dashboard.
 * No dependencies, deterministic output, print-friendly. Each builder takes a
 * container element and appends an inline SVG sized to the element's width. */
(function () {
  "use strict";

  const Palette = [
    "#2563eb", "#7c3aed", "#059669", "#d97706", "#dc2626", "#0ea5e9",
    "#db2777", "#ca8a04", "#4f46e5", "#65a30d", "#0891b2", "#9333ea",
  ];

  function color(i) { return Palette[i % Palette.length]; }

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function fmt(v) {
    if (typeof v !== "number") return String(v);
    return Math.abs(v) >= 1e6 || (Math.abs(v) < 0.01 && v !== 0)
      ? v.toPrecision(3)
      : Number(v.toFixed(2)).toLocaleString();
  }

  function svgEl(tag, attrs) {
    const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const k in attrs) el.setAttribute(k, attrs[k]);
    return el;
  }

  function newSvg(width, height) {
    const svg = svgEl("svg", { width: width, height: height, viewBox: "0 0 " + width + " " + height, class: "siroq-chart" });
    return svg;
  }

  function clear(el) {
    while (el.firstChild) el.removeChild(el.firstChild);
    // replace any existing svg placeholder header
  }

  const W = "width";

  function hBar(container, items, opts) {
    opts = opts || {};
    const pad = 14;
    const rowH = 20;
    const labelW = Math.max(90, Math.min(220, Math.round((container.clientWidth || 600) * 0.34)));
    const valW = 64;
    const h = pad * 2 + rowH * items.length + (opts.ticks ? 16 : 0);
    const w = container.clientWidth || 600;
    clear(container);
    if (!items.length) { container.innerHTML = '<span class="muted">no data</span>'; return; }
    const svg = newSvg(w, h);
    const chartW = w - labelW - valW - 40;
    const MAXL = Math.floor(labelW / 6.2);
    items.forEach(function (it, i) {
      const y = pad + i * rowH;
      let pct = (it.pct !== undefined && it.pct !== null) ? Number(it.pct) : 0;
      if (!pct && Number(it.value) > 0) {
        const maxv = opts.max || (items.reduce(function (s, x) { return Math.max(s, Number(x.value) || 0); }, 0));
        if (maxv > 0) pct = Math.max(0, Math.min(100, (Number(it.value) / maxv) * 100));
      }
      const len = Math.max(0, Math.min(100, pct || 0)) / 100 * chartW;
      const t = svgEl("text", { x: 0, y: y + 12, class: "clbl", "text-anchor": "start" });
      const full = (it.label === undefined || it.label === null) ? "" : String(it.label);
      t.textContent = full.length > MAXL ? full.slice(0, MAXL - 1) + "…" : full;
      if (full.length > MAXL) t.title = full;
      svg.appendChild(t);
      const track = svgEl("rect", { x: labelW, y: y + 3, width: chartW, height: 12, rx: 6, class: "ctrack" });
      svg.appendChild(track);
      const fill = svgEl("rect", { x: labelW, y: y + 3, width: Math.max(0, len), height: 12, rx: 6, class: "cfill", fill: it.color || color(i) });
      svg.appendChild(fill);
      if (len < 8) fill.setAttribute("rx", "6");
      const tip = svgEl("title");
      tip.textContent = it.label + ": " + fmt(it.value);
      fill.appendChild(tip);
      svg.appendChild(svgEl("text", { x: labelW + chartW + 10, y: y + 12, class: "cval", "text-anchor": "start" })).textContent = fmt(it.value);
    });
    container.appendChild(svg);
  }

  function vBars(container, opts) {
    // opts: {labels: [...], series: [{name, values: [...], color?}], hPadding?}
    opts = opts || {};
    const labels = opts.labels || [];
    const series = opts.series || [];
    const H = 200;
    const w = container.clientWidth || 600;
    clear(container);
    if (!labels.length || !series.length) { container.innerHTML = '<span class="muted">no data</span>'; return; }
    const top = 18;
    const bottom = 38;
    const chartH = H - top - bottom;
    const groupW = w / labels.length;
    const barW = Math.min(44, (groupW - 12) / series.length);
    let maxv = 0;
    series.forEach(function (s) { s.values.forEach(function (v) { if (v > maxv) maxv = v; }); });
    if (maxv === 0) maxv = 1;
    const svg = newSvg(w, H);
    const y = function (v) { return top + chartH - (v / maxv) * chartH; };
    // baseline
    svg.appendChild(svgEl("line", { x1: 0, y1: y(0), x2: w, y2: y(0), class: "caxis" }));
    labels.forEach(function (lb, i) {
      const cx = i * groupW + groupW / 2;
      series.forEach(function (s, j) {
        const bw = barW * 0.82;
        const x = cx + (j - (series.length - 1) / 2) * barW - bw / 2;
        const v = s.values[i] || 0;
        const r = svgEl("rect", { x: x, y: y(v), width: bw, height: Math.max(1, chartH - y(v) + y(0)), fill: s.color || color(j), rx: 3 });
        const t = svgEl("title");
        t.textContent = lb + " · " + s.name + ": " + fmt(v);
        r.appendChild(t);
        svg.appendChild(r);
        if (v !== 0) {
          const txt = svgEl("text", { x: x + bw / 2, y: y(v) - 4, class: "cval", "text-anchor": "middle" });
          txt.textContent = fmt(v);
          svg.appendChild(txt);
        }
      });
      const lblTxt = svgEl("text", { x: cx, y: H - 16, class: "clbl", "text-anchor": "middle" });
      lblTxt.textContent = lb;
      svg.appendChild(lblTxt);
    });
    // legend
    let lx = 4;
    const ly = 8;
    series.forEach(function (s, j) {
      const g = svgEl("g", {});
      g.appendChild(svgEl("rect", { x: lx, y: ly - 7, width: 9, height: 9, fill: s.color || color(j), rx: 2 }));
      const t = svgEl("text", { x: lx + 13, y: ly, class: "cleg" });
      t.textContent = s.name;
      g.appendChild(t);
      svg.appendChild(g);
      lx += 16 + t.getComputedTextLength ? t.getComputedTextLength() + 16 : (s.name.length * 7 + 24);
    });
    container.appendChild(svg);
  }

  function line(container, items, opts) {
    opts = opts || {};
    const H = 200;
    const w = container.clientWidth || 600;
    clear(container);
    if (!items.length) { container.innerHTML = '<span class="muted">no data</span>'; return; }
    const top = 16, bottom = 34, left = 0, right = 8;
    const chartW = w - left - right;
    const chartH = H - top - bottom;
    const vals = items.map(function (it) { return Number(it.value); });
    let mx = Math.max.apply(null, vals);
    const mn = Math.min.apply(null, vals);
    if (mx === mn) { mx = mx + 1; }
    const x = function (i) { return left + (items.length === 1 ? chartW / 2 : (i / (items.length - 1)) * chartW); };
    const y = function (v) { return top + chartH - ((v - mn) / (mx - mn)) * chartH; };
    const svg = newSvg(w, H);
    const stepLabel = Math.max(1, Math.ceil(items.length / 12));
    let path = "", area = "";
    items.forEach(function (it, i) {
      path += (i ? " L" : "M") + x(i).toFixed(1) + " " + y(it.value).toFixed(1);
      area += (i ? " L" : "M") + x(i).toFixed(1) + " " + y(it.value).toFixed(1);
      if (i % stepLabel === 0 || i === items.length - 1) {
        const t = svgEl("text", { x: x(i), y: H - 12, class: "clbl", "text-anchor": "middle" });
        t.textContent = String(it.label);
        svg.appendChild(t);
      }
      const c = svgEl("circle", { cx: x(i), cy: y(it.value), r: 2.5, class: "cdot" });
      const tip = svgEl("title");
      tip.textContent = it.label + ": " + fmt(it.value);
      c.appendChild(tip);
      svg.appendChild(c);
    });
    area += " L" + x(items.length - 1).toFixed(1) + " " + y(mn) + " L" + x(0).toFixed(1) + " " + y(mn) + " Z";
    svg.appendChild(svgEl("path", { d: area, class: "carea" }));
    svg.appendChild(svgEl("path", { d: path, class: "cpath" }));
    container.appendChild(svg);
  }

  function donut(container, items, opts) {
    opts = opts || {};
    const cx = 90, cy = 90, R = 64, ir = 42;
    const legendW = Math.max(140, (container.clientWidth || 600) - 210);
    const w = container.clientWidth || 600;
    const H = opts.table ? 220 : 190;
    clear(container);
    const total = items.reduce(function (s, it) { return s + Math.max(0, Number(it.value)); }, 0);
    if (!total || !items.length) { container.innerHTML = '<span class="muted">no data</span>'; return; }
    const svg = newSvg(w, H);
    const arc = function (a0, a1, r) {
      const x0 = cx + r * Math.cos(a0), y0 = cy + r * Math.sin(a0);
      const x1 = cx + r * Math.cos(a1), y1 = cy + r * Math.sin(a1);
      const large = (a1 - a0) > Math.PI ? 1 : 0;
      return "M" + x0.toFixed(2) + " " + y0.toFixed(2) + " A" + r + " " + r + " 0 " + large + " 1 " + x1.toFixed(2) + " " + y1.toFixed(2);
    };
    let a0 = Math.PI * 1.5;
    items.forEach(function (it, i) {
      const frac = Math.max(0, Number(it.value)) / total;
      const a1 = a0 + frac * Math.PI * 2;
      const d = arc(a0, a1, R) + " L" + (cx + ir * Math.cos(a1)).toFixed(2) + " " + (cy + ir * Math.sin(a1)).toFixed(2) + " " + arc(a1, a0, ir) + " Z";
      const seg = svgEl("path", { d: d, fill: it.color || color(i), "stroke": "#fff", "stroke-width": 1 });
      const t = svgEl("title");
      t.textContent = it.label + ": " + fmt(it.value) + " (" + Math.round(frac * 100) + "%)";
      seg.appendChild(t);
      svg.appendChild(seg);
      a0 = a1;
    });
    if (opts.center !== undefined && opts.center !== null) {
      const c = svgEl("text", { x: cx, y: cy - 2, class: "ccenter" });
      c.textContent = String(opts.center);
      svg.appendChild(c);
      const sub = svgEl("text", { x: cx, y: cy + 16, class: "cleg", "text-anchor": "middle" });
      sub.textContent = opts.centerLabel || "";
      svg.appendChild(sub);
    }
    let ly = opts.table ? 30 : 14;
    items.forEach(function (it, i) {
      const g = svgEl("g", {});
      g.appendChild(svgEl("rect", { x: 210, y: ly - 3, width: 9, height: 9, fill: it.color || color(i), rx: 2 }));
      const lt = svgEl("text", { x: 224, y: ly + 4, class: "cleg" });
      lt.textContent = it.label + " — " + fmt(it.value);
      g.appendChild(lt);
      svg.appendChild(g);
      ly += 18;
    });
    container.appendChild(svg);
  }

  function gauge(container, score, sub) {
    const w = container.clientWidth || 260;
    const H = 140;
    clear(container);
    if (score === null || score === undefined || isNaN(score)) {
      container.innerHTML = '<span class="muted">n/a</span>';
      return;
    }
    const cx = w / 2, cy = 108, R = Math.min(96, w / 2 - 24);
    const svg = newSvg(w, H);
    const a0 = Math.PI * 1.0;
    const arcPath = function (ang) {
      const x0 = cx + R * Math.cos(a0), y0 = cy + R * Math.sin(a0);
      const x1 = cx + R * Math.cos(ang), y1 = cy + R * Math.sin(ang);
      return "M" + x0.toFixed(2) + " " + y0.toFixed(2) + " A" + R + " " + R + " 0 0 1 " + x1.toFixed(2) + " " + y1.toFixed(2);
    };
    svg.appendChild(svgEl("path", { d: arcPath(Math.PI * 2.0), class: "gtrack" }));
    const frac = Math.max(0, Math.min(1, score / 100));
    const col = score >= 90 ? "#16a34a" : score >= 70 ? "#d97706" : "#dc2626";
    svg.appendChild(svgEl("path", { d: arcPath(Math.PI * 1.0 + frac * Math.PI), class: "gfill", stroke: col }));
    const c = svgEl("text", { x: cx, y: cy - 8, class: "gscore", "text-anchor": "middle", fill: col });
    c.textContent = String(score);
    svg.appendChild(c);
    const t = svgEl("text", { x: cx, y: cy + 14, class: "cleg", "text-anchor": "middle" });
    t.textContent = sub || "data quality / 100";
    svg.appendChild(t);
    container.appendChild(svg);
  }

  function forecastChart(container, payload, opts) {
    opts = opts || {};
    const hist = (payload.series || []).slice();
    const fcst = (payload.forecast || []).slice();
    const H = 220;
    const w = container.clientWidth || 600;
    clear(container);
    if (!hist.length || !fcst.length) { container.innerHTML = '<span class="muted">no forecast data</span>'; return; }
    const top = 18, bottom = 36, left = 6, right = 10;
    const chartW = w - left - right;
    const chartH = H - top - bottom;
    const dateOf = function (d) { return String(d).slice(0, 10); };
    const hv = hist.map(function (p) { return Number(p.value); });
    const fv = fcst.map(function (p) { return Number(p.value); });
    let mx = Math.max.apply(null, hv.concat(fv));
    let mn = Math.min.apply(null, hv.concat(fv));
    if (mn >= 0) { mn = 0; mx = mx * 1.08 || 1; }
    if (mx === mn) { mx = mn + 1; }
    const total = hist.length + fcst.length;
    const x = function (i) { return left + (total === 1 ? chartW / 2 : (i / (total - 1)) * chartW); };
    const y = function (v) { return top + chartH - ((v - mn) / (mx - mn)) * chartH; };
    const svg = newSvg(w, H);

    // confidence-interval band (drawn first, under the lines)
    const b0i = hist.length - 1;
    const b0y = y(hv[hv.length - 1]);
    let band = "M" + x(b0i).toFixed(1) + " " + b0y.toFixed(1);
    fcst.forEach(function (p, i) {
      band += " L" + x(hist.length + i).toFixed(1) + " " + y(Math.max(mn, Number(p.upper) || b0y)).toFixed(1);
    });
    for (var i = fcst.length - 1; i >= 0; i--) {
      const p = fcst[i];
      band += " L" + x(hist.length + i).toFixed(1) + " " + y(Math.max(mn, Number(p.lower) || b0y)).toFixed(1);
    }
    band += " L" + x(b0i).toFixed(1) + " " + b0y.toFixed(1) + " Z";
    svg.appendChild(svgEl("path", { d: band, class: "fcst-band" }));

    // boundary divider between history and forecast
    svg.appendChild(svgEl("line", {
      x1: x(b0i).toFixed(1), y1: top, x2: x(b0i).toFixed(1), y2: top + chartH,
      class: "fcst-divider",
    }));

    // history line
    let hpath = "";
    hist.forEach(function (p, i) {
      hpath += (i ? " L" : "M") + x(i).toFixed(1) + " " + y(p.value).toFixed(1);
      const c = svgEl("circle", { cx: x(i).toFixed(1), cy: y(p.value).toFixed(1), r: 2.2 });
      const t = svgEl("title");
      t.textContent = dateOf(p.date) + ": " + fmt(p.value);
      c.appendChild(t);
      svg.appendChild(c);
    });
    svg.appendChild(svgEl("path", { d: hpath, class: "cpath" }));

    // forecast line (dashed)
    let fpath = "M" + x(b0i).toFixed(1) + " " + b0y.toFixed(1);
    fcst.forEach(function (p, i) {
      fpath += " L" + x(hist.length + i).toFixed(1) + " " + y(p.value).toFixed(1);
      const c = svgEl("circle", { cx: x(hist.length + i).toFixed(1), cy: y(p.value).toFixed(1), r: 2.2, class: "fcst-dot" });
      const t = svgEl("title");
      t.textContent = dateOf(p.date) + " (step " + p.step + "): " + fmt(p.value) +
        "  [" + fmt(p.lower) + " .. " + fmt(p.upper) + "]";
      c.appendChild(t);
      svg.appendChild(c);
    });
    svg.appendChild(svgEl("path", { d: fpath, class: "fcst-path" }));

    // axis labels: first, last, and a couple in between
    const idxs = [0, Math.floor(hist.length / 2), hist.length - 1, hist.length + fcst.length - 1];
    idxs.forEach(function (i) {
      const d = i < hist.length ? hist[i].date : fcst[i - hist.length].date;
      const t = svgEl("text", { x: x(i).toFixed(1), y: H - 12, class: "clbl", "text-anchor": "middle" });
      t.textContent = dateOf(d);
      svg.appendChild(t);
    });
    // baseline
    svg.appendChild(svgEl("line", { x1: left, y1: y(mn).toFixed(1), x2: w - right, y2: y(mn).toFixed(1), class: "caxis" }));
    container.appendChild(svg);
  }

  window.SiroqCharts = { hBar: hBar, vBars: vBars, line: line, donut: donut, gauge: gauge, forecast: forecastChart, color: color, esc: esc, fmt: fmt };
})();