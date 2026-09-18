from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.routers.auth import router as auth_router
from app.routers.admin import router as admin_router
from app.routers.applications import router as applications_router, datasets_router
from app.routers.dashboards import router as dashboards_router

app = FastAPI(title="SiroQ", description="Pharmacy sales & inventory management")

# Expose settings on app.state so routers can read BRONZE_STORAGE_PATH etc.
app.state.settings = settings

from starlette.middleware.sessions import SessionMiddleware

from app.middleware import SameOriginGuard

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    same_site="lax",
)
# Added after the session middleware, so it wraps it and rejects cross-site
# posts before any session work happens.
app.add_middleware(SameOriginGuard)

# Mount static files (bootstrap RTL + custom.css are served from here)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Jinja2 templates
templates = Jinja2Templates(directory="app/templates")


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Render friendly HTML for the redirects and the 403 denial the auth
    dependencies raise, instead of leaking a bare JSON body."""
    if exc.status_code == 403:
        return HTMLResponse(status_code=403, content=open("app/templates/403.html").read())
    location = exc.headers.get("Location") if exc.headers else None
    if exc.status_code in (303, 307):
        return RedirectResponse(url=location or "/login", status_code=exc.status_code)
    return HTMLResponse(
        status_code=exc.status_code,
        content=f"<h1>{exc.status_code}</h1><p>{exc.detail}</p>",
    )


app.include_router(auth_router, tags=["auth"])
app.include_router(admin_router, prefix="/admin", tags=["admin"])
app.include_router(applications_router, prefix="/applications", tags=["applications"])
app.include_router(datasets_router, tags=["datasets"])
app.include_router(dashboards_router, prefix="/dashboards", tags=["dashboards"])


@app.get("/")
async def root():
    return RedirectResponse(url="/dashboards/sales", status_code=303)


@app.get("/health")
async def health():
    return {"status": "ok", "environment": settings.ENVIRONMENT}
