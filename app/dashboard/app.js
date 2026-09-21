/* SiroQ Analysis Dashboard — charts for every application and its saved
 * analyses. Talks to the same /api/v1 endpoints with the shared API key
 * (kept in localStorage after the first prompt). */
(function () {
  "use strict";

  const C = window.SiroqCharts;

  const state = {
    apps: [],
    appDetail: null,
    analysis: null,
    appId: null,
    analysisId: null,
  };

  const $ = function (id) { return document.getElementById(id); };
  const esc = C.esc;

  /* ---------- auth ---------- */

  function getKey() { return (localStorage.getItem("siroq_api_key") || "").trim(); }
  function setKey(k) { localStorage.setItem("siroq_api_key", k.trim()); }

  async function api(path, opts) {
    opts = opts || {};
    opts.headers = Object.assign({ "X-API-Key": getKey() }, opts.headers || {});
    const res = await fetch(path, opts);
    if (res.status === 401) { askForKey(); throw new Error("API key rejected"); }
    if (!res.ok) throw new Error("HTTP " + res.status + " on " + path);
    return res.json();
  }

  function askForKey() {
    const key = prompt(
      "Enter the SiroQ API key (X-API-Key) — it is stored in this browser only:",
      getKey()
    );
    if (key) { setKey(key); boot(); }
  }

  /* ---------- navigation ---------- */

  async function boot() {
    if (!getKey()) { askForKey(); return; }
    try {
      const data = await api("/api/v1/applications");
      state.apps = data.applications || [];
      renderAppNav();
      if (state.apps.length) selectApp(state.apps[0].id);
      else $("emptyBanner").innerHTML =
        "No applications yet. Upload files via <code>POST /api/v1/analyze</code> " +
        "or <code>POST /api/v1/applications/&lt;id&gt;/files</code>.";
    } catch (e) { fail(e); }
  }

  function renderAppNav() {
    const nav = $("appNav");
    nav.innerHTML = "";
    state.apps.forEach(function (a) {
      const el = document.createElement("div");
      el.className = "app-item" + (a.id === state.appId ? " active" : "");
      const dq = a.latest_analysis && a.latest_analysis.summary
        ? a.latest_analysis.summary.data_quality_score : null;
      el.innerHTML =
        '<div class="app-name">' + esc(a.name) + "</div>" +
        '<div class="app-sub">' + a.file_count + " file" + (a.file_count === 1 ? "" : "s") +
        " · " + a.analysis_count + " analysis" + (a.analysis_count === 1 ? "" : "es") +
        "</div>" +
        (dq !== null ? '<div class="app-dq dq-' + dqClass(dq) + '">' + dq + "</div>" : "");
      el.addEventListener("click", function () { selectApp(a.id); });
      nav.appendChild(el);
    });
  }

  function dqClass(v) { return v >= 90 ? "good" : v >= 70 ? "warn" : "bad"; }

  function analysisList() {
    return (state.appDetail && state.appDetail.analyses) || [];
  }

  async function selectApp(id) {
    state.appId = id;
    state.analysis = null;
    state.analysisId = null;
    renderAppNav();
    try {
      state.appDetail = await api("/api/v1/applications/" + id);
      renderAppHeader();
      renderAnalysesNav();
      const list = analysisList();
      if (list.length) selectAnalysis(list[0].id);
      else $("mainContent").innerHTML = '<div class="empty">No analyses yet for this application.</div>';
    } catch (e) { fail(e); }
  }

  function renderAnalysesNav() {
    const nav = $("analysisNav");
    nav.innerHTML = "";
    analysisList().forEach(function (a) {
      const el = document.createElement("div");
      el.className = "analysis-item" + (a.id === state.analysisId ? " active" : "");
      const s = a.summary || {};
      el.innerHTML =
        '<div class="analysis-when">' + esc(fmtWhen(a.created_at)) + "</div>" +
        '<div class="analysis-sub">' + (s.file_count || 0) + " files · " +
        fmtRows(s.total_rows) + " rows" + "</div>";
      el.addEventListener("click", function () { selectAnalysis(a.id); });
      nav.appendChild(el);
    });
  }

  function fmtWhen(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    const now = new Date();
    const diff = now - d;
    if (diff < 60000) return "just now";
    if (diff < 3600000) return Math.floor(diff / 60000) + "m ago";
    if (diff < 86400000) return Math.floor(diff / 3600000) + "h ago";
    return d.toLocaleDateString();
  }

  function fmtRows(n) { return Number(n || 0).toLocaleString(); }

  /* ---------- app header + trend ---------- */

  function renderAppHeader() {
    const d = state.appDetail;
    const head = $("appHeader");
    head.innerHTML =
      '<h2>' + esc(d.name) + "</h2>" +
      '<div class="app-meta">created ' + esc(fmtWhen(d.created_at)) + " · " +
      d.files.length + " file" + (d.files.length === 1 ? "" : "s") +
      " · latest analysis " + (d.analyses[0] ? esc(fmtWhen(d.analyses[0].created_at)) : "—") + "</div>";
  }

  function renderTrend() {
    const box = $("trendBox");
    const list = analysisList().slice(0, 30).reverse();
    const quality = list.map(function (a) {
      return { label: fmtWhen(a.created_at), value: a.summary && a.summary.data_quality_score };
    }).filter(function (p) { return p.value !== null && p.value !== undefined; });
    const rows = list.map(function (a) {
      return { label: fmtWhen(a.created_at), value: a.summary ? (a.summary.total_rows || 0) : 0 };
    });
    let html = "";
    if (quality.length) {
      html += '<div class="chart-block"><h4>Data quality over analyses</h4><div class="chart" id="trendQ"></div></div>';
    }
    if (rows.length) {
      html += '<div class="chart-block"><h4>Total rows over analyses</h4><div class="chart" id="trendR"></div></div>';
    }
    box.innerHTML = html || '<div class="muted">Upload more files and re-run analysis to see trends.</div>';
    if (quality.length) C.line($("trendQ"), quality);
    if (rows.length) C.hBar($("trendR"), rows.map(function (r) { return { label: r.label, value: r.value }; }));
  }

  /* ---------- full analysis charts ---------- */

  async function selectAnalysis(id) {
    state.analysisId = id;
    renderAnalysesNav();
    try {
      state.analysis = await api("/api/v1/applications/" + state.appId + "/analyses/" + id);
      renderMain();
      $("openReportBtn").disabled = false;
      $("dlJsonBtn").disabled = false;
    } catch (e) { fail(e); }
  }

  function renderMain() {
    const analysis = state.analysis;
    renderTrend();
    const r = analysis.report || {};
    const s = analysis.summary || {};
    renderSummary(s, r);
    renderFiles(r.files || []);
    // actions target the selected analysis
  }

  function renderSummary(s, r) {
    const box = $("summaryBox");
    const cats = s.categories_detected || {};
    const catItems = Object.keys(cats).map(function (k) {
      return { label: k, value: (cats[k] || []).length };
    }).sort(function (a, b) { return b.value - a.value; });
    const html =
      kpi("Files", s.file_count) +
      kpi("Total rows", fmtRows(s.total_rows)) +
      kpi("Quality score", s.data_quality_score === null || s.data_quality_score === undefined ? "—" : s.data_quality_score + " / 100", dqClass(s.data_quality_score)) +
      kpi("Findings", s.findings_count);
    box.innerHTML = html;
    const catBox = $("categoryBox");
    catBox.innerHTML = "";
    if (catItems.length) C.donut(catBox, catItems, { center: catItems.reduce(function (a, b) { return a + b.value; }, 0), centerLabel: "categories" });
    else catBox.innerHTML = '<span class="muted">no categories detected</span>';
  }

  function kpi(name, value, cls) {
    return '<div class="kpi"><div class="kpi-name">' + esc(name) + '</div><div class="kpi-value ' + (cls || "") + '">' + esc(String(value)) + "</div></div>";
  }

  function renderFiles(files) {
    const box = $("filesBox");
    box.innerHTML = "";
    files.forEach(function (f, idx) {
      const card = document.createElement("div");
      card.className = "file-card";
      card.innerHTML = fileHeader(f, idx);
      const body = document.createElement("div");
      body.className = "file-body";
      body.appendChild(fileDetail(f));
      card.appendChild(body);
      box.appendChild(card);
    });
  }

  function fileHeader(f, idx) {
    const tag = f.top_category ? '<span class="tag tag-' + esc(f.top_category) + '">' + esc(f.top_category) + "</span>" : "";
    const notes = (f.notes || []).map(function (n) { return "<div class='note'>" + esc(n) + "</div>"; }).join("");
    return '<div class="file-head">' +
      "<h4>" + (idx + 1) + ". " + esc(f.filename) + "</h4>" +
      '<span class="file-meta">' + esc(f.file_type || "") + " · " + fmtRows(f.row_count) + " rows · " +
      (f.columns ? f.columns.length : 0) + " cols" + (f.multi_sheet ? " · multi-sheet" : "") + "</span>" +
      '<span class="file-tags">' + tag + "</span>" +
      '<div class="file-notes">' + notes + "</div>" +
      "</div>";
  }

  function fileDetail(f) {
    const wrap = document.createElement("div");
    wrap.appendChild(sectionHtml("File summary", fileSummaryGrid(f)));
    const grid = document.createElement("div");
    grid.className = "charts-grid";
    grid.appendChild(block("Category probability", f.categories, "barrel"));
    grid.appendChild(block("Data quality", null, "quality", f));
    wrap.appendChild(grid);

    const domainEl = domainSection(f);
    if (domainEl) wrap.appendChild(domainEl);

    const pgrid = document.createElement("div");
    pgrid.className = "charts-grid";
    wrap.appendChild(sectionHtml("Column profile", ""));
    pgrid.appendChild(block("Null share", null, "nulls", f));
    pgrid.appendChild(block("Unique share", null, "uniques", f));
    wrap.appendChild(pgrid);
    wrap.appendChild(numberProfile(f));
    wrap.appendChild(categoricalProfile(f));

    const details = document.createElement("details");
    details.className = "rawjson";
    details.innerHTML = "<summary>Raw report JSON</summary><pre>" + esc(JSON.stringify(f, null, 2)) + "</pre>";
    wrap.appendChild(details);
    return wrap;
  }

  function sectionHtml(title, inner) {
    const d = document.createElement("div");
    d.className = "section-block";
    d.innerHTML = "<h4>" + esc(title) + "</h4>" + inner;
    return d;
  }

  function fileSummaryGrid(f) {
    const d = document.createElement("div");
    d.className = "summary-grid";
    d.innerHTML = [
      ["File", esc(f.filename || "")],
      ["Type", esc(f.file_type || "")],
      ["Rows", fmtRows(f.row_count)],
      ["Columns", (f.columns || []).length],
      ["Size", fmtBytes(f.size_bytes)],
      ["SHA-256", esc((f.sha256 || "").slice(0, 12) + "…")],
      ["Sheet", f.sheet_categories ? esc(f.sheet_categories.map(function (s) { return s.sheet + "→" + s.category; }).join(", ")) : "—"],
    ].reduce(function (h, pair) { return h + '<div class="sum"><span class="sum-k">' + esc(pair[0]) + "</span><span class='sum-v'>" + esc(pair[1]) + "</span></div>"; }, "");
    return d;
  }

  function fmtBytes(n) {
    n = Number(n || 0);
    if (n >= 1048576) return (n / 1048576).toFixed(1) + " MB";
    if (n >= 1024) return (n / 1024).toFixed(1) + " KB";
    return n + " B";
  }

  function block(title, data, kind, f, domain) {
    const d = document.createElement("div");
    d.className = "chart-block";
    d.setAttribute("data-kind", kind);
    d.innerHTML = "<h4>" + esc(title) + "</h4><div class='chart' data-chart='true'></div><div class='chart-extra'></div>";
    const cbox = d.querySelector(".chart");
    const extra = d.querySelector(".chart-extra");
    if (kind === "barrel") {
      C.hBar(cbox, data ? Object.keys(data).map(function (k) { return { label: k, value: data[k] }; }).sort(function (a, b) { return b.value - a.value; }) : []);
    } else if (kind === "quality") {
      const dq = (f.data_quality || {});
      C.gauge(cbox, dq.score);
      const checks = (dq.checks || []).map(function (ch) {
        return '<span class="pill pill-' + esc(ch.status) + '">' + esc(ch.check) + ": " + esc(ch.status) + "</span>";
      });
      extra.innerHTML = checks.join(" ") + (f.quality_findings && f.quality_findings.length ?
        "<ul class='findings'>" + f.quality_findings.map(function (x) { return "<li>" + esc(x) + "</li>"; }).join("") + "</ul>" : "");
    } else if (kind === "nulls") {
      const rows = (f.profile && f.profile.column_profiles || [])
        .map(function (c) { return { label: c.name, value: c.null_pct, pct: c.null_pct }; });
      C.hBar(cbox, rows);
    } else if (kind === "uniques") {
      const rows = (f.profile && f.profile.column_profiles || [])
        .map(function (c) { return { label: c.name, value: c.unique_pct, pct: c.unique_pct }; });
      C.hBar(cbox, rows);
    }
    return d;
  }

  /* ----- domain chart extraction ----- */

  function domainData(f) {
    const da = f.domain_analytics || {};
    const out = { kpis: [], bars: [], donuts: [], lines: [], tables: [], notes: [] };
    if (typeof da !== "object" || da === null) return out;
    const consumed = new Set(["skipped", "category"]);
    if (Array.isArray(da.skipped)) out.notes.push("skipped: " + da.skipped.join(", "));

    function takeList(key, label, lk, vk, mk) {
      const arr = da[key];
      if (!Array.isArray(arr) || !arr.length) return;
      out.bars.push({ title: mk || (label + " by " + vk), items: arr.map(function (r) { return { label: r[lk], value: r[vk] }; }) });
      consumed.add(key);
    }
    takeList("top_products", "Top products", "product", "amount");
    takeList("payment_mix", "Payment mix", "method", "amount");
    if (Array.isArray(da.daily_series) && da.daily_series.length) {
      out.lines.push({ title: "Daily series", items: da.daily_series.map(function (r) { return { label: String(r.date), value: r.count }; }) });
      consumed.add("daily_series");
    }
    if (da.expiry_risk_days && typeof da.expiry_risk_days === "object") {
      out.vbars = [{
        title: "Expiry risk (days ahead)",
        labels: Object.keys(da.expiry_risk_days),
        series: [{ name: "items", values: Object.keys(da.expiry_risk_days).map(function (k) { return da.expiry_risk_days[k]; }) }],
      }];
      consumed.add("expiry_risk_days");
    }
    const abc = da.abc_classes;
    if (abc && typeof abc === "object") {
      const counts = { A: 0, B: 0, C: 0 };
      Object.keys(abc).forEach(function (k) { counts[abc[k]] = (counts[abc[k]] || 0) + 1; });
      out.donuts.push({ title: "ABC classification", items: Object.keys(counts).map(function (k) { return { label: k, value: counts[k] }; }) });
      consumed.add("abc_classes");
    }

    Object.keys(da).forEach(function (key) {
      if (consumed.has(key)) return;
      const v = da[key];
      if (Array.isArray(v) && v.length && typeof v[0] === "object") {
        if (v[0].value !== undefined && v[0].count !== undefined) {
          out.bars.push({ title: key.replace(/_/g, " "), items: v.map(function (r) { return { label: r.value, value: r.count }; }) });
        } else {
          out.tables.push({ title: key.replace(/_/g, " "), headers: Object.keys(v[0]), rows: v.map(function (r) { return Object.keys(r).map(function (k) { return r[k]; }); }) });
        }
      } else if (typeof v === "object" && v !== null && Object.keys(v).length) {
        const nums = Object.keys(v).filter(function (k) { return typeof v[k] === "number"; });
        if (nums.length) out.bars.push({ title: key.replace(/_/g, " "), items: nums.map(function (k) { return { label: k, value: v[k] }; }) });
        else out.notes.push(key + ": " + JSON.stringify(v).slice(0, 200));
      } else if (typeof v === "number" && v !== da.category) {
        out.kpis.push({ label: key.replace(/_/g, " "), value: C.fmt(v) });
      }
    });
    return out;
  }

  function chartBlock(title, kind, data) {
    const blk = document.createElement("div");
    blk.className = "chart-block";
    blk.dataset.kind = kind;
    blk.innerHTML = "<h4>" + esc(title) + "</h4><div class='chart'></div>";
    const cbox = blk.querySelector(".chart");
    if (kind === "dbar") C.hBar(cbox, data.items);
    else if (kind === "ddonut") C.donut(cbox, data.items);
    else if (kind === "dline") C.line(cbox, data.items);
    else if (kind === "dvbar") C.vBars(cbox, data);
    else if (kind === "dtable") {
      blk.innerHTML = "<h4>" + esc(title) + "</h4>" + tableHtml(data.headers || [], data.rows || []);
    }
    return blk;
  }

  function domainSection(f) {
    const dd = domainData(f);
    if (!dd.kpis.length && !dd.bars.length && !dd.donuts.length && !dd.lines.length && !dd.vbars.length && !dd.tables.length && !dd.notes.length) {
      return null;
    }
    const sec = document.createElement("div");
    sec.className = "section-block";
    sec.innerHTML = "<h4>Domain analytics · " + esc(f.top_category || "—") + "</h4>";
    const k = kpiGrid(dd.kpis);
    if (k) sec.insertAdjacentHTML("beforeend", k);
    const g = document.createElement("div");
    g.className = "charts-grid";
    dd.bars.forEach(function (b) { g.appendChild(chartBlock(b.title, "dbar", b)); });
    dd.donuts.forEach(function (dn) { g.appendChild(chartBlock(dn.title, "ddonut", dn)); });
    dd.lines.forEach(function (ln) { g.appendChild(chartBlock(ln.title, "dline", ln)); });
    dd.vbars.forEach(function (vb) { g.appendChild(chartBlock(vb.title, "dvbar", vb)); });
    dd.tables.forEach(function (t) { g.appendChild(chartBlock(t.title, "dtable", t)); });
    if (g.children.length) sec.appendChild(g);
    dd.notes.forEach(function (n) {
      const p = document.createElement("div");
      p.className = "note";
      p.textContent = n;
      sec.appendChild(p);
    });
    return sec;
  }

  function tableHtml(headers, rows) {
    var h = "<table><tr>" + headers.map(function (x) { return "<th>" + esc(x) + "</th>"; }).join("") + "</tr>";
    rows.forEach(function (row) {
      h += "<tr>" + row.map(function (cell) { return "<td>" + esc(cell) + "</td>"; }).join("") + "</tr>";
    });
    return h + "</table>";
  }

  function kpiGrid(kpis) {
    if (!kpis.length) return "";
    return '<div class="kpis">' + kpis.map(function (k) { return kpi(k.label, k.value); }).join("") + "</div>";
  }

  function numberProfile(f) {
    const cols = (f.profile && f.profile.column_profiles || []).filter(function (c) { return c.kind === "number"; });
    if (!cols.length) return document.createElement("div");
    cols.sort(function (a, b) { return (b.mean || 0) - (a.mean || 0); });
    const top = cols.slice(0, 8);
    const d = document.createElement("div");
    d.className = "charts-grid";
    const blk = document.createElement("div");
    blk.className = "chart-block";
    blk.innerHTML = "<h4>Numeric columns · mean vs median</h4><div class='chart'></div>";
    d.appendChild(blk);
    C.vBars(blk.querySelector(".chart"), {
      labels: top.map(function (c) { return c.name; }),
      series: [
        { name: "mean", values: top.map(function (c) { return c.mean || 0; }), color: "#2563eb" },
        { name: "median", values: top.map(function (c) { return c.median || 0; }), color: "#059669" },
      ],
    });
    return d;
  }

  function categoricalProfile(f) {
    const cols = (f.profile && f.profile.column_profiles || []).filter(function (c) {
      return (c.kind === "category" || c.kind === "text") && (c.top_values || []).length;
    });
    if (!cols.length) return document.createElement("div");
    const top = cols.slice(0, 4);
    const d = document.createElement("div");
    d.className = "charts-grid";
    top.forEach(function (c) {
      const blk = document.createElement("div");
      blk.className = "chart-block";
      blk.innerHTML = "<h4>Top values · " + esc(c.name) + "</h4><div class='chart'></div>";
      d.appendChild(blk);
      C.hBar(blk.querySelector(".chart"), (c.top_values || []).slice(0, 10)
        .map(function (t) { return { label: t.value, value: t.count }; }));
    });
    return d;
  }

  /* ---------- actions ---------- */

  function actions() {
    const aid = state.analysisId, appId = state.appId;
    $("openReportBtn").addEventListener("click", async function () {
      try {
        const res = await fetch(
          "/api/v1/applications/" + appId + "/analyses/" + aid + "/report?format=html",
          { headers: { "X-API-Key": getKey() } }
        );
        if (res.status === 401) { askForKey(); return; }
        const html = await res.text();
        const w = window.open("", "_blank");
        if (!w) { alert("Pop-up blocked — allow pop-ups for the report."); return; }
        w.document.open();
        w.document.write(html);
        w.document.close();
      } catch (e) { alert("Could not open report: " + e.message); }
    });
    $("dlJsonBtn").addEventListener("click", async function () {
      try {
        const a = await api("/api/v1/applications/" + appId + "/analyses/" + aid);
        const blob = new Blob([JSON.stringify(a, null, 2)], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const el = document.createElement("a");
        el.href = url;
        el.download = "siroq-analysis-" + aid.slice(0, 8) + ".json";
        el.click();
        URL.revokeObjectURL(url);
      } catch (e) { alert("Could not download JSON: " + e.message); }
    });
  }

  /* ---------- misc ---------- */

  function fail(e) {
    if (String(e.message).includes("401")) { askForKey(); return; }
    const box = $("emptyBanner");
    box.innerHTML = '<div class="empty">Error: ' + esc(e.message) + "</div>";
  }

  let resizeTimer = null;
  function onResize() {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      if (state.analysis) renderMain();
    }, 220);
  }

  document.addEventListener("DOMContentLoaded", function () {
    actions();
    boot();
    window.addEventListener("resize", onResize);
    $("reloadBtn").addEventListener("click", boot);
    $("keyBtn").addEventListener("click", function () {
      const k = prompt("SiroQ API key (X-API-Key):", getKey());
      if (k) { setKey(k); boot(); }
    });
  });
})();