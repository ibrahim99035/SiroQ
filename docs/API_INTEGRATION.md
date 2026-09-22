# SiroQ Analysis Service — Consumer API Guide

How other services talk to SiroQ: authentication, every endpoint, the report
document shape, and recommended integration flows. The API is a versioned REST
interface over HTTP/JSON under `/api/v1`.

- Base URL: `http://<host>:8000/api/v1`
- Interactive reference: `http://<host>:8000/docs` (Swagger UI, auto-generated)
- Internal architecture: see [ARCHITECTURE.md](ARCHITECTURE.md)

---

## 1. Authentication

Every `/api/v1` route requires the shared static key via the `X-API-Key` header
(missing or wrong → `401`):

```
X-API-Key: dev_api_key_change_in_production
```

The key is configured with `API_KEY` (env or `.env`). Outside `ENVIRONMENT=development`,
the shipped placeholder key is refused at boot, so a real key must be provisioned.

## 2. Health

```
GET /health          (no auth required)
```
`200 {"status":"ok","service":"siroq-analysis","db":"up"}`
or `503` when the database is unreachable.

## 3. Endpoint reference

All parameters below are exact. `application_id` / `analysis_id` / `file_id`
are UUIDs; the service answers `404` for missing **or malformed** UUIDs.

### 3.1 One-shot: upload files and analyze in a single request

```
POST /analyze
multipart/form-data:
  application_name: str   (required; created if absent, reused if present)
  files: UploadFile[...]  (1..MAX_FILES_PER_REQUEST=25; each ≤ MAX_FILE_BYTES=50 MB)
```

A single call that creates the application row, persists every uploaded file,
runs the full pipeline, and returns the persisted analysis immediately (the run
is synchronous). Supported formats by extension: `.csv`, `.xlsx`, `.xls`,
`.json`, `.jsonl`. Reusing an application name re-runs analysis over the
accumulated file set.

Response `200`:

```json
{
  "application_id": "ba9c5613-…",
  "analysis_id": "4df3d638-…",
  "created_at": "2026-09-22T13:14:35.131623+00:00",
  "summary": {
    "file_count": 4,
    "total_rows": 9828,
    "categories_detected": {"sales": ["111.xls", "siroq 22.xls"], "purchase_orders": ["PURCHASE -ABDELHAMID.xls"]},
    "data_quality_score": 87.5,
    "findings_count": 7
  },
  "report": { "… full report document (§4) …" }
}
```

### 3.2 Applications

```
GET  /applications                          → all apps + latest analysis summary
POST /applications        {name, metadata?} → 201, creates (or returns) the app
GET  /applications/{application_id}         → app detail: files[] + analyses[]
```

`GET /applications` returns `{"applications":[{id,name,metadata,created_at,file_count,analysis_count,latest_analysis:{…}}]}`.
Creating an application is optional — `POST /analyze` already creates one by name.

### 3.3 Files

```
POST /applications/{application_id}/files   multipart: files[]  → uploads + analyzes immediately
GET  /files/{file_id}                        → raw bytes download (`X-SHA256` header = stored hash)
GET  /files/{file_id}/preview?sheet=&rows=25&offset=0   → live columns + sample rows
GET  /files/{file_id}/series?sheet=&date_col=&value_col=&agg=sum|mean|count   → daily series
GET  /files/{file_id}/forecast?sheet=&date_col=&value_col=&agg=&horizon=14&confidence=0.90    → forecast
```

`.csv`/`.json` files expose a single sheet (`Sheet1`/`default`); Excel files
expose each sheet name (pass `sheet` as name, or 0-based index). For series and
forecast, `date_col`/`value_col` are auto-detected when omitted; if no value
column is found the aggregation falls back to a row **count**.

`GET /files/{id}/preview` shape:

