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

  const PREVIEW_PAGE_ROWS = 50;
  const CELL_CLIP = 400;

  const $ = function (id) { return document.getElementById(id); };
  const esc = C.esc;

  /* ---------- auth ---------- */

  function getKey() { return (localStorage.getItem("siroq_api_key") || "").trim(); }
  function setKey(k) { localStorage.setItem("siroq_api_key", k.trim()); }

  async function api(path, opts) {
    opts = opts || {};
    opts.headers = Object.assign({ "X-API-Key": getKey() }, opts.headers || {});
    const res = await fetch(path, opts);
    if (res.status === 401) { openKeyModal("API key rejected — please enter a valid key."); throw new Error("API key rejected"); }
    if (!res.ok) throw new Error("HTTP " + res.status + " on " + path);
    return res.json();
  }

  /* ---------- api key input (modal, not a native prompt) ---------- */

  function openKeyModal(msg) {
    const modal = $("keyModal");
    const input = $("keyInput");
    input.value = getKey();
    $("keyMsg").textContent = msg || "";
    input.classList.toggle("bad", !!msg);
    modal.hidden = false;
    input.focus();
  }

  function closeKeyModal() {
    $("keyModal").hidden = true;
  }

  function saveKey() {
    const k = $("keyInput").value.trim();
    if (!k) { $("keyMsg").textContent = "A key is required."; return; }
    setKey(k);
    closeKeyModal();
    boot();
  }

  function showNoKey() {
    $("emptyBanner").innerHTML =
      '<div class="empty">Add your SiroQ API key to load applications. ' +
      '<button class="btn primary" id="openKeyAgain">Add API key</button></div>';
    const btn = $("openKeyAgain");
    if (btn) btn.addEventListener("click", function () { openKeyModal(); });
  }

  /* ---------- create application / add files (modal) ---------- */

  let appModalMode = "create";

  function openCreateModal() {
    appModalMode = "create";
    $("appModalTitle").textContent = "New application";
    $("appModalNote").textContent =
      "Give the application a name. Optionally attach files — they are uploaded " +
      "and analyzed immediately (the analysis then shows here).";
    $("appNameInput").hidden = false;
    var lbl = $("appNameInput").closest("label");
    lbl.hidden = false;
    $("appCreateBtn").textContent = "Create application";
    $("appFilesStatus").textContent = "";
    $("appModalMsg").textContent = "";
    $("appFilesInput").value = "";
    $("appNameInput").value = "";
    $("appModal").hidden = false;
    $("appNameInput").focus();
  }

  function openAddFilesModal() {
    if (!state.appId) return;
    appModalMode = "addfiles";
    $("appModalTitle").textContent = "Add files";
    $("appModalNote").textContent =
      "Choose files to upload. A new analysis is run automatically " +
      "over all files in this application.";
    var lbl = $("appNameInput").closest("label");
    lbl.hidden = true;
    $("appNameInput").hidden = true;
    $("appCreateBtn").textContent = "Upload & analyze";
    $("appFilesStatus").textContent = "";
    $("appModalMsg").textContent = "";
    $("appFilesInput").value = "";
    $("appModal").hidden = false;
    $("appFilesInput").focus();
  }

  function closeAppModal() {
    $("appModal").hidden = true;
  }

  async function submitAppModal() {
    const createBtn = $("appCreateBtn");
    const msg = $("appModalMsg");
    const files = Array.prototype.slice.call($("appFilesInput").files || []);
    const name = $("appNameInput").value.trim();
    if (appModalMode === "create" && !name) {
      msg.textContent = "An application name is required.";
      return;
    }
    if (appModalMode === "addfiles" && !files.length) {
      msg.textContent = "Choose at least one file to upload.";
      return;
    }
    createBtn.disabled = true;
    msg.textContent = "Working…";
    try {
      let res;
      if (appModalMode === "addfiles") {
        const fd = new FormData();
        files.forEach(function (f) { fd.append("files", f); });
        res = await api("/api/v1/applications/" + state.appId + "/files", { method: "POST", body: fd });
      } else if (files.length) {
        const fd = new FormData();
        fd.append("application_name", name);
        files.forEach(function (f) { fd.append("files", f); });
        res = await api("/api/v1/analyze", { method: "POST", body: fd });
      } else {
        res = await api("/api/v1/applications", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: name }),
        });
      }
      closeAppModal();
      await boot();
      const appId = res.analysis_id ? res.application_id : res.id;
      if (appId) selectApp(appId);
    } catch (e) {
      createBtn.disabled = false;
      msg.textContent = "Failed: " + e.message;
    }
  }

  /* ---------- navigation ---------- */

  async function boot() {
    if (!getKey()) { showNoKey(); openKeyModal(); return; }
    try {
      const data = await api("/api/v1/applications");
      state.apps = data.applications || [];
      renderAppNav();
      if (state.apps.length) selectApp(state.apps[0].id);
      else {
        $("addFilesBtn").disabled = true;
        $("emptyBanner").innerHTML =
          "No applications yet. Use <b>New application</b> above to create one.";
      }
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
        " · " + a.analysis_count + (a.analysis_count === 1 ? " analysis" : " analyses") +
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
    $("addFilesBtn").disabled = false;
    renderAppNav();
    try {
      state.appDetail = await api("/api/v1/applications/" + id);
      renderAppHeader();
      renderAnalysesNav();
      const list = analysisList();
      if (list.length) selectAnalysis(list[0].id);
      else $("emptyBanner").innerHTML =
        '<div class="empty">No analyses yet for this application — use <b>Add files</b> to upload and analyze.</div>';
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
  function fmtNum(n) {
    const v = Number(n);
    if (!isFinite(v)) return String(n);
    return Math.abs(v) >= 1000 || (v !== 0 && Math.abs(v) < 0.01)
      ? v.toLocaleString(undefined, { maximumFractionDigits: 2 })
      : v.toLocaleString(undefined, { maximumFractionDigits: 4 });
  }

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
    const all = analysisList().slice(0, 30).reverse();
    const list = all.filter(function (a) { return a.summary; });
    if (list.length < 2) {
      box.innerHTML = '<div class="muted">Add more analyses over time to see trends here.</div>';
      return;
    }
    const quality = list.map(function (a) {
      return { label: fmtWhen(a.created_at), value: a.summary.data_quality_score };
    }).filter(function (p) { return p.value !== null && p.value !== undefined; });
    const rows = list.map(function (a) {
      return { label: fmtWhen(a.created_at), value: a.summary.total_rows || 0 };
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
    if (rows.length) {
      const maxR = rows.reduce(function (s, r) { return Math.max(s, Number(r.value) || 0); }, 0);
      C.hBar($("trendR"), rows.map(function (r) {
        const v = Number(r.value) || 0;
        return { label: r.label, value: v, pct: maxR ? Math.max(0, Math.min(100, (v / maxR) * 100)) : 0 };
      }), { max: maxR });
    }
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
    $("emptyBanner").innerHTML = "";
    renderTrend();
    const r = analysis.report || {};
    const s = analysis.summary || {};
    renderSummary(s, r);
    renderFiles(r.files || []);
    // actions target the selected analysis
  }

  function renderSummary(s, r) {
    const box = $("summaryBox");
    const catsObj = (s.categories_detected && typeof s.categories_detected === "object" && !Array.isArray(s.categories_detected))
      ? s.categories_detected : {};
    const catItems = Array.isArray(s.categories_detected)
      ? s.categories_detected.map(function (k) { return { label: k, value: 1 }; })
      : Object.keys(catsObj).map(function (k) { return { label: k, value: (catsObj[k] || []).length }; });
    const html =
      kpi("Files", s.file_count) +
      kpi("Total rows", fmtRows(s.total_rows)) +
      kpi("Quality score", s.data_quality_score === null || s.data_quality_score === undefined ? "—" : s.data_quality_score + " / 100", dqClass(s.data_quality_score)) +
      kpi("Findings", s.findings_count);
    box.innerHTML = html;
    const catBox = $("categoryBox");
    catBox.innerHTML = "";
    if (catItems.length) C.donut(catBox, catItems, { center: catItems.length, centerLabel: "categories" });
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
    const size = f.size_bytes ? " · " + fmtBytes(f.size_bytes) : "";
    const sha = f.sha256 ? '<span class="file-sha" title="sha256 ' + esc(f.sha256) + '">' + esc(f.sha256.slice(0, 8)) + "</span>" : "";
    const notes = (f.notes || []).map(function (n) { return "<div class='note'>" + esc(n) + "</div>"; }).join("");
    return '<div class="file-head">' +
      "<h4>" + (idx + 1) + ". " + esc(f.filename) + "</h4>" +
      '<span class="file-meta">' + esc(f.file_type || "") + " · " + fmtRows(f.row_count) + " rows · " +
      (f.columns ? f.columns.length : 0) + " cols" + size + (f.multi_sheet ? " · multi-sheet" : "") + "</span>" +
      '<span class="file-tags">' + tag + sha + "</span>" +
      '<div class="file-notes">' + notes + "</div>" +
      "</div>";
  }

  function fileDetail(f) {
    const wrap = document.createElement("div");

    const grid = document.createElement("div");
    grid.className = "charts-grid";
    grid.appendChild(block("Category probability", f.categories, "barrel"));
    grid.appendChild(block("Data quality", null, "quality", f));
    wrap.appendChild(grid);

    const domainEl = domainSection(f);
    if (domainEl) wrap.appendChild(domainEl);

    const insightEl = insightSection(f);
    if (insightEl) wrap.appendChild(insightEl);

    const acc = document.createElement("details");
    acc.className = "col-profile";
    acc.innerHTML = "<summary>Column profiles</summary>";
    const ab = document.createElement("div");
    ab.className = "acc-body";
    ab.appendChild(sectionHtml("Column profile", ""));
    const pgrid = document.createElement("div");
    pgrid.className = "charts-grid";
    pgrid.appendChild(block("Null share", null, "nulls", f));
    pgrid.appendChild(block("Unique share", null, "uniques", f));
    ab.appendChild(pgrid);
    const numProf = numberProfile(f);
    const catProf = categoricalProfile(f);
    if (numProf) ab.appendChild(numProf);
    if (catProf) ab.appendChild(catProf);
    acc.appendChild(ab);
    wrap.appendChild(acc);

    const details = document.createElement("details");
    details.className = "rawjson";
    details.innerHTML = "<summary>Raw report JSON</summary><pre>" + esc(JSON.stringify(f, null, 2)) + "</pre>";
    wrap.appendChild(details);

    const tools = dataTools(f);
    if (tools) wrap.appendChild(tools);
    return wrap;
  }

  /* ---------- calculated insights ---------- */

  const INSIGHT_FAMILY_ORDER = ["profitability", "trend", "concentration", "waste"];
  const INSIGHT_FAMILY_TITLES = {
    profitability: "Profitability", trend: "Trends",
    concentration: "Concentration", waste: "Waste & stock risk", general: "Insights",
  };

  function insightSection(f) {
    const list = Array.isArray(f.insights) ? f.insights : [];
    if (!list.length) return null;
    const ok = list.filter(function (i) { return i.status === "ok"; });
    const notes = list.filter(function (i) { return i.status !== "ok"; });

    const sec = document.createElement("div");
    sec.className = "section-block";
    sec.innerHTML = "<h4>Calculated insights</h4>";

    const extra = [];
    ok.forEach(function (i) {
      const fam = i.family || "general";
      if (INSIGHT_FAMILY_ORDER.indexOf(fam) === -1 && extra.indexOf(fam) === -1) extra.push(fam);
    });
    INSIGHT_FAMILY_ORDER.concat(extra).forEach(function (fam) {
      const items = ok.filter(function (i) { return (i.family || "general") === fam; });
      if (!items.length) return;
      const head = document.createElement("div");
      head.className = "insight-group";
      head.textContent = INSIGHT_FAMILY_TITLES[fam] || fam;
      sec.appendChild(head);
      const grid = document.createElement("div");
      grid.className = "insight-grid";
      items.forEach(function (it) { grid.appendChild(insightCard(it)); });
      sec.appendChild(grid);
    });

    if (notes.length) {
      const det = document.createElement("details");
      det.className = "insight-notes";
      det.innerHTML = "<summary>Not calculated (" + notes.length + ")</summary>";
      const ul = document.createElement("ul");
      notes.forEach(function (n) {
        const li = document.createElement("li");
        li.className = "insight-note" + (n.status === "error" ? " err" : "");
        li.innerHTML = "<strong>" + esc(n.label || n.key || "") + "</strong> — " +
          esc(n.detail || "") +
          ((n.missing_columns && n.missing_columns.length)
            ? ' <span class="insight-missing">needs: ' + esc(n.missing_columns.join(", ")) + "</span>"
            : "");
        ul.appendChild(li);
      });
      det.appendChild(ul);
      sec.appendChild(det);
    }
    return sec;
  }

  function insightCard(it) {
    const card = document.createElement("div");
    card.className = "insight-card " + (it.severity || "info");
    card.innerHTML =
      '<div class="insight-name">' + esc(it.label || "") + "</div>" +
      '<div class="insight-value ' + (it.severity || "info") + '">' +
        esc(fmtInsightValue(it.value, it.unit)) + "</div>" +
      (it.detail ? '<div class="insight-detail">' + esc(it.detail) + "</div>" : "");
    if (it.evidence && Object.keys(it.evidence).length) {
      const rows = insightEvidence(it.evidence);
      if (rows.length) {
        const dl = document.createElement("dl");
        dl.className = "insight-ev";
        rows.forEach(function (r) {
          const dt = document.createElement("dt");
          dt.textContent = r[0];
          const dd = document.createElement("dd");
          dd.textContent = r[1];
          dl.appendChild(dt);
          dl.appendChild(dd);
        });
        card.appendChild(dl);
      }
    }
    return card;
  }

  function fmtInsightValue(value, unit) {
    if (value === null || value === undefined || value === "") return "—";
    const n = Number(value);
    if (!isFinite(n)) return String(value);
    if (unit === "percent") return n.toFixed(2) + "%";
    if (unit === "currency") return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (unit === "ratio") return n.toFixed(2) + "x";
    if (["index", "count", "rows", "products", "units"].indexOf(unit) !== -1) {
      return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
    }
    return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }

  function insightEvidence(ev) {
    const rows = [];
    Object.keys(ev).forEach(function (k) {
      if (k === "top" || k === "buckets" || k === "products") return;
      const v = ev[k];
      if (v === null || v === undefined || v === "") return;
      if (typeof v === "object") return;
      rows.push([k.replace(/_/g, " "), typeof v === "number" ? fmtNum(v) : String(v)]);
    });
    if (ev.top && ev.top.length) {
      rows.push(["worst", ev.top.slice(0, 3).map(function (t) {
        return (t.product || t.month || "?") + " (" + fmtNum(t.value) + ")";
      }).join(", ")]);
    }
    if (ev.buckets) {
      const b = ev.buckets;
      rows.push(["buckets", "expired " + (b.expired || 0) + " · ≤30d " + (b["30"] || 0) +
        " · ≤90d " + (b["90"] || 0) + " · ≤180d " + (b["180"] || 0)]);
    }
    return rows.slice(0, 6);
  }

  /* ---------- data preview + forecast tools ---------- */

  function dataTools(f) {
    const fid = f.file_id || f.id;
    if (!fid) return null;
    const details = document.createElement("details");
    details.className = "data-tools";
    details.innerHTML =
      '<summary>Data sources &amp; forecast</summary>' +
      '<div class="dt-loading">Loading live preview…</div>';
    details.addEventListener("toggle", function () {
      if (details.open && !details.dataset.loaded) {
        details.dataset.loaded = "1";
        setupDataTools(details, fid);
      }
    });
    return details;
  }

  async function setupDataTools(details, fid) {
    const body = details.querySelector(".dt-loading");
    body.classList.remove("dt-loading");
    body.className = "dt-body";
    let info;
    try {
      info = await api("/api/v1/files/" + encodeURIComponent(fid) + "/preview?rows=" + PREVIEW_PAGE_ROWS);
    } catch (e) {
      body.innerHTML = '<div class="dt-err">Could not load preview: ' + esc(e.message) + "</div>";
      return;
    }
    body.innerHTML =
      '<div class="dt-head">' +
      '<strong>' + esc(info.filename) + "</strong>" +
      '<span class="dt-sub">' + esc(info.file_type) + " · " + fmtRows(info.row_count) + " rows · " +
      esc((info.sheets && info.sheets.length) ? info.sheets.join(", ") : "") + "</span></div>" +
      '<div class="dt-grid"><div class="dt-pane">' + previewPane(info) + "</div>" +
      '<div class="dt-pane">' + forecastPane(info) + "</div></div>";
    bindPreviewGrid(body, fid, info);
    bindForecast(body, fid, info);
  }

  function previewTableHtml(info) {
    const sample = info.sample_rows || [];
    const cols = (info.columns || []).map(function (c) { return c.name; });
    if (!sample.length) return '<span class="muted">no rows to preview</span>';
    return '<div class="dt-table-wrap"><table class="dt-table"><tr>' +
      cols.map(function (c) { return "<th>" + esc(c) + "</th>"; }).join("") + "</tr>" +
      sample.map(function (r) {
        return "<tr>" + cols.map(function (c) {
          const v = r[c];
          const full = v === null || v === undefined ? "·" : esc(String(v));
          const cell = full.length > CELL_CLIP ? full.slice(0, CELL_CLIP) + "…" : full;
          return '<td title="' + full + '">' + cell + "</td>";
        }).join("") + "</tr>";
      }).join("") + "</table></div>";
  }

  function previewPane(info) {
    const total = Number(info.row_count || 0);
    const offset = Number(info.offset || 0);
    let html =
      '<div class="dt-pane-title">Preview</div>' +
      '<div class="dt-cols">' +
      (info.columns || []).map(function (c) {
        return '<span class="pill dt-col dt-kind-' + esc(c.kind) + '" title="nulls: ' + fmtRows(c.null_count) + '">' +
          esc(c.name) + " · " + esc(c.kind) + "</span>";
      }).join("") +
      "</div>";
    if ((info.sheets || []).length > 1) {
      html += '<div class="dt-row"><label>Sheet</label><select class="dt-sheet">' +
        info.sheets.map(function (s) {
          return '<option' + (s === info.sheet ? " selected" : "") + ">" + esc(s) + "</option>";
        }).join("") + "</select></div>";
    }
    html += previewTableHtml(info);
    const start = Math.min(offset + 1, total);
    const end = Math.min(offset + PREVIEW_PAGE_ROWS, total);
    html += '<div class="dt-pager">' +
      '<button class="btn p-prev"' + (offset <= 0 ? " disabled" : "") + ">‹ Prev</button>" +
      '<span class="p-info">' + (total ? "Rows " + fmtRows(start) + "–" + fmtRows(end) + " of " + fmtRows(total) : "0 rows") + "</span>" +
      '<button class="btn p-next"' + (end >= total ? " disabled" : "") + ">Next ›</button>" +
      "</div>";
    return html;
  }

  function bindPreviewGrid(body, fid, info) {
    const prevBtn = body.querySelector(".p-prev");
    const nextBtn = body.querySelector(".p-next");
    const pager = body.querySelector(".p-info");
    if (!pager) return;
    const state2 = { offset: Number(info.offset || 0), rows: PREVIEW_PAGE_ROWS };
    const tableBox = body.querySelector(".dt-table-wrap");

    async function go(offset) {
      state2.offset = Math.max(0, offset);
      pager.textContent = "Loading…";
      try {
        const url = "/api/v1/files/" + encodeURIComponent(fid) + "/preview?rows=" + state2.rows +
          "&offset=" + state2.offset +
          ((info.sheets || []).length > 1 && info.sheet ? "&sheet=" + encodeURIComponent(info.sheet) : "");
        const next = await api(url);
        const total = Number(next.row_count || 0);
        const end = Math.min(state2.offset + state2.rows, total);
        const start = Math.min(state2.offset + 1, total);
        tableBox.innerHTML = "";
        tableBox.appendChild(tableElement(next));
        pager.textContent = "Rows " + fmtRows(start) + "–" + fmtRows(end) + " of " + fmtRows(total);
        prevBtn.disabled = state2.offset <= 0;
        nextBtn.disabled = end >= total;
      } catch (e) {
        pager.textContent = "Could not load page: " + e.message;
      }
    }

    prevBtn.addEventListener("click", function () { go(state2.offset - state2.rows); });
    nextBtn.addEventListener("click", function () { go(state2.offset + state2.rows); });
  }

  function tableElement(info) {
    // build a fresh <div class="dt-table-wrap"><table>… for a page payload
    const wrap = document.createElement("div");
    wrap.className = "dt-table-wrap";
    const sample = info.sample_rows || [];
    const cols = (info.columns || []).map(function (c) { return c.name; });
    if (!sample.length) {
      wrap.appendChild(document.createTextNode("no rows"));
      return wrap;
    }
    const table = document.createElement("table");
    table.className = "dt-table";
    table.appendChild(rowsHtml("<tr>" + cols.map(function (c) { return "<th>" + esc(c) + "</th>"; }).join("") + "</tr>"));
    sample.forEach(function (r) {
      const tr = document.createElement("tr");
      cols.forEach(function (c) {
        const v = r[c];
        const full = v === null || v === undefined ? "·" : esc(String(v));
        const cellText = full.length > CELL_CLIP ? full.slice(0, CELL_CLIP) + "…" : full;
        const td = document.createElement("td");
        td.textContent = cellText;
        if (full.length > CELL_CLIP) td.title = r[c];
        tr.appendChild(td);
      });
      table.appendChild(tr);
    });
    wrap.appendChild(table);
    return wrap;
  }

  function rowsHtml(html) {
    const t = document.createElement("template");
    t.innerHTML = html;
    return t.content.firstChild;
  }

  function forecastPane(info) {
    const dateCols = [];
    const numCols = [];
    const otherCols = [];
    (info.columns || []).forEach(function (c) {
      if (c.kind === "date") dateCols.push(c.name);
      else if (c.kind === "number") numCols.push(c.name);
      else otherCols.push(c.name);
    });
    const rec = info.recommended || {};
    const dateOpts = ([""].concat(dateCols)).map(function (c) {
      const label = c ? esc(c) : "(autodetect)";
      return '<option value="' + esc(c) + '"' + (c === rec.date_col ? " selected" : "") + ">" + label + "</option>";
    }).join("");
    const valOpts = ["(count rows)"].concat(numCols).map(function (c) {
      const label = c ? esc(c) : "(count rows)";
      return '<option' + (c === rec.value_col ? " selected" : "") + '>' + label + "</option>";
    }).join("");
    return '<div class="dt-pane-title">Forecast</div>' +
      '<div class="dt-controls">' +
      '<label>Date column<select class="f-date">' + dateOpts + "</select></label>" +
      '<label>Value column<select class="f-value">' + valOpts + "</select></label>" +
      '<label>Aggregation<select class="f-agg"><option>sum</option><option>mean</option><option>count</option></select></label>' +
      '<label>Horizon (days)<input class="f-horizon" type="number" min="1" max="365" value="14"></label>' +
      "</div>" +
      '<div class="dt-actions"><button class="btn f-run" disabled>Run forecast</button></div>' +
      '<div class="f-result"></div>';
  }

  function bindForecast(body, fid, info) {
    // re-fetch preview when the sheet changes (new columns may appear)
    const sheetSel = body.querySelector(".dt-sheet");
    if (sheetSel) {
      sheetSel.addEventListener("change", async function () {
        try {
          const next = await api("/api/v1/files/" + encodeURIComponent(fid) + "/preview?rows=" + PREVIEW_PAGE_ROWS + "&sheet=" + encodeURIComponent(sheetSel.value));
          const grid = body.querySelector(".dt-grid");
          grid.innerHTML = '<div class="dt-pane">' + previewPane(next) + "</div>" +
            '<div class="dt-pane">' + forecastPane(next) + "</div>";
          bindPreviewGrid(body, fid, next);
          bindForecast(body, fid, next);
        } catch (e) {
          body.querySelector(".f-result").innerHTML = '<div class="dt-err">' + esc(e.message) + "</div>";
        }
      });
      return;
    }
    const dateSel = body.querySelector(".f-date");
    const valSel = body.querySelector(".f-value");
    const aggSel = body.querySelector(".f-agg");
    const horiz = body.querySelector(".f-horizon");
    const runBtn = body.querySelector(".f-run");
    const result = body.querySelector(".f-result");

    const hasDate = (dateSel.options.length > 1);
    runBtn.disabled = !hasDate;
    if (!hasDate) {
      result.innerHTML = '<div class="dt-msg">No date/time column detected in this file — forecasting needs one.</div>';
      return;
    }

    // "(count rows)" selected -> force count aggregation
    valSel.addEventListener("change", function () {
      if (valSel.value === "(count rows)") aggSel.value = "count";
    });

    async function run() {
      const q = {
        date_col: dateSel.value || "",
        value_col: valSel.value === "(count rows)" ? "" : valSel.value,
        agg: aggSel.value,
        horizon: (parseInt(horiz.value, 10) || 14),
      };
      const qs = Object.keys(q).map(function (k) { return q[k] ? k + "=" + encodeURIComponent(q[k]) : ""; }).filter(Boolean).join("&");
      result.innerHTML = '<div class="dt-msg">Forecasting…</div>';
      try {
        const payload = await api("/api/v1/files/" + encodeURIComponent(fid) + "/forecast" + (qs ? "?" + qs : ""));
        renderForecast(result, payload);
      } catch (e) {
        result.innerHTML = '<div class="dt-err">' + esc(e.message) + "</div>";
      }
    }

    runBtn.addEventListener("click", run);
    [dateSel, valSel, aggSel, horiz].forEach(function (el) {
      el.addEventListener("change", run);
    });
    run();
  }

  function renderForecast(result, payload) {
    const m = payload.meta || {};
    const method = esc(payload.method_description || payload.method);
    const chart = document.createElement("div");
    chart.className = "chart";
    const stats = '<div class="dt-sub">method: <strong>' + method + "</strong> · agg: <strong>" +
      esc(m.agg) + "</strong> · points: " + fmtRows(m.points) + " · " +
      esc(m.date_column) + " → " + esc(m.value_column || "row count") + "</div>";
    const holdout = payload.diagnostics && payload.diagnostics.holdout !== null
      ? " · evaluated on " + payload.diagnostics.holdout + "-day holdout"
      : "";
    result.innerHTML = stats + holdout;
    result.appendChild(chart);
    C.forecast(chart, payload);
  }

  function sectionHtml(title, inner) {
    const d = document.createElement("div");
    d.className = "section-block";
    d.innerHTML = "<h4>" + esc(title) + "</h4>";
    if (typeof inner === "string") d.insertAdjacentHTML("beforeend", inner);
    else if (inner) d.appendChild(inner);
    return d;
  }

  function fmtFinding(x) {
    const s = String(x);
    const eq = s.indexOf("=");
    if (eq === -1) return "<li>" + esc(s) + "</li>";
    const check = s.slice(0, eq).trim();
    const rest = s.slice(eq + 1).trim();
    const sp = rest.indexOf(" ");
    const status = sp === -1 ? rest : rest.slice(0, sp);
    const detail = sp === -1 ? "" : rest.slice(sp + 1).trim();
    const cls = /^fail$/i.test(status) ? "pill-fail" : /^warn/i.test(status) ? "pill-warn" : "pill-ok";
    return "<li><b>" + esc(check) + "</b> <span class='pill " + cls + "'>" + esc(status) + "</span>" +
      (detail ? "<span class='f-detail'>" + esc(detail) + "</span>" : "") + "</li>";
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
      C.hBar(cbox, data ? Object.keys(data).map(function (k) {
        const v = Number(data[k]) || 0;
        return { label: k, value: v, pct: Math.max(0, Math.min(100, v * 100)) };
      }).sort(function (a, b) { return b.value - a.value; }) : []);
    } else if (kind === "quality") {
      const dq = (f.data_quality || {});
      C.gauge(cbox, dq.score);
      const findings = (f.quality_findings || []).filter(function (x) {
        return x && !/^[a-z_]+ *= *pass(\b|$)/.test(String(x));
      });
      extra.innerHTML = findings.length
        ? "<ul class='findings'>" + findings.map(fmtFinding).join("") + "</ul>"
        : '<div class="muted">All checks passed.</div>';
    } else if (kind === "nulls") {
       const rows = (f.profile && f.profile.column_profiles || [])
         .map(function (c) { return { label: c.name, value: Number(c.null_pct) || 0, pct: Number(c.null_pct) || 0 }; });
       const maxN = rows.reduce(function (s, x) { return Math.max(s, x.value); }, 0);
       C.hBar(cbox, rows, { max: maxN });
     } else if (kind === "uniques") {
       const rows = (f.profile && f.profile.column_profiles || [])
         .map(function (c) { return { label: c.name, value: Number(c.unique_pct) || 0, pct: Number(c.unique_pct) || 0 }; });
       const maxU = rows.reduce(function (s, x) { return Math.max(s, x.value); }, 0);
       C.hBar(cbox, rows, { max: maxU });
    }
    return d;
  }

  /* ----- domain chart extraction ----- */

  function domainData(f) {
    const da = f.domain_analytics || {};
    const out = { kpis: [], bars: [], donuts: [], lines: [], vbars: [], tables: [], notes: [] };
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
        const v0 = v[0];
        if (v0 && typeof v0 === "object" && v0.value !== undefined && v0.count !== undefined) {
          const items = v.filter(function (x) { return x && typeof x === "object"; }).map(function (r) { return { label: r.value, value: r.count }; });
          out.bars.push({ title: key.replace(/_/g, " "), items: items });
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
    if (kind === "dbar") {
      const items = (data.items || []).filter(function (x) { return x && typeof x === "object"; }).map(function (x) { return { label: x.label, value: Number(x.value) || 0 }; });
      const maxB = items.reduce(function (s, x) { return Math.max(s, x.value); }, 0);
      C.hBar(cbox, items.map(function (x) { return { label: x.label, value: x.value, pct: maxB ? Math.max(0, Math.min(100, (x.value / maxB) * 100)) : 0 }; }), { max: maxB });
    } else if (kind === "ddonut") C.donut(cbox, data.items || []);
    else if (kind === "dline") C.line(cbox, data.items || []);
    else if (kind === "dvbar") C.vBars(cbox, data || {});
    else if (kind === "dtable") {
      blk.innerHTML = "<h4>" + esc(title) + "</h4>" + tableHtml(data.headers || [], data.rows || []);
    }
    return blk;
  }

  function domainSection(f) {
    const dd = domainData(f);
    const hasContent = !!(dd.kpis.length || dd.bars.length || dd.donuts.length ||
      dd.lines.length || dd.vbars.length || dd.tables.length);
    if (!hasContent) {
      if (!dd.notes.length) return null;
      const p = document.createElement("div");
      p.className = "note";
      p.textContent = "Domain analytics · " + (f.top_category || "—") + " — " + dd.notes.join("; ");
      return p;
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
    if (!cols.length) return null;
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
    if (!cols.length) return null;
    const top = cols.slice(0, 4);
    const d = document.createElement("div");
    d.className = "charts-grid";
    top.forEach(function (c) {
      const blk = document.createElement("div");
      blk.className = "chart-block";
      blk.innerHTML = "<h4>Top values · " + esc(c.name) + "</h4><div class='chart'></div>";
      d.appendChild(blk);
      const tv = (c.top_values || []).slice(0, 10).filter(function (t) { return t && typeof t === "object"; }).map(function (t) { return { label: t.value, value: t.count || 0 }; });
      const maxTv = tv.reduce(function (s, x) { return Math.max(s, x.value); }, 0);
      C.hBar(blk.querySelector(".chart"), tv.map(function (x) { return { label: x.label, value: x.value, pct: maxTv ? Math.max(0, Math.min(100, (x.value / maxTv) * 100)) : 0 }; }), { max: maxTv });
    });
    return d;
  }

  /* ---------- actions ---------- */

  function actions() {
    $("openReportBtn").addEventListener("click", function () {
      const appId = state.appId, aid = state.analysisId;
      if (!appId || !aid) return;
      const reportUrl =
        "/api/v1/applications/" + appId + "/analyses/" + aid + "/report?format=html";
      const w = window.open("", "_blank");
      if (!w) { alert("Pop-up blocked — allow pop-ups for the report."); return; }
      w.document.open();
      w.document.write(
        "<!doctype html><html><head><title>Loading report…</title></head>" +
        "<body style='font-family:sans-serif;padding:2rem'>Loading report…</body></html>"
      );
      w.document.close();
      fetch(reportUrl, { headers: { "X-API-Key": getKey() } })
        .then(async function (res) {
          if (res.status === 401) { w.close(); openKeyModal(); return; }
          if (!res.ok) {
            const detail = await res.text();
            w.document.body.innerHTML = "Could not load report (" + res.status + "):<br>" + esc(detail);
            return;
          }
          const html = await res.text();
          w.document.open();
          w.document.write(html);
          w.document.close();
          // document.write leaves the tab on about:blank, so the address bar
          // showed the dashboard URL and the report could not be refreshed,
          // bookmarked or reloaded. Point the tab at the real report URL.
          try {
            w.history.replaceState(null, "", reportUrl);
          } catch (e) {
            /* cross-origin or blocked: the report still renders */
          }
          w.focus();
        })
        .catch(function (e) {
          w.document.body.innerHTML = "Could not load report: " + esc(e.message);
        });
    });
    $("dlJsonBtn").addEventListener("click", async function () {
      const appId = state.appId, aid = state.analysisId;
      if (!appId || !aid) return;
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
    if (String(e.message).includes("401")) { openKeyModal(); return; }
    const box = $("emptyBanner");
    if (box) box.innerHTML = '<div class="empty">Error: ' + esc(e.message) + "</div>";
    else console.error("SiroQ dashboard error:", e);
  }

  let resizeTimer = null;
  function onResize() {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () {
      if (state.analysis) renderMain();
    }, 220);
  }

  /* ---------- build freshness ---------- */

  function hardReload() {
    // location.reload() can be served from cache; ask for the network copy
    try { location.replace(location.pathname + "?build=" + Date.now()); }
    catch (e) { location.reload(); }
  }

  function checkBuildFreshness() {
    const meta = document.querySelector('meta[name="siroq-build"]');
    const loaded = meta && meta.getAttribute("content");
    return fetch("/dashboard/build.json", { cache: "no-store" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (loaded && d.build && d.build !== loaded) {
          const banner = $("staleBanner");
          if (banner) banner.hidden = false;
        }
      })
      .catch(function () { /* offline: keep the current build */ });
  }

  document.addEventListener("DOMContentLoaded", function () {
    actions();
    boot();
    checkBuildFreshness();
    setInterval(checkBuildFreshness, 60000);
    window.addEventListener("resize", onResize);
    $("reloadBtn").addEventListener("click", boot);
    $("staleReloadBtn").addEventListener("click", hardReload);
    $("keyBtn").addEventListener("click", function () { openKeyModal(); });
    $("keySaveBtn").addEventListener("click", saveKey);
    $("keyCancelBtn").addEventListener("click", closeKeyModal);
    $("keyInput").addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") saveKey();
      if (ev.key === "Escape") closeKeyModal();
    });
    $("newAppBtn").addEventListener("click", openCreateModal);
    $("addFilesBtn").addEventListener("click", openAddFilesModal);
    $("appCreateBtn").addEventListener("click", submitAppModal);
    $("appCancelBtn").addEventListener("click", closeAppModal);
    $("appNameInput").addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") submitAppModal();
      if (ev.key === "Escape") closeAppModal();
    });
    $("appModal").addEventListener("click", function (ev) {
      if (ev.target === $("appModal")) closeAppModal();
    });
    $("appFilesInput").addEventListener("change", function (ev) {
      const n = ev.target.files ? ev.target.files.length : 0;
      $("appFilesStatus").textContent =
        n ? n + (n === 1 ? " file selected" : " files selected") : "";
    });
  });
})();