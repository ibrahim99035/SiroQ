# SiroQ — AI-Assisted Forecasting & Multi-Format Delivery

Design for extending the forecasting capability with (a) stronger statistical
methods, (b) a provider-agnostic LLM layer used **exclusively for narrative
interpretation**, and (c) a persisted forecast artifact that renders into any
number of output formats at zero marginal cost.

> For the registry pattern this builds on, see [ARCHITECTURE.md](ARCHITECTURE.md).
> For "how do I add a forecast method?", see [ADDING_FEATURES.md](ADDING_FEATURES.md).
> For measured resource costs, see [CAPACITY.md](CAPACITY.md).

This document is design only. It adds no dependency and changes no schema until
the phases in §12 are approved and implemented.

---

## 1. Goals / non-goals

**Goals**

1. Improve forecast accuracy without introducing numpy/scipy/statsmodels.
2. Let a narrative be generated once and reused by every consumer.
3. Support multiple LLM providers, configured and rotated at runtime.
4. Keep per-request cost near zero via a persisted, content-addressed artifact.
5. Add formats suitable for *other* applications, not just the bundled dashboard.

**Non-goals — these are decisions, not omissions**

| Non-goal | Why |
|---|---|
| The LLM never produces numeric point forecasts | LLMs are materially worse numeric forecasters than the models already in `forecast.py`. Spending tokens here would reduce accuracy. |
| No embedding model / vector store in v1 | Retrieval value is concentrated in numeric-series analog matching, which is better served by interpretable features. See §4.3. |
| Raw uploaded files are never sent to a provider | The pipeline already reduces input ~63× before any LLM call. See §3.3. |
| The LLM never overwrites deterministic results | Healthcare-adjacent data (`engines/patients.py`, `prescriptions.py`, `payers.py`) requires the numeric result to remain independently auditable. |

---

## 2. Terminology

- **Series** — a univariate `(date, value)` sequence built by `build_series()`
  (`app/analytics_service/preview.py:118`).
- **Artifact** — the persisted result of forecasting one series under one
  parameter set, addressed by a `signature` (§5.1).
- **Narrative** — LLM-generated prose interpreting an artifact. Advisory only.
- **Renderer** — a pure function from a stored artifact to one output format.

---

## 3. Current state (verified)

### 3.1 There is no AI in the codebase today

Zero provider SDKs, zero outbound HTTP clients in `app/`, zero embeddings, zero
vector store, zero prompt construction. The code states this deliberately:

- `app/analytics_service/classification.py:7-8` — "intentionally NO LLM-fallback layer"
- `app/analytics_service/forecast.py:1-5` — "no ML / AI, no fitted model artifacts"
- `app/routers/analyses.py:236` — "Non-AI forecasting"

**No tokens are being spent today, so "save tokens" is not a saving to bank — it
is a design constraint to hold and to measure.**

### 3.2 The existing forecast engine

`forecast()` (`app/analytics_service/forecast.py:301-474`) is implemented from
scratch in pure `math` — the module docstring (`forecast.py:19-23`) records the
intent explicitly:

> Everything is implemented from scratch in pure Python `math` — no scipy, no
> statsmodels, no numpy. The grade-9 linear algebra (Gaussian elimination for the
> small normal equations) lives here so nothing slips into requirements.txt.

Six candidates, registered as pluggable entries:

| Method | Registration | Notes |
|---|---|---|
| `naive` | `forecast.py:145` | repeat last value |
| `mean` | `forecast.py:153` | flat at historical average |
| `moving_average` | `forecast.py:174,176` | two windows (win=3, win=7) under one name via `allow_dup=True` |
| `linear` | `forecast.py:180` | least-squares trend |
| `damped_trend` | `forecast.py:246` | Holt with damped trend, 9×9 α/β grid (`forecast.py:253-260`) |
| `seasonal` | `forecast.py:192` | trend + **additive** weekly factors, period fixed at 7 |

Selection: default holdout `max(2, min(14, n // 5))` (`forecast.py:364`), scored
`0.7 * holdout_error + 0.3 * in_sample_error` (`forecast.py:392`), MAPE with MASE
fallback. Return shape (`forecast.py:466-474`):

```
{ series, forecast, method, horizon, period, confidence, diagnostics }
```

