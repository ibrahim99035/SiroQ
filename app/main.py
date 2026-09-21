"""SiroQ Analysis Service — a single service that ingests an application's
files in one request, deep-analyzes them, persists the results, and serves them
back on request to other services.
"""
from fastapi import FastAPI

from app.routers import analyses, applications

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


@app.get("/health", tags=["health"], include_in_schema=False)
def health():
    return {"status": "ok", "service": "siroq-analysis"}