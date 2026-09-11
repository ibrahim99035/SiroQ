from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.database import engine, SessionLocal
from app.models.models import Base
from app.routers.auth import router as auth_router
from app.routers.admin import router as admin_router
from app.routers.applications import router as applications_router
from app.routers.data_explorer import router as data_explorer_router
from app.routers.dashboards import router as dashboards_router

# Create database tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="SiroQ", description="Pharmacy sales & inventory management")

# Add session middleware for auth cookies
from starlette.middleware.sessions import SessionMiddleware
app.add_middleware(
    SessionMiddleware,
    secret=settings.SECRET_KEY,
    same_site="lax",
    downcase_keys=True,
)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Jinja2 templates
templates = Jinja2Templates(directory="app/templates")

# Include routers
app.include_router(auth_router, prefix="/auth", tags=["auth"])
app.include_router(admin_router, prefix="/admin", tags=["admin"])
app.include_router(applications_router, prefix="/applications", tags=["applications"])
app.include_router(data_explorer_router, prefix="/data_explorer", tags=["data_explorer"])
app.include_router(dashboards_router, prefix="/dashboards", tags=["dashboards"])


@app.get("/")
async def root():
    return {"message": "SiroQ API"}


@app.get("/health")
async def health():
    return {"status": "ok"}