`diagnostics` (`forecast.py:450-464`) already carries `method`, `holdout`,
`holdout_error`, `selection_score`, `rmse`, `mape`, `r2`, `slope`, `points`,
`notes`, and `considered` (every candidate's holdout error) — a strong basis for
narrative generation.

### 3.3 The pipeline already does the compression RAG would buy

Measured on live data:

| Artifact | Size |
|---|---|
| Raw uploaded files (`files` table) | 1,068 kB |
| `analyses.report` JSON | **17 kB** |
| `analyses.summary` JSON | 152 bytes |

**~63× reduction before any LLM is involved.** Chunking spreadsheets into
embedding vectors would spend embedding tokens to save inference tokens, and the
chunk boundary destroys row/column context. The persisted report is already the
token-efficient representation.

### 3.4 The real efficiency problem is recomputation, not tokens

`GET /files/{file_id}/forecast` (`app/routers/analyses.py:225`) is documented as
running "over the live data source (no persisted analysis)". Every call:

1. Re-parses the file from disk (`load_file` → `select_frame`).
2. Re-runs the whole candidate grid, including Holt's 81-iteration α/β search.

There is **no caching anywhere in the project** — no Redis, no `lru_cache`, no
memoization layer. `tests/test_forecast.py:24-27` (`test_deterministic`)
guarantees identical input yields identical output, so **caching is both free and
exactly correct.** Fixing this is Phase 0 and is a win independent of any AI work.

### 3.5 Infrastructure facts that constrain the design

| Fact | Value | Consequence |
|---|---|---|
| Postgres | 15.8 (`postgis/postgis:15-3.4`) | — |
| `pgvector` | **not available** (only `fuzzystrmatch`, `pg_trgm`, `unaccent`, `postgis`) | No vector DB without an image change. `pg_trgm` is available and sufficient. |
| `cryptography` | **not installed** | New dependency, required for §5.4. Binary wheels — no build step. |
| `httpx` | `0.27.2` in `requirements.txt:17` | Available at runtime; no new HTTP client needed. |
| `siroq_app` grants | `SELECT` on tables, **no** `CREATE` on database | Migration `008` must run as `siroq` via `MIGRATIONS_DATABASE_URL`; the app role needs explicit `GRANT`s. |
| Handlers | all sync `def`, no `async` | An LLM call adds network latency to a currently pure-compute path — must stay off the critical path. |
| Test suite | 46 tests, 6 modules | Any new code must be additive behind a default-off flag. |

Note: the zero-dependency stance in `forecast.py:19-23` is about **numerics**.
Adding `cryptography` for secret storage does not conflict with it.

---

## 4. Architectural decisions

### 4.1 Persist once, render many

One canonical artifact per unique series configuration. Every format is a pure
function of that stored artifact.

```
signature = sha256(file_id | date_col | value_col | agg | horizon | confidence | series_fingerprint)
```

`series_fingerprint` covers the actual values, so appending new rows produces a
new signature and a recompute — the cache can never serve a stale forecast.

This single decision is what makes multi-format delivery free. Formats do not
call the LLM; they read `artifact.narrative`.

### 4.2 The LLM is a narrative layer, not a numeric layer

The division of labour:

| Concern | Owner | Cost |
|---|---|---|
| Point forecasts + prediction band | `forecast.py` candidates | 0 tokens |
| Method selection | holdout scoring (§3.2) | 0 tokens |
| Anomaly / structural-break detection | deterministic (new, §5) | 0 tokens |
| Prose interpretation, what-if framing, scenario narrative | **LLM** | ~1 call per artifact |
| Format rendering | deterministic renderers | 0 tokens |

The narrative is generated **once**, stored in the artifact, and served to every
consumer. 50 dashboard views cost 1 LLM call, not 50.

### 4.3 Retrieval: feature-based analog matching, not embeddings

The one place RAG genuinely applies is informing method selection — today
selection sees only the current series (`forecast.py:377-394`).

Before/alongside the holdout, retrieve the *k* most similar historical artifacts
and bias selection toward methods that historically won on comparable shapes.

**Why features, not embeddings:** embeddings of numeric series are weak and
expensive. An interpretable feature vector is more reliable *and* free:

| Feature | Rationale |
|---|---|
| `points` | length regime |
| `slope` (normalized) | trend direction and magnitude |
| `seasonal_strength` | ACF peak at the candidate period |
| `cv` (coef. of variation) | volatility regime |
| `missing_ratio` | data density |
| `period` | detected seasonal period |

Similarity is a weighted Euclidean distance over these scalars — no model, no
index, no embedding cost. `pg_trgm` handles any label/synonym matching on top.

Retrieval is a **tie-breaker and confidence signal**, never an override: the
holdout score remains authoritative, because only the holdout is fitted on data
the model has not seen.

### 4.4 Provider selection is policy-driven, not key-driven

Selecting a provider by *which key happens to exist* is non-deterministic and
silently changes behaviour when a key is added. Instead: an explicit
`task → provider` mapping resolved by `priority`, with a fallback chain and a
per-provider circuit breaker.

### 4.5 Redaction is the default

Results are not automatically safe to send. The series values are aggregated
business figures, and **column names leak the most** — this service has
`engines/patients.py`, `prescriptions.py`, and `payers.py`, so a column named
`diagnosis` or `patient_id` would reach a third-party log.

Default prompt payload: **statistics and series shape only** — method,
diagnostics, and a downsampled value sequence. No column names. Narrative stays
generic ("the selected series"). Column names are transmitted only under an
explicit opt-in, because the model cannot meaningfully discuss semantics without
them.

---

## 5. Data model — migration `008`

Owned by `siroq`, with explicit `GRANT`s to `siroq_app` (which cannot `CREATE`).
Follows the existing pattern in `alembic/versions/20260921_006_analysis_service.py`
and `20260921_007_unique_application_name.py`.

### 5.1 `forecast_artifacts`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | server default |
| `application_id` | UUID FK → `applications` | `ON DELETE CASCADE` |
| `file_id` | UUID FK → `files` | `ON DELETE CASCADE` |
| `signature` | TEXT **UNIQUE** | cache key (§4.1) |
| `sheet`, `date_col`, `value_col`, `agg` | TEXT | reproducing inputs |
| `horizon`, `period`, `confidence` | INT / FLOAT | reproducing inputs |
| `result` | JSONB | verbatim `forecast()` output |
| `features` | JSONB | §4.3 feature vector, for analog matching |
| `anomalies` | JSONB | deterministic detections (§5.4) |
| `narrative` | JSONB | `{headline, detail, caveats[], confidence_note}` |
| `narrative_model`, `narrative_provider` | TEXT | provenance |
| `generated_at` | TIMESTAMPTZ | |

Index: `UNIQUE (signature)`; `INDEX (application_id, created_at)`.

### 5.2 `forecast_precedents`

Realized outcomes, written when actuals later arrive for a past horizon.

| Column | Type | Notes |
|---|---|---|
| `artifact_id` | UUID FK | the prediction |
| `features` | JSONB | §4.3 vector at prediction time |
| `winning_method` | TEXT | what selection chose |
| `realized_error` | FLOAT | absolute error between actual and predicted at the shortest common horizon |
| `observed_at` | TIMESTAMPTZ | |

### 5.3 `llm_providers` and `llm_usage`

`llm_providers`: `id`, `name`, `kind` (`openai_compat` | `anthropic` | `gemini` |
`ollama`), `base_url`, `model`, `key_ciphertext BYTEA`, `key_fingerprint`,
`key_last4`, `priority`, `enabled`, `created_at`, `updated_at`.

`llm_usage`: `id`, `provider_id` FK, `task`, `model`, `tokens_in`, `tokens_out`,
`cache_read_tokens`, `latency_ms`, `estimated_cost_usd`, `artifact_id` (nullable),
`created_at`.

### 5.4 Storage-key security properties

Because this database holds clinical-adjacent data:

- Master key `LLM_MASTER_KEY` (32 bytes, base64) from **env only**, never
  persisted. Losing it loses every stored key — that is the intended blast radius.
- Per-row **AES-256-GCM**, random 96-bit nonce, with provider `id` bound as AAD so
  ciphertext cannot be transplanted to another row.
- The API returns only `key_fingerprint` (`sha256:ab12cd34`) and `key_last4`.
  **Ciphertext never crosses the wire** — which makes a provider-management UI
  safe by construction.
- Never logged, including on exception paths.
- `llm_usage` is **append-only**: `INSERT` only, no `UPDATE`/`DELETE`.
- `llm_providers` gets `SELECT/INSERT/UPDATE` but **no `DELETE`** — providers are
  retired via `enabled=false` to preserve the audit trail.
- Database backups hold ciphertext; the master key lives outside the database, so
  a stolen backup alone yields no usable keys.

**Alternative if this concentration of risk is unwanted:** an OS keyring or a
secrets service behind the same `Provider` interface. `app/llm/secrets.py` is the
only module that touches key material, so swapping the source is a config change
rather than a refactor. (`keyring` is also not currently installed.)

---

## 6. Module layout

```
app/
├── llm/
│   ├── types.py              LLMRequest / LLMResponse / Usage / ProviderConfig + Provider Protocol
│   ├── registry.py           provider registry (mirrors analytics_service/registry.py)
│   ├── routing.py            task → provider, fallback chain, circuit breaker
│   ├── cache.py              content-hash response cache keyed on (model, prompt, params)
│   ├── ledger.py             append-only usage writes
│   ├── secrets.py            THE ONLY module that touches key material
│   ├── adapters/
│   │   ├── openai_compat.py  OpenAI + Groq/Together/OpenRouter/Ollama/vLLM/LM Studio/Azure
│   │   ├── anthropic.py      messages API (no system role, separate max_tokens)
│   │   └── gemini.py         contents API
│   └── knowledge/
│       └── store.py          pg_trgm + feature-distance analog retrieval
└── analytics_service/
    ├── forecast.py           (extended, §8)
    ├── narrative.py          artifact → narrative (LLM)
    ├── anomalies.py          deterministic break/outlier detection
    └── renderers/
        ├── json.py  csv.py  markdown.py
        ├── html.py          fragment for the dashboard
        └── chart.py         payload for the existing dashboard charts.js
```

`openai_compat` is the base adapter because one HTTP shape covers the large
majority of hosted and self-hosted providers. Native adapters exist only where
the wire format genuinely differs.

---

## 7. Provider configuration

```ini
LLM_ENABLED=false                 # default off — zero behaviour change
LLM_MASTER_KEY=<base64 32 bytes>  # env only, never in the DB
LLM_TASK_FORECAST_NARRATIVE=openai_compat:default
```

New `Settings` fields in `app/config.py`, validated by the existing
`model_validator` pattern (`app/config.py:30-38`): refuse to boot with
`LLM_ENABLED=true` and no `LLM_MASTER_KEY`.

Adapters are sync `httpx` calls, consistent with the project's all-sync handler
style. `httpx==0.27.2` is already in `requirements.txt:17` and installed at
runtime, so the **only** new runtime dependency this design introduces is
`cryptography` (§5.4).

---

## 8. Model extensions (zero new dependencies)

Registered via the existing `@forecast_method` decorator, so selection, the
`allow_dup` semantics, and the `order` tie-break all keep working unchanged.

| Addition | Rationale |
|---|---|
| Multiplicative seasonality | Current `seasonal` (`forecast.py:192`) is additive-only; multiplicative growth is the most common real pattern. |
| Log-transform variant | Lets `linear`/`seasonal` model percentage growth. Guard `y <= 0`. |
| Auto period detection (7/12/24/30/365) | Period is fixed at 7 (`forecast.py:305`) and the router never overrides it — `analyses.py:274-279` passes only `values`, `dates`, `horizon`, `confidence`, and no `period` parameter is exposed on `/files/{id}/forecast`. Callers therefore cannot change it, and monthly or hourly series are mis-modelled today. |
| Outlier / structural-break handling | Robust fit so one spike cannot capture a damped trend. |
| `ets` (Holt-Winters, full) | Completes the Holt family already started at `forecast.py:246`. |

> **Prerequisite fix.** `Registry.all()` (`app/analytics_service/registry.py:73-79`)
> iterates `self._entries.values()` in dict insertion order and **never sorts by
> `order`**. Today stage/method order is correct only because the decorators
> happen to appear in the right order. Adding registrations that depend on
> `order` makes that latent bug load-bearing. Sort by `order` in `all()` first —
> this will change nothing observable today and is covered by existing tests.

All additions must be deterministic. `tests/test_forecast.py:24-27` is the guard.

---

## 9. Narrative generation

**Input:** method, `diagnostics`, `anomalies`, downsampled series (~30 points),
forecast summary. **No column names by default** (§4.5).

**Output** stored in `artifact.narrative`:

```json
{
  "headline": "...",
  "detail": "...",
  "caveats": ["..."],
  "confidence_note": "..."
}
```

- On provider failure: `narrative` stays null, the artifact is still valid and
  still renders in every numeric format. A missing narrative is not a 500.
- Grounded strictly in the supplied numbers; instructed to state uncertainty
  rather than to hedge generically.
- `cache.py` dedupes by `(model, prompt_hash, params)` so a re-render after a
  no-op change costs nothing.

---

## 10. Output formats

All are pure functions of a stored artifact — **zero LLM calls**.

| Format | Consumer |
|---|---|
| Canonical JSON | other services, `/api/v1` |
| CSV | spreadsheets, downstream ETL |
| Markdown | docs, tickets, email |
| HTML fragment | bundled dashboard report |
| Chart payload | existing `app/dashboard/charts.js` |
| Plain narrative | CLI, notifications |

New endpoints consume `forecast_artifacts`; `/files/{id}/forecast` keeps working
as a live-recompute escape hatch.

---

## 11. Token budget

Per unique artifact signature:

| Item | Value |
|---|---|
| Prompt | ~400 tokens (diagnostics + ~30 points) |
| Completion | ~200 tokens |
| Calls | **1** |
| Subsequent renders | **0** |

| Approach | Calls for a 50-view dashboard | Tokens |
|---|---|---|
| LLM narrates per request | 50 | ~30,000 |
| **Narrative stored in artifact** | **1** | **~600** |

`llm_usage` (§5.3) records every call, so this claim is **measurable rather than
asserted** — including the case where it turns out to be wrong.

---

## 12. Phasing

| Phase | Scope | Acceptance |
|---|---|---|
| **P0** | Persist + cache the forecast. No AI, no schema beyond `forecast_artifacts`. Fix `Registry.all()` ordering. | Repeat `/forecast` does not re-parse or re-fit. 46 existing tests pass. |
| **P1** | Provider registry, `secrets.py`, `routing.py`, `llm_providers`, `llm_usage`. | Two real providers round-trip. API never returns ciphertext. Append-only ledger enforced. |
| **P2** | `narrative.py`, `anomalies.py`, `renderers/`, narrative + format endpoints. | One call per artifact, verified in `llm_usage`. All 6 formats render with the LLM disabled. |
| **P3** | `knowledge/store.py` analog retrieval → `forecast_precedents`. | Retrieval measurably changes method selection on a backtest; otherwise it is reverted. |

P0 is worth doing on its own merits and gates nothing else. P3 is explicitly
falsifiable — if analog retrieval does not improve selection on a backtest, it
should be dropped rather than kept.

---

## 13. Risks

| Risk | Mitigation |
|---|---|
| LLM narrative read as authoritative | Rendered in a visually distinct block; the numeric band and method are always shown alongside. §1 non-goals. |
| Keyring concentrates secret risk in one DB | Encrypted at rest, master key external (§5.4); source swappable behind `secrets.py`. |
| Series contain business values | Redaction default: statistics only, no column names (§4.5). |
| Network latency on a sync path | Narrative is generated post-persist and never blocks artifact creation. |
| Over-fitting method selection to short history | Retrieval is a tie-breaker only; the holdout remains authoritative (§4.3). |
| Added complexity without accuracy gain | P3 has an explicit revert criterion. |

### When this is the wrong tool

If the real gap is **numeric accuracy**, the answer is `statsmodels`
(ETS/ARIMA/SARIMAX) or `sklearn` gradient boosting with lag features — not an
LLM. That path was explicitly declined in favour of extending the hand-rolled
registry (§8) to preserve `forecast.py:19-23`. If §8 does not close the accuracy
gap, revisit that decision deliberately rather than reaching for the LLM.

---

## 14. Open questions

1. **Downstream consumers.** Which specific applications render these formats?
   Their requirements should shape §10 before P2 is built.
2. **Retention.** `llm_usage` and `forecast_artifacts` grow without bound. A
   retention policy is needed before P1 ships.
3. **Provider management UI.** The bundled dashboard is a static SPA. If keys
   are managed from the UI, note that §5.4 already guarantees it cannot receive
   ciphertext.
4. **Multi-series and hierarchical forecasting.** `file_series` forecasts one
   column at a time. Cross-column and hierarchical forecasts are out of scope
   here and worth a separate design.
