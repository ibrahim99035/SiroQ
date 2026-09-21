"""SiroQ Analysis Service — a single service that ingests an application's
files in one request, deep-analyzes them, persists the results, and serves them
back on request to other services.
"""
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


if DASHBOARD_DIR.is_dir():
    app.mount(
        "/dashboard",
        StaticFiles(directory=str(DASHBOARD_DIR), html=True),
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