"""SiroQ Analysis Service — a single service that ingests an application's
files in one request, deep-analyzes them, persists the results, and serves them
back on request to other services.
"""
import hashlib
import json
import logging
from pathlib import Path

from fastapi import FastAPI, Response, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.config import settings
from app.database import SessionLocal
from app.routers import analyses, applications

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

DASHBOARD_DIR = Path(__file__).resolve().parent / "dashboard"

app = FastAPI(
    title="SiroQ Analysis Service",
    description=(
        "Receive files for an application in one request, deeply analyze them "
        "(profile + classify + data-quality + best-effort domain analytics), "
        "persist raw files and results, and return them on later requests."
    ),
    version="0.1.0",
)

app.include_router(applications.router)
app.include_router(analyses.router)


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/dashboard/", status_code=307)


class NoCacheStaticFiles(StaticFiles):
    """Serve dashboard assets so a browser can never reuse a stale copy.

    ``no-cache`` only forces revalidation, so an aggressive browser (Brave, or
    Chrome with an explicit disk cache) may still reuse the stored body. The
    dashboard's JS and CSS are edited in place, so a stale ``app.js`` silently
    keeps running the previous build. ``no-store`` removes the option.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response


BUILD_FILES = ("app.js", "charts.js", "styles.css")


def _dashboard_build() -> str:
    """Fingerprint the dashboard assets so the page can detect a stale build."""
    parts = []
    for name in BUILD_FILES:
        path = DASHBOARD_DIR / name
        try:
            stat = path.stat()
            parts.append(f"{name}:{stat.st_mtime_ns}:{stat.st_size}")
        except OSError:
            parts.append(f"{name}:missing")
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]


if DASHBOARD_DIR.is_dir():
    @app.api_route("/dashboard/", methods=["GET", "HEAD"], include_in_schema=False)
    def dashboard_index() -> Response:
        """Serve index.html.

        Cache headers on every dashboard asset are ``no-store`` (see
        :class:`_NoStoreStaticFiles`), so a reload always picks up the current
        build. There is deliberately no build-id meta tag and no build.json
        endpoint: the freshness check they powered kept showing a "newer build
        available" banner at the top of the dashboard after any edit, including
        edits to files the dashboard does not even use.
        """
        html = (DASHBOARD_DIR / "index.html").read_text(encoding="utf-8")
        return Response(
            content=html,
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    app.mount(
        "/dashboard",
        NoCacheStaticFiles(directory=str(DASHBOARD_DIR)),
        name="dashboard",
    )


@app.get("/health", tags=["health"], include_in_schema=False)
def health(response: Response):
    db_up = True
    try:
        with SessionLocal() as session:
            session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - health must never 500
        logging.getLogger(__name__).warning("health db check failed: %s", exc)
        db_up = False
    if not db_up:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "degraded", "service": "siroq-analysis", "db": "down"}
    return {"status": "ok", "service": "siroq-analysis", "db": "up"}