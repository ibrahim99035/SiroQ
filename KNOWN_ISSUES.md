# KNOWN_ISSUES.md

## Explicitly Deferred Items (Phase 2+)

| Severity | Deferred Item | Reason |
|---|---|---|
| Low | LLM classification fallback | LLM-based column disambiguation is complex and error-prone; rapidfuzz + content-based inference is the Phase 1 approach. |
| Medium | Bulk/zip upload | Bulk/zip upload explicitly deferred — Phase 1 supports single CSV/XLSX only. |
| Medium | Prescriber/Geographic/Cross-Pharmacy analytics | These modules require additional data (prescriber databases, geo coordinates, cross-pharmacy comparison data) not available in Phase 1. |
| Medium | Forecasting module | Requires time-series data and statistical models beyond Phase 1 scope. |
| Medium | Anomaly/Fraud detection | Requires historical baseline data and ML models. |
| High | RxNorm/NDC drug normalization | Requires external drug reference data API integration. |
| Medium | S3/MinIO migration for Bronze storage | Local file system Bronze storage is Phase 1; cloud migration is deferred. |
| Low | Multi-currency FX conversion | Exchange rate data and conversion logic not implemented in Phase 1. |
| Medium | Scheduled/recurring feed ingestion | Requires cron/background scheduler infrastructure beyond APScheduler in-process jobs. |
| High | Webhooks | External system integration not in Phase 1 scope. |
| Medium | Multi-sheet Excel auto-splitting | Phase 1 handles single-Sheet XLSX; multi-sheet auto-splitting is deferred. |
| Medium | Cross-Pharmacy Benchmarking | Requires aggregated data across pharmacies with proper RBAC. |
| Low | Multi-tenancy isolation beyond association_id | Current RLS uses association_id; more granular tenant isolation is deferred. |
| Medium | Audit trail for ingestion pipeline | Ingestion audit logging is planned for Phase 2. |
| Low | Role-based dashboard customization | Dashboard layout customization per role is deferred. |

## Deferred Items Summary

- **LLM classification fallback**: LLM-based column disambiguation is complex and error-prone; rapidfuzz + content-based inference is the Phase 1 approach.
- **Bulk/zip upload**: Bulk/zip upload explicitly deferred — Phase 1 supports single CSV/XLSX only.
- **Prescriber/Geographic/Cross-Pharmacy analytics**: These modules require additional data (prescriber databases, geo coordinates, cross-pharmacy comparison data) not available in Phase 1.
- **Forecasting module**: Requires time-series data and statistical models beyond Phase 1 scope.
- **Anomaly/Fraud detection**: Requires historical baseline data and ML models.
- **RxNorm/NDC drug normalization**: Requires external drug reference data API integration.
- **S3/MinIO migration for Bronze storage**: Local file system Bronze storage is Phase 1; cloud migration is deferred.
- **Multi-currency FX conversion**: Exchange rate data and conversion logic not implemented in Phase 1.
- **Scheduled/recurring feed ingestion**: Requires cron/background scheduler infrastructure beyond APScheduler in-process jobs.
- **Webhooks**: External system integration not in Phase 1 scope.
- **Multi-sheet Excel auto-splitting**: Phase 1 handles single-Sheet XLSX; multi-sheet auto-splitting is deferred.
- **Cross-Pharmacy Benchmarking**: Requires aggregated data across pharmacies with proper RBAC.
- **Multi-tenancy isolation beyond association_id**: Current RLS uses association_id; more granular tenant isolation is deferred.
- **Audit trail for ingestion pipeline**: Ingestion audit logging is planned for Phase 2.
- **Role-based dashboard customization**: Dashboard layout customization per role is deferred.