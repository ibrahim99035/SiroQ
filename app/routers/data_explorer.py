from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_session
from app.models.models import Applications


router = APIRouter(prefix="/data_explorer", tags=["data_explorer"])


@router.get("/")
def explorer_home(request: Request, db: Session = Depends(get_session)):
    """Data explorer home page."""
    return {"message": "Data Explorer"}