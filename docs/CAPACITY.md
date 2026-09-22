# Capacity Sizing: File Load & Resources

How much upload/analysis load this service can carry, and what hardware it needs.
All numbers are **measured on this codebase** (direct calls into the same
`ingestion` / `pipeline` code the HTTP endpoints use), not theoretical.

## 1. Reference machine (measurement baseline)

| Spec | Value |
|---|---|
| CPU | Intel Core i3-4030U, 2 cores / 4 threads @ 1.9 GHz |
| RAM | 3.7 GB (2.4 GB in use during runs) |
| OS | Linux |

Scale the per-core numbers by **≈ 2× on a modern server CPU** (3 GHz+ Xeon/EPYC
or a current cloud vCPU of comparable class). Everything below is expressed per
"worker" (= one uvicorn process, one analysis at a time) so it scales linearly
with cores.

## 2. What one request costs (measured)

`POST /applications/{id}/analyze` (and `/analyze`) run the whole pipeline
synchronously in the request thread: **read → profile → classify → quality →
domain engines**, all in-process.

### 2.1 Real files (production samples shipped with the repo)

| file | on disk | rows | cols | read | analyze | wall, 1 file | peak RSS |
|---|---|---:|---:|---:|---:|---:|---:|
| siroq.xlsx | 0.18 MB | 626 | 9 | 0.30 s | 0.29 s | ~0.9 s | ~86 MB |
| 111.xls | 0.69 MB | 1 750 | 9 | 0.55 s | 0.75 s | ~1.5 s | ~98 MB |
| siroq 22.xls | 0.89 MB | 2 270 | 9 | 0.55 s | 0.91 s | ~1.7 s | ~103 MB |
| PURCHASE -ABDELHAMID.xls | 3.2 MB | 5 182 | 13 | 1.8 s | 1.40 s | ~3.2 s | ~154 MB |
| **4-file app** | **5.0 MB** | **9 828** | — | 3.0 s | 3.4 s | **~6.4 s** | **~154 MB** |

### 2.2 Scaled synthetic CSV (16 "sales-like" columns, clean typed data)

| rows | on disk | read | analyze | peak RSS |
|---|---:|---:|---:|---:|---:|
| 50 k | 5.5 MB | 0.20 s | **11.6 s** | ~119 MB |
| 250 k | 27.6 MB | 0.75 s | **57.3 s** | ~250 MB |
| 1 000 k | 110 MB | 3.5 s | **233 s** | ~717 MB |

### 2.3 What the numbers mean

- **Analyze cost is ~linear in (rows × columns).** Clean CSV ≈ **0.23 ms/row** at
  16 columns (≈ 15 µs/row/column). Real-world XLS is messier (mixed types,
  Arabic header sanitization, rapidfuzz classification) ≈ 0.3–0.5 ms/row.
  Analyze dominates the whole request for anything over a few thousand rows.
- **Reading is the fixed-cost ticket.** CSV parse ≈ 3.5 ms / 1 000 rows; Excel
  (calamine) has a **~0.3–0.6 s floor per file** + slow per-row cost
  (~0.25–0.4 ms/row). A 3 MB XLS pays ~2 s just to open.
- **Memory scales ~0.6 KB/row** (16 cols) on top of a **~90 MB baseline**
  (Python interpreter + pandas + calamine + app). Peak working set ≈ 5–6 × the
  on-disk CSV size.
- **Peak RSS in the 4-file/5 MB app is only 154 MB** — files are analyzed
  sequentially, so memory tracks the *largest single file*, not the batch.

## 3. Cost model (for planning)

For a request of `R` rows / `C` columns (clean, CSV-like), per worker:

```
t_read   ≈ 0.2 s  + R × 3.5 µs          (CSV)   |  0.6 s + R × 0.3 ms (XLS/XLSX)
t_analyze≈ R × 15 µs × C
t_http   ≈ 0.3 s  (auth, validation, DB row, response render)
t_total  ≈ t_http + t_read + R × 15 µs × C
peak_RAM≈ 90 MB + R × 0.6 KB            (per concurrent worker)
```

Throughput ceiling per worker (its analyze-bound asymptote): **`3600 / (R × 15 µs × C)` files/hr**,
i.e. **~15 M rows/hr/worker at 16 columns** — beyond that rows get more expensive
only by upload-capped request size, not by rate.

### Worked throughput, one worker (model: `t_total ≈ 0.3 + 0.2 + R×3.5µs + R×0.24 ms` for 16-col CSV)

| inventory / file | rows | t_total | files/hr | rows/hr | peak RAM |
|---|---:|---:|---:|---:|---:|
| tiny CSV | 1 k | 0.74 s | ~4 800 | 4.8 M | ~91 MB |
| small CSV | 5 k | 1.7 s | ~2 100 | 10.5 M | ~93 MB |
| medium CSV | 50 k | 12.7 s | ~284 | 14.2 M | ~120 MB |
| large CSV | 250 k | 61.4 s | ~59 | 14.7 M | ~240 MB |
| cap-sized | 450 k | 110 s | ~33 | 14.7 M | ~360 MB |

For real XLS files add the calamine floor (~0.5 s) and the messier per-row cost
(0.3–0.5 ms/row): a 5 k-row XLS lands at ~2.2–2.6 s (~1 500 files/hr → 7.5 M
rows/hr). Note rows/hr plateaus around **15 M/worker** — the service is
throughput-bound by analysis time, which is row-linear, not by file handling.

## 4. Hard limits already in the code (config.py)

| limit | value |
|---|---|
| `MAX_FILE_BYTES` | **50 MB / file** |
| `MAX_FILES_PER_REQUEST` | **25 files / request** |
| Redis/queue | none — endpoints are synchronous |

