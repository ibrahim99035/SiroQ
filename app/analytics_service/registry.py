"""Tiny, dependency-free registry primitives that make every extensible part of
the analysis service pluggable:

- ``domain_engines``   : per-category domain analytics (e.g. ``sales``)
- ``insight_rules``    : derived, calculated insights (trends, waste, Pareto)
- ``quality_checks``   : data-quality gates + the aggregated score
- ``forecast_methods`` : statistical forecast builders
- ``file_analyzers``   : per-dataframe pipeline stages
- ``file_readers``     : ingestion readers, keyed by file type

Each registry is ordered and collected at import time. To add a feature you
write a function and decorate it; nothing else needs editing (see
``docs/ADDING_FEATURES.md`` for the walkthroughs).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Entry:
    """One registered item: its name, the callable, and optional metadata."""

    name: str
    fn: Callable
    meta: dict[str, Any] = field(default_factory=dict)


class Registry:
    """Ordered name -> callable collection with decorator support.

    A name may hold several entries when ``allow_dup=True`` (e.g. two
    ``moving_average`` forecast candidates with different windows); the plain
    accessors return the first match, while :meth:`all` yields every entry in
    registration order.
    """

    def __init__(self, kind: str):
        self.kind = kind
        self._entries: dict[str, list[Entry]] = {}

    def register(
        self, name: str, fn: Callable | None = None, *, allow_dup: bool = False, **meta: Any
    ) -> Callable:
        """Register ``fn`` under ``name``. Works as a plain function call or a
        decorator (``fn`` omitted). Returns the callable unchanged."""

        def _wrap(callable_obj: Callable) -> Callable:
            bucket = self._entries.setdefault(name, [])
            if bucket and not allow_dup:
                raise ValueError(
                    f"Duplicate {self.kind} registration: {name!r}"
                    f" (pass allow_dup=True to register a variant)"
                )
            bucket.append(Entry(name, callable_obj, dict(meta)))
            return callable_obj

        if fn is None:
            return _wrap
        return _wrap(fn)

    def get(self, name: str) -> Callable | None:
        entry = self.entry(name)
        return entry.fn if entry else None

    def entry(self, name: str) -> Entry | None:
        bucket = self._entries.get(name)
        return bucket[0] if bucket else None

    def entries_for(self, name: str) -> list[Entry]:
        return list(self._entries.get(name, []))

    def all(self, name: str | None = None) -> list[Entry]:
        if name is not None:
            return list(self._entries.get(name, []))
        out: list[Entry] = []
        for bucket in self._entries.values():
            out.extend(bucket)
        return out

    def names(self) -> list[str]:
        return list(self._entries)

    def meta(self, name: str) -> dict[str, Any]:
        entry = self.entry(name)
        return dict(entry.meta) if entry else {}

    def __contains__(self, name: str) -> bool:
        return name in self._entries

    def __len__(self) -> int:
        return sum(len(b) for b in self._entries.values())


# --- shared registries ------------------------------------------------------

domain_engines = Registry("domain engine")
insight_rules = Registry("insight rule")
quality_checks = Registry("quality check")
forecast_methods = Registry("forecast method")
file_analyzers = Registry("file analyzer")
file_readers = Registry("file reader")


def domain_engine(category: str, *, order: float = 100.0) -> Callable:
    """Decorate a domain-analytics engine, e.g. ``@domain_engine("sales")``.

    The engine is called as ``fn(df: pd.DataFrame, fmap: dict) -> dict`` and its
    return value becomes the file's ``domain_analytics`` document.
    """
    return domain_engines.register(category, order=order)


def insight_rule(
    key: str,
    *,
    family: str = "general",
    order: float = 100.0,
    requires: tuple[str, ...] = (),
) -> Callable:
    """Decorate a calculated insight, e.g. ``@insight_rule("pareto_80")``.

    The rule is called as ``fn(ctx) -> dict`` where ``ctx`` is the
    :class:`~app.analytics_service.insights.InsightContext` passed in. It must
    always return a dict shaped like
    ``{"value": ..., "unit": ..., "detail": ..., "evidence": {...}}``; the
    dispatcher fills in ``key``/``family``/``status``/``severity``.

    Rules never raise: a rule that cannot find its input columns returns a
    ``skipped`` result naming what was missing, so one absent column degrades a
    single insight instead of failing the analysis.
    """
    return insight_rules.register(
        key, family=family, order=order, requires=tuple(requires)
    )


def quality_check(name: str, *, penalty: float = 0.0, needs_field_scores: bool = False) -> Callable:
    """Decorate a data-quality check, e.g. ``@quality_check("duplicate_rows", penalty=10)``.

    ``penalty`` is subtracted from 100 when the check does not pass.
    ``needs_field_scores=True`` marks checks that require the classification
    field map and are therefore skipped when classification failed.
    """
    return quality_checks.register(
        name,
        penalty=penalty,
        needs_field_scores=needs_field_scores,
    )


def forecast_method(name: str, *, description: str, order: int = 100) -> Callable:
    """Decorate a statistical forecast builder.

    The builder is called as ``fn(ys, seasons, steps, **params) -> (fitted,
    future)``. ``description`` feeds ``forecast.describe()``; ``order`` breaks
    ties during holdout selection (lower wins). Registering the same name twice
    (e.g. a method at two window sizes) adds both as holdout candidates.
    """
    return forecast_methods.register(
        name,
        description=description,
        order=order,
        allow_dup=True,
    )


def file_analyzer(name: str, *, order: float = 100.0) -> Callable:
    """Decorate a per-dataframe pipeline analyzer.

    The analyzer is called as ``fn(ctx) -> None`` where ``ctx`` is a dict with
    ``df``, ``section`` (the report slice) and shared keys described in
    ``pipeline.py``. Stage ``order`` controls execution order (lower first).
    """
    return file_analyzers.register(name, order=order)


def file_reader(file_type: str) -> Callable:
    """Decorate an ingestion reader for ``file_type``.

    The reader is called as ``fn(content: bytes, filename: str) -> ReaderResult``
    (see ``ingestion.py``).
    """
    return file_readers.register(file_type)