```json
{
  "file_id": "feeafc28-…", "file_type": "excel", "filename": "111.xls",
  "size_bytes": 706048, "sheet": "Sheet1", "sheets": ["Sheet1"],
  "row_count": 1750, "offset": 0,
  "columns": [
    {"name": "net_profit", "dtype": "float64", "kind": "number",
     "null_count": 1, "sample_values": ["2.0", "-0.92"]}
  ],
  "rows": [ { "…first page of rows…" } ]
}
```

`GET /files/{id}/series` → `{"series": [{"date":"2026-04-01","value":1.0}],
"meta": {"points": 77, "date_column": "expiry_date", "value_column": null, "agg":"count",
"min_date": "…", "max_date": "…", "span_days": 23406, "reindexed": false,
"file_id": "…", "filename": "…", "sheet": "Sheet1", "sheets": ["Sheet1"]}, "columns": […]}`.

`GET /files/{id}/forecast` → adds to the series:

```json
{
  "series": [{"date": "…", "value": 108.0, "fitted": 1.0}],
  "forecast": [{"step": 1, "date": "…", "value": 1.0, "lower": 0.0, "upper": 146.57}],
  "method": "naive",
  "method_description": "Naive: repeat the last observed value.",
  "horizon": 7, "period": 7, "confidence": 0.9,
  "diagnostics": { "…residual stats…" },
  "meta": { "…series meta above + file context…" }
}
```

Forecasting is fully deterministic, classical statistics only (naive / moving
average / linear / weekly seasonality / damped Holt, chosen by a holdout).

### 3.4 Analyses (saved results)

```
GET /applications/{application_id}                                   → analyses[] ids
POST /applications/{application_id}/analyze                          → re-run analysis over existing files
GET /applications/{id}/analyses/{analysis_id}                        → full saved analysis (summary + report)
GET /applications/{id}/analyses/{analysis_id}/report?format=html|json
```

`format=json` streams the persisted report document (`Content-Disposition:
attachment` with a `siroq-report-<app>-<id8>.json` filename) — the standard way
for other services to consume results. `format=html` renders a self-contained,
printable report page.

## 4. Report document schema

Persisted per analysis (both in `GET …/analyses/{id}` and downloadable as JSON):

```json
{
  "application_id": "…", "application_name": "…",
  "analyzed_at": "2026-09-22T13:14:35+00:00",
  "engine_version": "0.1.0",
  "files": [
    {
      "file_id": "…", "filename": "111.xls", "file_type": "excel",
      "sha256": "…", "size_bytes": 706048,
      "row_count": 1750,
      "columns": ["net_profit", "product_name", "…"],
      "profile": {
        "row_count": 1750, "column_count": 9, "columns": ["…"],
        "memory_bytes": 153581,
        "column_profiles": [
          {"column": "net_profit", "kind": "number", "count": 1750,
           "nulls": 1, "unique": 416, "min": -45.0, "max": 910.0,
           "mean": 12.7, "std": 55.1, "zeros": 0, "negatives": 5,
           "histogram": [{"bin": "0–91", "count": 1180}, "…"]}
        ]
      },
      "classification": {
        "headers": ["net_profit", "…"],
        "field_scores": {
          "net_profit": {"status": "confirmed", "score": 1.0,
                         "suggested_mapping": "total_amount", "confidence": 0.97}
        },
        "canonical_fields": {"total_amount": "net_profit", "sale_timestamp": "sale_date", "…"}
      },
      "data_quality": {
        "score": 85.0,
        "checks": [
          {"check": "header_confidence", "status": "warn",
           "low_confidence_fields": ["…"]},
          {"check": "empty_or_null", "status": "fail",
           "empty_file": false, "high_null_columns": [{"column": "script", "null_pct": 0.83}]},
          {"check": "duplicate_rows", "status": "fail", "count": 2,
           "sample_rows": [{"…": "…"}]},
          {"check": "negative_values", "status": "pass", "by_column": {}},
          {"check": "type_coercion", "status": "pass", "by_column": {}},
          {"check": "unmapped_columns", "status": "warn", "unmapped_columns": ["vendor_ref"]}
        ],
        "duplicate_count": 2, "findings_count": 4
      },
      "quality_findings": ["header_confidence=warn …", "…"],
      "categories": {"sales": 0.62, "purchase_orders": 0.38},
      "top_category": "sales",
      "domain_analytics": {
        "category": "sales",
        "revenue": 123456.78, "transactions": 1750, "avg_basket": 70.54,
        "top_products": [{"product": "Amoxil 500", "revenue": 3210.5}],
        "gross_merchandise_value": 123456.78, "gross_margin": 0.18, "…"
      }
    }
  ]
}
```

