# Client report contract

What the analysis service emits, and what SiroQ-Client guarantees it can render.

The two repositories are deployed independently, so this document exists to make
one thing explicit: **which JSON shapes are a contract, and which are incidental.**
Incidental shapes can change whenever the engine improves. The ones below cannot,
without a schema-version bump, because a component reads their exact keys.

## The envelope

Every projection starts with:

```json
{ "Schema version": "siroq.client.v1", "Application": "…", "Files": { "…": { … } } }
```

`Schema version` is the drift check. `KNOWN_SCHEMA_VERSION` in the client's
`components/report-panel.tsx` is the version that panel knows how to render; a
mismatch surfaces a warning rather than being silently misread. Adding keys does
not warrant a bump, so the tags described below landed without one — the panel
renders an unknown `$`-tag it does not recognise as an ordinary branch.

## The tree

```ts
type ReportValue = string | number | boolean | null;
type ReportNode = ReportValue | ReportNode[] | { [key: string]: ReportNode };
type ReportResultData = { [key: string]: ReportNode };
```

Two rules follow, and both are enforced in `tests/test_client_projection.py` and
`scripts/verify-reports.ts`:

1. **Untagged leaves are primitives.** A number or string is printed as-is;
   anything else would reach the formatter as `[object Object]`.
2. **An object inside a list is only allowed inside a tagged subtree.** Outside
   one, the panel is walking values, not records — so a bare object there is a
   defect, not a shape.

## Tagged nodes

A key beginning with `$` marks a subtree as *drawable* rather than tabular. The
service sends the data; the client draws it. Before these tags the only way to
express a bar chart in a label→value tree was a list of strings, which is
exactly what the panel used to render.

### `$chart`

```json
{ "$chart": "bar", "Total": "22,885.00",
  "Bars": [ { "Label": "Product 4", "Value": "4,630.00", "Share": "100.0%" } ] }
{ "$chart": "table",
  "Columns": ["Rank", "Product", "Value"],
  "Rows": [["1", "A", "10.00"]] }
```

- `Total` (bars only) is the summed magnitude, so the reader has the denominator.
- `Share` is a percentage of the largest bar, not of the total. It is what makes
  the bars comparable when the values have units or very different magnitudes.
- Bars are capped at `CLIENT_BAR_MAX`; tables at 6 columns × 25 rows. The document
  is `jsonb` read on every panel open, so chart size is part of the contract.
- `Value` and `Share` are **pre-formatted strings**. The client parses them for
  geometry via `toNumber()` and prints them verbatim, so a bar's label, its
  printed value and its drawn width cannot disagree.
- `Rows` cells are **pre-formatted strings** for the same reason, and a cell with
  no value is `""` — an all-null column has no min/mean/max to print, and its
  100% null rate is the finding.

### `$forecast`

```json
{ "$forecast": true,
  "Series": "Revenue", "Granularity": "week",
  "Method": "linear", "Method note": "Least-squares linear trend extrapolated forward.",
  "Confidence": "90% interval", "Horizon": "6 weeks ahead",
  "History":   [ { "Period": "2026-05-25", "Value": "1,587.00" } ],
  "Projected": [ { "Period": "2026-06-08", "Value": "1,463.49",
                   "Low": "1,122.60", "High": "1,804.39" } ],
  "Accuracy": { "Fit (R²)": "0.56", "Typical error": "0.2%" },
  "Notes": [ "…" ] }
```

- **Every projected point carries `Low` and `High`.** The client shades them as a
  prediction interval, which is the point of the chart — a bare forward line
  reads as a commitment. A projection without bounds would be drawn as if certain.
- `Period` is an ISO date. Its calendar step is not cosmetic: `forecast.py`
  infers the median spacing of the observations and uses it for both seasonality
  and the projected dates, so a weekly series does not project onto consecutive
  days. `Granularity` is what the reader sees; the dates themselves are the truth.
- The node is **omitted entirely** when a file has no dated revenue or volume
  series. An absent forecast is not a neutral zero.
- History is truncated to the most recent `CLIENT_FORECAST_POINTS` (18) so the
  document stays small.
- Source series: `revenue_trend` when present, else `volume_trend`. Revenue is
  what a filing is usually about, and falling back rather than failing keeps a
  unit-only file from silently reporting on the wrong quantity.

### `$notes`

```json
{ "$notes": [ { "Severity": "critical", "Subject": "Read error", "Detail": "…" } ] }
```

Notes are the sentences that bound how far the rest of the report can be trusted,
so they get a severity marker rather than another row of values.

| Severity | Raised for |
| --- | --- |
| `critical` | A read error, or a file where no rows could be attributed |
| `warn` | An insight the engine declined to compute |
| `info` | Structural rows excluded, and how the table was located |
| `good` | Reserved for favourable findings |

The client maps unknown severities to a plain note. A new severity should read as
information, not as an unstyled block.

Four places emit notes, all as `$notes`:

- `Caveats` — per-file read errors, structural-row exclusions, and ingestion
  notes (header row position, repeated blocks, discarded spacers). Ingestion
  notes are the only record that a sheet was reconstructed rather than read
  straight off, so they are not optional.
- `Insights → Not computed` — insight groups that were skipped and why.
- `Evidence gaps` — document-level: files where the evidence does not support
  the metrics, or failed a quality check.
- `Quality → Finding detail` — the per-file quality checks. `findings_detail`
  writes `"<check>=<status> <detail>"` (`quality.py:227`), so the status travels
  with the sentence instead of being discarded, and `fail` reaches the client as
  `critical` rather than reading like an advisory. The three fields are split
  once, in `_finding_note`, because splitting the string twice put the status
  token in `Detail` — a failure whose explanation was the word "fail". This is
  also why the node is a tagged list: the count above it and the list under it
  must describe the same set, so the count is derived, not restated.

## The column profile

`Column profile` is a `$chart: table` of `Column`, `Type`, `Nulls` and `Unique`.
It is a table rather than a map because it is a grid: sent as a
`{name: {Type, Nulls, Unique}}` map it arrives in the client as one nest per
column, which reads as thirteen key-value pairs on a dispensing log instead of
four columns of numbers. A table is also the shape a reviewer copies.

`Column detail` carries the per-column statistics and most-frequent values, and
is **omitted for any column whose entire content is already in the table**.
Sending both in full put the same null and unique rates on the page twice under
two headings, which reads as two disagreeing measurements. The table is built
from the same map it filters, so a column cannot appear in one and be missing
from the other.

## What is *not* a contract

Only the keys a component destructures are pinned. The prose in `Quality` and
`Domain` is descriptive: it is pre-formatted for display and read as text, so its
wording can change with the engine without a bump. The tests assert on presence
and shape, never on exact sentences. `Column detail`'s *contents* are likewise
descriptive, though the key it lives under is pinned.

## Verification

| Where | What it checks |
| --- | --- |
| `tests/test_client_projection.py` | Tag presence, well-formedness, bounds, and the untagged-object-list rule |
| `tests/test_forecast.py` | Cadence inference, projected dates, interval bounds |
| `scripts/verify-reports.ts` | The stored document round-trips; each destructured key survives `jsonb` |

The client harness is the one that would have caught a store-side rewrite, so it
carries the full tagged shapes rather than a flattened approximation — a fixture
that merely *resembles* the service output proves nothing about whether the rich
nodes survive.
