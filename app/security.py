"""Service-to-service authentication: a single shared static API key.

Deny-by-default: any request without a matching ``X-API-Key`` is rejected with
401 — there is no pass-through branch.
"""
import hmac

from fastapi import HTTPException, Request

from app.config import settings


def require_api_key(request: Request) -> None:
    key = request.headers.get("X-API-Key", "")
    if not key or not hmac.compare_digest(key, settings.API_KEY):
        raise HTTPException(status_code=401, detail="Missing or invalid API key")