Summary document (also embedded in analyses responses):

```json
{
  "file_count": 4, "total_rows": 9828,
  "categories_detected": {"sales": ["111.xls"], "purchase_orders": ["…"]},
  "data_quality_score": 87.5,       // mean of per-file scores, or null
  "findings_count": 7
}
```

Multi-sheet workbooks differ per-file: `multi_sheet: true`, `sheets: [{sheet,
row_count, columns, profile, data_quality, …}]`, `domain_analytics.skipped` =
`"multi-sheet workbook; analyze each sheet separately"`, and
`sheet_categories` lists per-sheet top categories. Numbers are JSON-safe
(`NaN`/`±inf` → `null`).

### Domain categories and canonical fields

`top_category` and `domain_analytics.category` are one of:
`sales`, `inventory`, `purchase_orders`, `products`, `patients`,
`prescriptions`, `suppliers`, `payers` (whichever wins header scoring).
`classification.canonical_fields` maps the *actual* columns to canonical SiroQ
names (e.g. `total_amount`, `sale_timestamp`, `product_name`, `quantity_sold`,
`stock_balance`). Unknown categories yield `domain_analytics = {"skipped":
"no category detected"}`.

## 5. Errors

| Status | Meaning |
|--------|---------|
| `400` | Bad request — missing name/files, unsupported extension, no files to analyze, no date/value column, empty sheet |
| `401` | Missing or invalid `X-API-Key` |
| `404` | Application / analysis / file not found (or malformed UUID) |
| `413` | File exceeds `MAX_FILE_BYTES` (50 MB default) |
| `500` | Analysis pipeline failed |

Consumers should treat `500` as transient and retry, and `409`-style races as
unlikely (applications are created idempotently by name).

## 6. Recommended integration flow

1. **Ingest** — `POST /analyze` once per batch (files + `application_name`). The
   response carries `analysis_id` + `summary` synchronously.
2. **Consume results** — either use the inline `report`, or later
   `GET /applications/{id}/analyses/{analysis_id}/report?format=json`.
3. **Refresh** — re-run `POST /applications/{id}/analyze` after new uploads, or
   upload more files via `POST /applications/{id}/files` (which re-analyzes).
4. **Interact with a single file** (optional) — `preview` for UI inspection,
   `series` for time-series plumbing, `forecast` for a deterministic projection.

Example (curl):

```bash
BASE=http://127.0.0.1:8000/api/v1
KEY="X-API-Key: dev_api_key_change_in_production"

# one-shot ingest + analyze
curl -s -H "$KEY" -F "application_name=Pharma A" \
     -F "files=@sales.xlsx" -F "files=@purchases.xls" \
     "$BASE/analyze" > analysis.json

# later: pull the machine-readable report
ANALYSIS_ID=$(jq -r .analysis_id analysis.json)
curl -s -H "$KEY" "$BASE/applications/$(jq -r .application_id analysis.json)/analyses/$ANALYSIS_ID/report?format=json"
```

## 7. Operational notes

- Upload limits live in settings (`MAX_FILES_PER_REQUEST=25`,
  `MAX_FILE_BYTES=50 MB`) — tune via env, do not change the contract.
- Raw file bytes are stored under `STORAGE_PATH` and referenced by
  `files.stored_path`; hashes are `sha256` so consumers can dedupe.
- The service is stateless between requests except for Postgres + storage; the
  human dashboard lives at `/dashboard/` and is not part of the machine API.
- OpenAPI is served at `/openapi.json`; import it into API clients / codegen
  against any of the documented routes.