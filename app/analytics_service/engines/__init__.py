"""Domain analytics engines, one module per business category.

Importing this package registers every ``@domain_engine``-decorated engine in
``app.analytics_service.registry.domain_engines``. To add a new category:

1. create ``engines/<category>.py`` defining ``@domain_engine("<category>")``
2. import it here (the import is what registers it)
3. (optional) add column synonyms to ``classification.CANONICAL_FIELD_SYNONYMS``

See ``docs/ADDING_FEATURES.md`` for the full walkthrough.
"""
from __future__ import annotations

from typing import Any, Callable

import pandas as pd

from app.analytics_service.engines import inventory, purchase_orders, reference, sales
from app.analytics_service.registry import domain_engines


def run_domain_analytics(df: pd.DataFrame, category: str, fmap: dict) -> dict[str, Any]:
    """Dispatch to the registered domain engine for ``category``.

    Returns the engine's report dict, or a ``{"skipped": ...}`` document when no
    engine is registered for the category. The mapping ``fmap`` resolves
    canonical field names (``"total_amount"``, ``"sale_timestamp"``, ...) to the
    file's actual columns.
    """
    engine: Callable | None = domain_engines.get(category)
    if engine is None:
        return {"skipped": f"no domain engine for category {category!r}"}
    try:
        return engine(df, fmap)
    except Exception as exc:  # defensive: one engine must never break the run
        return {"category": category, "skipped": f"{type(exc).__name__}: {exc}"}


def available_categories() -> list[str]:
    """Registered domain-engine categories (useful for docs/tooling)."""
    return domain_engines.names()