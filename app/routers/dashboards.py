from fastapi import APIRouter, Request, Depends
from sqlalchemy.orm import Session
from fastapi.responses import HTMLResponse

from app.database import get_session
from app.models.models import Applications


router = APIRouter(prefix="/dashboards", tags=["dashboards"])


@router.get("/")
def dashboards_home(request: Request, db: Session = Depends(get_session)):
    """Dashboards home page."""
    return {"message": "Dashboards"}