"""Pure statistical forecasting engine for SiroQ.

Classical, deterministic time-series methods only — no ML / AI, no fitted model
artifacts, no external statistics dependencies. Given the same input, the same
forecast is always produced (no randomness anywhere).

Methods:
  * ``naive``          — repeat the last observed value
  * ``mean``           — global average (flat series)
  * ``moving_average`` — average of the last ``win`` points
  * ``linear``         — least-squares straight-line trend
  * ``seasonal``       — linear trend + additive weekly (day-of-week) factors
  * ``damped_trend``   — Holt linear smoothing, dampened toward a flat level

A small holdout (default the last ``min(7, n // 3)`` points) selects the method
with the best out-of-sample error; the winner is refit on the full series and
extrapolated ``horizon`` steps. A prediction interval is derived from the
residual standard error and grows with ``sqrt(step)`` like a random walk.

Everything is implemented from scratch in pure Python ``math`` — no scipy, no
statsmodels, no numpy. The grade-9 linear algebra (Gaussian elimination for the
small normal equations) lives here so nothing slips into requirements.txt.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any, Callable, Sequence

from app.analytics_service.registry import forecast_method, forecast_methods

# z-scores for the prediction interval widths we support.
_Z: dict[float, float] = {
    0.80: 1.28155,
    0.90: 1.64485,
    0.95: 1.95996,
}


def _coerce_float(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _parse_datetime(value: Any) -> datetime | None:
    """Best-effort date parsing (ISO dominates; a few common formats fallback)."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if not isinstance(value, str):
        return None
    s = value.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")) if "T" in s else None
    except ValueError:
        return None