The 50 MB cap bounds a single file to roughly **450 k rows (CSV)**, keeping peak
RSS per file ≈ 360 MB. A maximal request (25 × 50 MB) is 1.25 GB — plan burst
memory for that if your clients use it.

## 5. Scenarios: what each box size handles

Multipliers: one analysis at a time per core (CPU-bound, pandas/GIL), so
**workers ≈ cores**. RAM headroom factor 0.7. Reference CPU is the 1.9 GHz
laptop; ×2 for a modern server core.

### A. Small / single tenant — 2 vCPU, 4 GB
- workers = 2 · usable RAM 2.8 GB → max concurrent peak ≈ 1.4 GB per analysis
  slot (≈ 2.2 M rows) → never memory-limited within the 50 MB cap.
- small CSV (5 k rows): **~4 200 files/hr** (21 M rows/hr)
- medium CSV (50 k rows): **~570 files/hr** (28.4 M rows/hr)
- XLS at 5 k rows: **~3 000 files/hr**
- burst: 1 concurrent 25-file request OK (≤ 720 MB at cap); 2+ maxed requests
  risks swap.

### B. Medium — 4 vCPU, 8 GB  ← recommended default for production
- workers = 4 · cap-sized file peak ≈ 360 MB each → 4 concurrent maxed files =
  1.4 GB ✓ (headroom 0.7 × 8 GB = 5.6 GB).
- small CSV (5 k): **~8 400 files/hr** (42 M rows/hr)
- medium CSV (50 k): **~1 140 files/hr** (57 M rows/hr)
- large CSV (250 k): **~235 files/hr** (59 M rows/hr)

### C. High throughput — 8 vCPU, 16 GB
- workers = 8 · 8 × cap-sized = 2.9 GB ✓
- medium CSV (50 k): **~2 270 files/hr** (114 M rows/hr)
- large CSV (250 k): **~470 files/hr** (118 M rows/hr)

| config | vCPU | RAM | peak files/hr (50 k-row) | limit |
|---|---:|---:|---:|---:|
| A small | 2 | 4 GB | ~570 | rows cap |
| B medium | 4 | 8 GB | ~1 140 | rows cap |
| C high | 8 | 16 GB | ~2 270 | rows cap |

## 6. Storage & database

- **Raw files** are persisted byte-for-byte under `STORAGE_PATH`, keyed by
  **SHA-256 of content** → identical re-uploads reuse one on-disk copy (the
  `files` row is still created). Disk growth ≈ sum of *unique* uploaded bytes:
  at 600 files/hr × 3 MB avg that's **≈ 1.8 GB/hr** of new data sustained.
  Use fast local disk (SSD/NVMe); this write path is sequential and cheap.
- **PostgreSQL** is light relative to raw storage:
  - one `files` row ≈ 2 kb, one `applications` row ≈ 1 kb.
  - one `analyses` run ≈ **~10 KB** (`report` JSON average measured 9.7 KB,
    `summary` 0.2 KB; pg bloat ~2–3 × on update-heavy workloads, but analyses
    are append-only).
  - measured live DB: 4 analyses + 6 files → **16 MB total** including PostGIS.
  - at 600 runs/hr ≈ 6–20 MB/hr into PG. A 30 GB disk holds ~a year of
    high-rate operation plus report history; backups should prioritize the
    **storage tree** (it is the bulk).

## 7. Recommendations (turn this into production config)

1. **Run `uvicorn --workers <cores>`** (the Dockerfile currently uses no
   `--workers`; docker-compose dev stack runs `--reload`, single worker — never
   for prod). Match workers to vCPU; this doubling is the cheapest capacity
   lever and the model above assumes it.
2. Size RAM so **concurrent workers × worst file peak ≤ 0.7 × RAM**, using
   `peak_RAM ≈ 90 MB + rows × 0.6 KB`. At the 50 MB cap that is **~360 MB per
   worker**, so 4 workers comfortably fit 8 GB.
3. **Cap concurrency at the edge.** The endpoints are synchronous — a 250 k-row
   request occupies a worker for ~1 min. Under load, requests queue in FastAPI's
   threadpool (default 40 per worker) and pile up memory before they ever
   analyze. Put an nginx/alb `limit_conn`/worker concurrency cap (e.g. 4–8 per
   process) so the box saturates at analysis speed instead of buffering jobs.
4. **For sustained high file rates, add a background queue.** At >~1 000 files/hr
   sustained, `POST`-and-poll (queue + status row) is better than holding HTTP
   threads; otherwise deploy B/C-class boxes and rely on edge concurrency caps.
5. Keep the **50 MB / 25-file** caps — they are also the memory guardrails.
   Lower `MAX_FILES_PER_REQUEST` if a single client bursts the whole 1.25 GB.
6. Don't co-locate heavy PostGIS work on the app box if you run C-class rates;
   the DB itself is trivial, but shared I/O competes with the storage write
   path.

## 8. How to re-measure on your own hardware

The harness lives in the repo (`bench/`) and runs on Windows as well as Linux —
full walkthrough for a client machine, including PowerShell setup and what
output to send back, is in **`bench/README.md`**.

`bench/bench_runner.py` (mode `ingest`/`analyze`) runs the exact
`ingestion.read_file` / `pipeline._analyze_dataframe` call chain in an isolated
process and reports per-file read/analyze time and peak RSS; point it at a
folder to scan your real files. `bench/gen_capacity.py` synthesizes
reproducible sales-like CSVs (50 k / 250 k / 1 M rows) when real data isn't
available. Close other heavy apps before running — memory numbers skew under
memory pressure (the measurements above were validated on an idle box).