from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_session
from app.dependencies import require_role
from app.models.models import Users, Associations, Pharmacies
from app.security import hash_password

router = APIRouter(tags=["admin"])
templates = Jinja2Templates(directory="app/templates")

ADMIN_ROLE = require_role("association_admin")
VALID_ROLES = ["association_admin", "pharmacy_manager", "analyst", "data_steward", "viewer"]


@router.get("/")
def admin_home(request: Request, user: Users = Depends(ADMIN_ROLE)):
    return templates.TemplateResponse(request, "admin/home.html", {"request": request, "user": user})


@router.get("/associations")
def associations_list(request: Request, db: Session = Depends(get_session),
                      user: Users = Depends(ADMIN_ROLE)):
    associations = db.query(Associations).order_by(Associations.created_at).all()
    return templates.TemplateResponse(
        request, "admin/associations.html", {"request": request, "user": user,
                                     "associations": associations}
    )


@router.post("/associations")
def associations_create(request: Request, db: Session = Depends(get_session),
                        user: Users = Depends(ADMIN_ROLE),
                        name: str = Form(...)):
    assoc = Associations(name=name)
    db.add(assoc)
    db.commit()
    return RedirectResponse(url="/admin/associations", status_code=303)


@router.get("/pharmacies")
def pharmacies_list(request: Request, db: Session = Depends(get_session),
                    user: Users = Depends(ADMIN_ROLE)):
    pharmacies = db.query(Pharmacies).order_by(Pharmacies.name).all()
    associations = db.query(Associations).all()
    return templates.TemplateResponse(
        request, "admin/pharmacies.html", {"request": request, "user": user,
                                   "pharmacies": pharmacies, "associations": associations}
    )


@router.post("/pharmacies")
def pharmacies_create(request: Request, db: Session = Depends(get_session),
                      user: Users = Depends(ADMIN_ROLE),
                      name: str = Form(...), association_id: str = Form(...)):
    assoc = db.query(Associations).get(association_id)
    if assoc:
        db.add(Pharmacies(name=name, association_id=assoc.id))
        db.commit()
    return RedirectResponse(url="/admin/pharmacies", status_code=303)


@router.get("/users")
def users_list(request: Request, db: Session = Depends(get_session),
               user: Users = Depends(ADMIN_ROLE)):
    users = db.query(Users).order_by(Users.email).all()
    pharmacies = db.query(Pharmacies).all()
    return templates.TemplateResponse(
        request, "admin/users.html", {"request": request, "user": user, "users": users,
                              "pharmacies": pharmacies, "valid_roles": VALID_ROLES}
    )


@router.post("/users")
def users_create(request: Request, db: Session = Depends(get_session),
                 user: Users = Depends(ADMIN_ROLE),
                 email: str = Form(...), full_name: str = Form(...),
                 role: str = Form(...), pharmacy_id: str | None = Form(None)):
    if role not in VALID_ROLES:
        return RedirectResponse(url="/admin/users", status_code=303)
    if role == "association_admin" and pharmacy_id:
        pharmacy_id = None  # admins are always association-wide
    if role in ("pharmacy_manager", "data_steward") and not pharmacy_id:
        return RedirectResponse(url="/admin/users", status_code=303)
    new_user = Users(
        email=email,
        hashed_password=hash_password("ChangeMe123!"),
        full_name=full_name,
        role=role,
        association_id=user.association_id,
        pharmacy_id=pharmacy_id or None,
    )
    db.add(new_user)
    db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)