"""Compatibility shim for the domain-analytics API.

The engines now live in :mod:`app.analytics_service.engines` (one module per
business category) and are dispatched through the registry. This module re-exports
the same interface so existing imports keep working unchanged.
"""
from __future__ import annotations

from app.analytics_service.engines import available_categories, run_domain_analytics
from app.analytics_service.registry import domain_engines

__all__ = ["available_categories", "domain_engines", "run_domain_analytics"]