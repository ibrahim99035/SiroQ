# SiroQ — Adding Features

Every extensible part of the analysis service is a **registry** collected at
import time (see [ARCHITECTURE.md](ARCHITECTURE.md)). To add a feature you write
one decorated function; you almost never edit existing logic. After any change,
rerun the tests (command at the bottom).

---

## 1. Add a domain-analytics engine (a business category)

Example: add a `hr` category that computes headcount + payroll.

```python
# app/analytics_service/engines/hr.py
from app.analytics_service.engines._helpers import pick_col, numeric, safe_sum
from app.analytics_service.registry import domain_engine


@domain_engine("hr")
def hr_analytics(df, fmap):
    result = {
        "category": "hr",
        "headcount": int(len(df)),
        "payroll_total": safe_sum(numeric(df, pick_col(fmap, "payroll"))),
    }
    return {"metric": result}
```

Then register it by importing the module:

```python
# app/analytics_service/engines/__init__.py
from app.analytics_service.engines import hr, inventory, purchase_orders, reference, sales
```

Notes:
- `fmap` resolves canonical field names to the file's real columns (from
  classification). Use `pick_col(fmap, "canonical_name")` to get the column, or
  `None` when the file lacks it.
- `@domain_engine` takes an optional `order=` (used for future tie-breaking /
  ordering; defaulting is fine).
- If your category should be *detected* by column names, add strong/weak tokens
  in `ingestion.py` (`_STRONG_TOKENS`, `_WEAK_TOKENS`) — e.g.
  `"hr": {"employee", "badge", "attendance"}`.
- The engine returns a JSON-safe dict; the dispatcher wraps any exception into
  `{"skipped": "…"}` so one engine can never break a run.

---

## 2. Add a data-quality check

Example: flag columns whose values are all identical:

```python
# app/analytics_service/quality.py
from app.analytics_service.registry import quality_check, quality_checks


@quality_check("constant_columns", penalty=5)
def check_constant_columns(df, field_scores):
    constants = []
    if df is not None and not df.empty:
        for col in df.columns:
            if df[col].nunique(dropna=True) <= 1:
                constants.append(str(col))
    return {
        "check": "constant_columns",
        "status": "warn" if constants else "pass",
        "constant_columns": constants,
    }
```

Notes:
- `penalty` is subtracted from 100 when the check doesn't pass (default 0 — the
  finding still counts toward `findings_count`).
- Checks must tolerate `df is None` and empty frames (both code paths run every
  registered check).
- If the check reads the classification output, pass `needs_field_scores=True`
  to the decorator and use the `field_scores` argument — it is then skipped
  (injected as `{}`) when classification produced nothing.
- That's it: `run_quality_checks` picks it up automatically, in registration
  order. No edits to the scoring loop.

---

## 3. Add a forecast method

Example: add a `last_two` method — a 2-point trailing average:

```python
# app/analytics_service/forecast.py
from app.analytics_service.registry import forecast_method  # already imported


@forecast_method("last_two", description="Average of the last two points.")
def _mod_last_two(ys, seasons, steps, **kw):
    if not ys:
        return [], [0.0] * steps
    base = ys[-2:]
    m = (base[0] + base[1]) / 2 if len(base) == 2 else base[0]
    return [m] * len(ys), [m] * steps
```

Notes:
- Signature is always `fn(ys, seasons, steps, **kw)` returning `(fitted, future)`.
  `ys` is the numeric series, `seasons` the day-of-week alignment (may be empty),
  `steps` the horizon.
- The method is instantly a holdout **candidate**; it wins only if its
  out-of-sample error beats the others. `order` breaks ties (lower wins).
- `description` feeds `forecast.describe()` shown in the UI.
- Registering the **same name twice** is allowed (e.g. two windows) — the last
  registration wins the holdout score, the first wins the final fit.

---

## 4. Add a pipeline stage (run on every DataFrame)

Example: compute a row-completeness ratio into the report:

```python
# app/analytics_service/pipeline.py
from app.analytics_service.registry import file_analyzer, file_analyzers  # already imported


@file_analyzer("completeness", order=50)
def _stage_completeness(df, ctx):
    if df.empty:
        ctx["completeness"] = None
        return
    filled = df.notna().sum().sum()
    ctx["completeness"] = round(filled / (len(df) * len(df.columns)), 3)
```

Notes:
- `order` controls execution order (profile=10, classification=20, quality=30,
  domain=40; run yours after those you depend on).
- Stages share one `ctx` dict: `df`, `classification`, `field_scores`, `fmap`,
  `profile`, `data_quality`, `categories`, `top_category`,
  `domain_analytics`. Read the ones you need, write your own.
- To surface the result in the report, add it to `_analyze_dataframe`'s `sub`
  dict (e.g. `sub["completeness"] = ctx.get("completeness")`).
- A raising stage is caught and recorded in `ctx["stage_errors"]` (not surfaced
  in the report) — it cannot break its sibling stages.

---

## 5. Add a file reader (new import format)

Example: support `.tsv` (tab-separated values):

```python
# app/analytics_service/ingestion.py
from app.analytics_service.registry import file_readers  # already imported


@file_readers.register("tsv", description="Tab-separated values.")
def _read_tsv_source(content: bytes):
    df = pd.read_csv(io.BytesIO(content), sep="\t")
    return ReaderResult({"default": df}, [])
```

And map the extension to the reader name:

```python
_EXT_TO_TYPE = {
    ".csv": "csv",
    ".tsv": "tsv",            # <-- add
    ".xlsx": "excel",
    ".xls": "excel",
    ".json": "json",
    ".jsonl": "json",
}
```

