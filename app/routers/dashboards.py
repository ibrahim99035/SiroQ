from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_session
from app.dependencies import require_role
from app.models.models import Users
from app.services.analytics import sales_kpis, inventory_kpis

router = APIRouter(tags=["dashboards"])
templates = Jinja2Templates(directory="app/templates")

ALL_ROLES = ("association_admin", "pharmacy_manager", "analyst", "data_steward", "viewer")


@router.get("/sales", response_class=HTMLResponse)
def sales_dashboard(
    request: Request,
    db: Session = Depends(get_session),
    user: Users = Depends(require_role(*ALL_ROLES)),
):
    k = sales_kpis(db, user.association_id, pharmacy_id=user.pharmacy_id)
    return templates.TemplateResponse(
        request, "dashboards/sales.html", {"request": request, "user": user, **k}
    )


@router.get("/inventory", response_class=HTMLResponse)
def inventory_dashboard(
    request: Request,
    db: Session = Depends(get_session),
    user: Users = Depends(require_role(*ALL_ROLES)),
):
    k = inventory_kpis(db, user.association_id, pharmacy_id=user.pharmacy_id)
    return templates.TemplateResponse(
        request, "dashboards/inventory.html", {"request": request, "user": user, **k}
    )