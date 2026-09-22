#!/usr/bin/env python
"""Cross-platform file-load probe for the SiroQ analysis service.

Usage:
    python bench/bench_runner.py analyze <file-or-folder>
    python bench/bench_runner.py ingest  <file-or-folder>

Modes
  ingest   -> ingestion.read_file only (reports parse time + dataframe peak)
  analyze  -> read_file + _analyze_dataframe (the full per-file pipeline)

If PATH is a folder, every supported file in it is probed (one JSON line each)
in ingest then analyze order. Peak memory uses /proc-style rusage on POSIX,
psutil on Windows (works both), and falls back to tracemalloc (Python heap
only) when psutil is missing.

Each JSON line:
{"scenario", "path", "size", "rows", "cols", "read_s", "analyze_s", "peak_kb"}
"""
import json
import os
import sys
import time

# Ensure the repo's app/ package is importable when this file is run directly
# (python bench/bench_runner.py ... puts bench/ at sys.path[0], not the repo
# root, and the client machine may not have the wheel installed).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from app.analytics_service import ingestion, pipeline


def _rss_backend() -> str:
    """Pick the peak-RSS backend: 'resource' (POSIX) > 'psutil' > 'tracemalloc'."""
    try:
        import resource  # noqa: F401

        if hasattr(resource, "getrusage"):
            return "resource"
    except ImportError:
        pass
    try:
        import psutil  # noqa: F401
    except Exception:
        return "tracemalloc"
    return "psutil"


_PEAK_BACKEND = _rss_backend()


def rss_kb() -> int:
    if _PEAK_BACKEND == "resource":
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if _PEAK_BACKEND == "psutil":
        import psutil

        return psutil.Process().memory_info().rss // 1024
    import tracemalloc

    _current, peak = tracemalloc.get_traced_memory()
    return peak // 1024


def probe(mode: str, path: str) -> dict:
    with open(path, "rb") as fh:
        content = fh.read()

    t0 = time.perf_counter()
    ingested = ingestion.read_file(content, os.path.basename(path))
    read_s = time.perf_counter() - t0

    analyze_s = None
    if mode == "analyze":
        import tracemalloc as tm

        if _PEAK_BACKEND == "tracemalloc":
            tm.start()
            tm.reset_peak()
        t0 = time.perf_counter()
        pipeline._analyze_dataframe(ingested.dataframe)
        analyze_s = time.perf_counter() - t0
        if _PEAK_BACKEND == "tracemalloc":
            tm.stop()

    return {
        "scenario": os.environ.get("SCENARIO", os.path.basename(path)),
        "path": path,
        "size": len(content),
        "rows": int(len(ingested.dataframe)),
        "cols": int(len(ingested.dataframe.columns)),
        "read_s": round(read_s, 4),
        "analyze_s": round(analyze_s, 4) if analyze_s is not None else None,
        "peak_kb": rss_kb(),
    }


def main() -> None:
    mode, path = sys.argv[1], sys.argv[2]
    targets = (
        sorted(
            os.path.join(path, f)
            for f in os.listdir(path)
            if f.lower().endswith((".xls", ".xlsx", ".csv"))
        )
        if os.path.isdir(path)
        else [path]
    )
    modes = [mode] if mode == "ingest" else ["ingest", "analyze"]
    for target in targets:
        if os.path.isdir(target):
            continue
        for m in modes:
            print(json.dumps(probe(m, target)), flush=True)


if __name__ == "__main__":
    main()