Notes:
- A reader receives `content: bytes` and returns a `ReaderResult(sheets, notes)`
  where `sheets` maps sheet names → DataFrames (`{"default": df}` for
  single-frame formats). `notes` are structural notes appended to the file's
  `notes` list.
- `read_file` normalizes column names (lowercase, space→underscore) after the
  reader runs, so readers don't need to.
- Failures inside a reader surface as `errors` on the `IngestedFile`, never a
  crash.

---

## 6. Add a calculated insight

Insights are cross-file business facts (profitability, concentration, waste,
trends) rather than per-category engines. They live in
`app/analytics_service/insights.py`, are registered with `@insight_rule`, and
run in the `insights` pipeline stage (order 50).

```python
# app/analytics_service/insights.py
import pandas as pd

from app.analytics_service.registry import insight_rule

REVENUE = ("total_amount", "revenue", "total_revenue")
PRICE = ("unit_price", "selling_price")


@insight_rule("discount_depth", family="profitability", order=42,
              requires=("total_amount", "unit_price"))
def discount_depth(ctx) -> dict:
    revenue_col = ctx.first(REVENUE)
    price_col = ctx.first(PRICE)
    if revenue_col is None or price_col is None:
        return _skip(["total_amount", "unit_price"],
                     "Needs a row revenue column and a unit price column.")
    price = ctx.num(price_col).replace(0, pd.NA)
    if price.isna().all():
        return _skip(["unit_price"], "Unit price is zero or empty everywhere.")
    depth = 1.0 - (ctx.num(revenue_col) / price).mean()
    return _ok(
        round(float(depth) * 100, 2), "percent",
        "Average discount implied by row revenue vs list price.",
        evidence={"rows": int(len(ctx.df))},
    )
```

Notes:
- **Never raise.** The dispatcher isolates every rule: an exception becomes
  `status="error"` with the message, and the other rules still run. Return
  `_skip(...)` for anything you cannot compute.
- `_skip(missing, reason)` fills in `status="skipped"`, `severity="muted"`,
  `value=None` and `missing_columns`; the reason sentence becomes the
  user-facing note, so say which columns are needed.
- `_ok(value, unit, detail, severity=..., evidence=...)` is JSON-safe by
  contract: NumPy scalars are converted and `evidence` is truncated to keep one
  result small. Keep evidence to a handful of scalars plus a short
  `top`/`buckets` list.
- `severity` drives the UI colour: `good` / `warn` / `bad` / `info` (and `muted`
  for skipped).
- Columns are reached through the classification map, never by hard-coded
  header. Declare a module constant as a flat tuple of the canonical name
  followed by fallbacks (`REVENUE = ("total_amount", "revenue", ...)`), then use
  `ctx.first(REVENUE)` for the first group that resolves, `ctx.col("unit_price",
  "selling_price")` for one name plus fallbacks, and `ctx.require(REVENUE,
  COST_TOTAL)` when you need the missing names too. `ctx.num` / `ctx.dates` /
  `ctx.label_of` coerce a column to numbers, datetimes and product labels.
- Use `ctx.unit_cost()` when valuing stock. It prefers a real unit-cost column
  and otherwise derives one as `total_cost / quantity_sold`, labelling the
  result `implied` — multiplying stock by a row total is never correct.
- `family` must be one of the keys in `INSIGHT_FAMILY_TITLES` in
  `reporting.py` (or a new key, which is rendered verbatim).

Registration is import-time: `pipeline.py` imports `insights`, so editing that
module is enough — no list to maintain.

### Adding a canonical field

Rules read columns through the classification map, so a new source field may
need a canonical name in `app/analytics_service/classification.py`:

- Add it to `CANONICAL_FIELDS` with a `kind`, and to `FIELD_VALUE_KINDS` if it
  must be matched by *content* rather than header text.
- **Cost, profit, stock, event and waste fields belong in
  `HEADER_ONLY_FIELDS`.** Those must never be inferred from numbers alone — an
  arbitrary numeric column is otherwise mapped as revenue, and stock valuation
  or margin silently comes out wrong. Prefer a confident header match and skip
  the insight when the header is unknown.

---

## 7. Verify

Lint/compile the touched modules and run the suite:

```bash
.venv/bin/python -m compileall -q app tests
.venv/bin/python -m pytest -q
```

`tests/conftest.py` rewrites `DATABASE_URL` and `MIGRATIONS_DATABASE_URL` to
the sibling `*_test` database (creating it if needed) **before** importing
`app.config`, and every test truncates only that database. Point `STORAGE_PATH`
somewhere throwaway as well:

```bash
STORAGE_PATH="/tmp/opencode/siroq_test_storage" .venv/bin/python -m pytest -q
```

This redirect is not optional. The isolation fixture truncates
`applications, files, analyses`, so running the suite against the development
database erases real applications, their analyses and their file rows — the
files survive in storage only because they are content-addressed.

Baseline: **72 passed**.

### Golden rules

- **Never hard-code a new behavior into a consumer loop.** Write a decorated
  function; the registry does the rest.
- **Keep JSON output stable.** Checked-in report shapes (keys, statuses,
  `skipped` messages) are relied on by the dashboard and by clients — preserve
  them when refactoring, add keys rather than renaming.
- **Don't import-cycle.** Engine/stage modules import from `registry` and
  `_helpers`, never back from `pipeline`.
- **Registration is import-time.** New modules must be imported somewhere that
  is always loaded (`engines/__init__.py` for engines; the module's own file for
  checks/methods/stages/readers) or the feature silently won't register.
- **A skipped rule is a feature, not a gap.** If a file lacks the columns for an
  insight, say so explicitly and name the columns; never let a rule guess.