def _solve_linear(a: list[list[float]], b: list[float]) -> list[float] | None:
    """Solve ``a.x = b`` by Gaussian elimination (small, dense systems only)."""
    n = len(a)
    m = [row[:] + [bv] for row, bv in zip(a, b)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            return None
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(n):
            if r == col:
                continue
            factor = m[r][col] / m[col][col]
            for c in range(col, n + 1):
                m[r][c] -= factor * m[col][c]
    return [m[i][n] / m[i][i] for i in range(n)]


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _linfit(xs: Sequence[float], ys: Sequence[float]) -> tuple[float, float]:
    """Least-squares intercept/slope for ``y = a + b*x``."""
    n = len(xs)
    mx, my = _mean(xs), _mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx if sxx else 0.0
    return my - b * mx, b


def _r2(ys: Sequence[float], fitted: Sequence[float]) -> float | None:
    n = len(ys)
    if n == 0:
        return None
    my = _mean(ys)
    ss_tot = sum((y - my) ** 2 for y in ys)
    if not ss_tot:
        return None
    ss_res = sum((y - f) ** 2 for y, f in zip(ys, fitted))
    return 1.0 - ss_res / ss_tot


def _mape(actual: Sequence[float], pred: Sequence[float]) -> float | None:
    """MAPE over nonzero actuals (None when nothing is comparable)."""
    errs, denoms = [], 0.0
    n = min(len(actual), len(pred))
    for a, p in zip(actual[:n], pred[:n]):
        if abs(a) > 1e-9:
            errs.append(abs(p - a) / abs(a))
            denoms += 1
    return (sum(errs) / denoms) if denoms else None


def _mase(actual: Sequence[float], pred: Sequence[float]) -> float | None:
    """Mean absolute error scaled by the mean absolute one-step difference."""
    n = min(len(actual), len(pred))
    if n == 0:
        return None
    naive_den = sum(abs(b - a) for a, b in zip(actual, actual[1:]))
    if not naive_den:
        return None
    return (sum(abs(a - p) for a, p in zip(actual[:n], pred[:n])) / n) / (naive_den / max(len(actual) - 1, 1))


# --- candidate models -------------------------------------------------------
# Each ``(_name, _fn, _params)`` entry: ``fn(ys, seasons, steps, **params)``
# returns ``(fitted[len(ys)], future[steps])``. ``seasons`` is the full season
# axis (length >= len(ys) + steps); model k-th future step reads ``seasons[
# len(ys) + k]`` so weekly alignment is position-correct even for holdouts.


@forecast_method("naive", order=0, description="Naive: repeat the last observed value.")
def _mod_naive(ys, seasons, steps, **kw):
    n = len(ys)
    fitted = [ys[0]] * n if n else []
    future = [ys[-1]] * steps if n else [0.0] * steps
    return fitted, future


@forecast_method("mean", order=1, description="Mean forecast: flat at the historical average.")
def _mod_mean(ys, seasons, steps, **kw):
    m = _mean(ys)
    return [m] * len(ys), [m] * steps


def _mod_ma(ys, seasons, steps, win=3, **kw):
    fitted = []
    for i in range(len(ys)):
        lo = max(0, i - win + 1)
        fitted.append(sum(ys[lo:i + 1]) / (i - lo + 1))
    if not ys:
        return [], [0.0] * steps
    base = ys[-min(win, len(ys)):]
    m = _mean(base)
    return fitted, [m] * steps


# Two moving-average windows compete as separate holdout candidates; the score
# for the shared name is the last-registered (win=7) one, and the final fit uses
# the first (win=3) tolerance — exactly the historical selection behaviour.
forecast_methods.register("moving_average", _mod_ma, allow_dup=True, params={"win": 3}, order=2,
                          description="Moving average of recent points.")
forecast_methods.register("moving_average", _mod_ma, allow_dup=True, params={"win": 7}, order=2,
                          description="Moving average of recent points.")


@forecast_method("linear", order=3, description="Least-squares linear trend extrapolated forward.")
def _mod_linear(ys, seasons, steps, **kw):
    n = len(ys)
    if n == 0:
        return [], [0.0] * steps
    xs = list(range(n))
    a, b = _linfit(xs, ys)
    fitted = [a + b * i for i in range(n)]
    future = [a + b * (n + s) for s in range(steps)]
    return fitted, future


@forecast_method("seasonal", order=5, description="Linear trend plus additive weekly seasonality.")
def _mod_seasonal(ys, seasons, steps, period=7, **kw):
    """Linear trend + additive seasonal factors (dummy per season, one dropped)."""
    n = len(ys)
    if n == 0:
        return [], [0.0] * steps
    p = max(2, int(period))
    q = p - 1
    ncol = 2 + q
    xtx = [[0.0] * ncol for _ in range(ncol)]
    xty = [0.0] * ncol
    for i, y in enumerate(ys):
        x = [1.0, float(i)] + [0.0] * q
        s = seasons[i] % p if seasons else (i % p)
        if s < q:
            x[2 + s] = 1.0
        for r in range(ncol):
            xty[r] += x[r] * y
            for c in range(ncol):
                xtx[r][c] += x[r] * x[c]
    coef = _solve_linear(xtx, xty)
    if coef is None:
        return _mod_linear(ys, seasons, steps)

    def _x(pos: int) -> list[float]:
        x = [1.0, float(pos)] + [0.0] * q
        s = seasons[pos] % p if seasons else (pos % p)
        if s < q:
            x[2 + s] = 1.0
        return x

    fitted = [_dot(coef, _x(i)) for i in range(n)]
    future = [_dot(coef, _x(n + s)) for s in range(steps)]
    return fitted, future


def _holt_run(ys, alpha, beta, phi):
    n = len(ys)
    fitted = [None] * n
    l = float(ys[0])
    b = float(ys[1] - ys[0]) if n >= 2 else 0.0
    fitted[0] = float(ys[0])
    sse = 0.0
    for i in range(1, n):
        pred = l + b
        err = float(ys[i]) - pred
        sse += err * err
        fitted[i] = pred
        new_l = alpha * float(ys[i]) + (1 - alpha) * (l + b)
        new_b = beta * (new_l - l) + (1 - beta) * b
        l, b = new_l, new_b
    return fitted, sse, l, b


@forecast_method("damped_trend", order=4, description="Holt exponential smoothing with a dampened trend.")
def _mod_damped(ys, seasons, steps, phi=0.9, **kw):
    n = len(ys)
    if n == 0:
        return [], [0.0] * steps
    if n < 3:
        return _mod_naive(ys, seasons, steps)
    grid = [round(0.1 + 0.1 * i, 1) for i in range(9)]  # 0.1 .. 0.9
    best = None
    for alpha in grid:
        for beta in grid:
            fitted, sse, l, b = _holt_run(ys, alpha, beta, phi)
            if best is None or sse < best[0]:
                best = (sse, fitted, l, b)
    _sse, fitted, l, b = best
    future = [l + b * sum(phi ** j for j in range(1, s + 2)) for s in range(steps)]
    return fitted, future


def _build_models() -> list[tuple[str, Callable, dict]]:
    """Materialise the candidate model table from the forecast registry.

    Keeps registration order, so the selection and final-fit semantics (last
    entry of a repeated name wins the holdout score; first entry wins the fit)
    are identical to the historical hard-coded list.
    """
    models = []
    for entry in forecast_methods.all():
        params = dict(entry.meta.get("params", {}))
        models.append((entry.name, entry.fn, params))
    return models


def _build_preference() -> list[str]:
    """Method tie-break order: first registration order, then ``order`` meta."""
    named: dict[str, tuple] = {}
    for entry in forecast_methods.all():
        named.setdefault(entry.name, (entry.meta.get("order", 100), entry.name))
    return [name for name, _ in sorted(named.values(), key=lambda kv: kv[0])]


_MODELS: list[tuple[str, Callable, dict]] = _build_models()
_PREFERENCE = _build_preference()


def _order_score(name: str) -> int:
    try:
        return _PREFERENCE.index(name)
    except ValueError:
        return len(_PREFERENCE)


# --- public API -------------------------------------------------------------


def forecast(
    values: Sequence[Any],
    dates: Sequence[Any] | None = None,
    horizon: int = 14,
    period: int = 7,
    confidence: float = 0.90,
    holdout: int | None = None,
) -> dict[str, Any]:
    """Forecast a univariate series with deterministic classical methods.

    :param values: numeric observations (``None``/NaN entries are dropped).
    :param dates: aligned date labels (ISO strings or date objects); optional.
    :param horizon: number of future steps to predict (1..365).
    :param period: seasonal period (default 7 for weekly). ``seasonal`` is only
        fitted when enough history is available.
    :param confidence: prediction-interval confidence (0.80/0.90/0.95 rounded).
    :param holdout: override the holdout size in method selection.
    :return: dict with ``series`` (history + fitted), ``forecast`` (step +
        value + lower/upper), ``method``, ``horizon``, ``confidence``, and
        ``diagnostics``.
    """
    pairs = []
    for idx, v in enumerate(values):
        f = _coerce_float(v)
        if f is None:
            continue
        d = None
        if dates and idx < len(dates):
            d = dates[idx]
        pairs.append((d, f))
    if not pairs:
        return {
            "series": [],
            "forecast": [],
            "method": None,
            "horizon": horizon,
            "confidence": confidence,
            "diagnostics": {"notes": ["no valid numeric observations to forecast"]},
        }
    # normalise in ascending date order when dates parse, else keep input order
    with_dates = [p for p in pairs if _parse_datetime(p[0]) is not None]
    if with_dates:
        pairs = sorted(pairs, key=lambda p: (_parse_datetime(p[0]) or datetime.min, 0))

    ys = [float(p[1]) for p in pairs]
    n = len(ys)
    horizon = max(1, min(365, int(horizon)))
    notes: list[str] = []
    if n != len(values):
        notes.append(f"dropped {len(values) - n} non-finite observation(s)")

    parsed = [_parse_datetime(p[0]) for p in pairs]

    def _season(i: int) -> int:
        if parsed and parsed[0] is not None:
            base = parsed[0] + timedelta(days=i)
            return base.weekday()
        return (i % max(2, int(period)))

    seasons = [_season(i) for i in range(n + horizon)]

    holdout_len = holdout
    if holdout_len is None:
        holdout_len = max(2, min(14, n // 5))
    holdout_len = min(holdout_len, n - 2) if n > 2 else 1
    holdout_len = max(1, holdout_len)

    # --- method selection on a holdout slice -------------------------------
    # Score combines the holdout error (70%) with the in-sample error (30%) so
    # a flat method that happens to win a noisy short window is still penalised
    # for missing the overall shape (helps trending/seasonal series).
    winner: str | None = None
    scores: dict[str, float] = {}
    holdout_errors: dict[str, float] = {}
    h = max(1, n - holdout_len)
    if holdout_len >= 1 and h >= 2:
        for name, fn, params in _MODELS:
            try:
                _tr, pred = fn(ys[:h], seasons, holdout_len, **params)
                f_full, _ft = fn(ys, seasons, 1, **params)
            except Exception:  # defensive — one model must never break the run
                continue
            h_err = _mape(ys[h:], pred)
            if h_err is None:
                h_err = _mase(ys[h:], pred)
            if h_err is None:
                continue
            in_err = _mape(ys, f_full)
            if in_err is None:
                in_err = h_err
            holdout_errors[name] = h_err
            scores[name] = 0.7 * h_err + 0.3 * in_err
    if scores:
        winner = min(scores, key=lambda k: (scores[k], _order_score(k)))

    if winner is None:
        winner = "naive"

    # --- final fit over the full history -----------------------------------
    def _run_model(name: str) -> tuple[list[float], list[float]]:
        for mname, fn, params in _MODELS:
            if mname == name:
                return fn(ys, seasons, horizon, **params)
        return _mod_naive(ys, seasons, horizon)

    fitted, future = _run_model(winner)

    residuals = [(a - f) for a, f in zip(ys, fitted)]
    rmse = math.sqrt(sum(r * r for r in residuals) / max(len(residuals), 1))
    conf = min(sorted(_Z), key=lambda k: abs(k - float(confidence)))
    z = _Z[conf]
    sigma = max(rmse, 1e-9)

    series = []
    for i, ((d, _v), f) in enumerate(zip(pairs, fitted)):
        series.append({
            "date": d if d is not None else i,
            "value": round(ys[i], 4),
            "fitted": round(f, 4),
        })

    forecast_row = []
    clamp_lower = min(ys, default=0) >= 0
    for s in range(horizon):
        v = future[s]
        sd = sigma * math.sqrt(s + 1)
        lo = v - z * sd
        hi = v + z * sd
        if clamp_lower:
            lo = max(0.0, lo)
            hi = max(lo, hi)
        forecast_row.append({
            "step": s + 1,
            "date": (parsed[-1] + timedelta(days=s + 1))
            if parsed and parsed[-1] is not None else (n + s),
            "value": round(v, 4),
            "lower": round(lo, 4),
            "upper": round(hi, 4),
        })

    # diagnostics from the winning fit
    slope = None
    if winner in ("linear", "seasonal"):
        xs = list(range(n))
        a, slope = _linfit(xs, ys)
    elif winner == "damped_trend":
        _f, _s, l, b = _holt_run(ys, 0.5, 0.5, 0.9)
        slope = b

    diagnostics: dict[str, Any] = {
        "method": winner,
        "holdout": holdout_len,
        "holdout_error": None,
        "rmse": round(rmse, 4),
        "mape": _mape(ys, fitted),
        "r2": _r2(ys, fitted),
        "slope": round(slope, 6) if slope is not None else None,
        "points": n,
        "notes": notes,
    }
    if winner and holdout_errors.get(winner) is not None:
        diagnostics["holdout_error"] = round(holdout_errors[winner], 6)
        diagnostics["selection_score"] = round(scores[winner], 6)
        diagnostics["considered"] = {k: round(v, 6) for k, v in sorted(holdout_errors.items(), key=lambda kv: kv[1])}

    return {
        "series": series,
        "forecast": forecast_row,
        "method": winner,
        "horizon": horizon,
        "period": period,
        "confidence": conf,
        "diagnostics": diagnostics,
    }


def describe(builder_name: str) -> str:
    """One-line human description for the landed method (used by the UI).

    Falls back to the registered method description, then to the builder name.
    """
    description = forecast_methods.meta(builder_name).get("description")
    return description or builder_name or "n/a"