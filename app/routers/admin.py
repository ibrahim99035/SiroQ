from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_session
from app.models.models import Users, Associations, Pharmacies


router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/associations")
def associations_list(request: Request, db: Session = Depends(get_session)):
    """List associations."""
    from app.models.models import Associations
    associations = db.query(Associations).all()
    return {"associations": associations}


@router.post("/associations")
def associations_create(
    request: Request,
    db: Session = Depends(get_session),
    name: str = Form(...),
):
    """Create a new association."""
    from app.models.models import Associations
    assoc = Associations(name=name)
    db.add(assoc)
    db.commit()
    db.refresh(assoc)
    return {"id": str(assoc.id)}


@router.get("/pharmacies")
def pharmacies_list(request: Request, db: Session = Depends(get_session)):
    """List pharmacies."""
    from app.models.models import Pharmacies
    pharmacies = db.query(Pharmacies).all()
    return {"pharmacies": pharmacies}


@router.post("/pharmacies")
def pharmacies_create(
    request: Request,
    db: Session = Depends(get_session),
    name: str = Form(...),
    association_id: str = Form(...),
):
    """Create a new pharmacy."""
    from app.models.models import Associations, Pharmacies
    assoc = db.query(Associations).get(association_id)
    if not assoc:
        return {"error": "Association not found"}
    pharmacy = Pharmacies(name=name, association_id=assoc.id)
    db.add(pharmacy)
    db.commit()
    db.refresh(pharmacy)
    return {"id": str(pharmacy.id)}


@router.get("/users")
def users_list(request: Request, db: Session = Depends(get_session)):
    """List users."""
    from app.models.models import Users
    users = db.query(Users).all()
    return {"users": users}


@router.post("/users")
def users_create(
    request: Request,
    db: Session = Depends(get_session),
    email: str = Form(...),
    full_name: str = Form(...),
    role: str = Form(...),
    pharmacy_id: str | None = Form(None),
):
    """Create a new user."""
    from app.models.models import Users, Associations, Pharmacies
    from app.security import hash_password
    from app.dependencies import require_role

    valid_roles = ["association_admin", "pharmacy_manager", "analyst", "data_steward", "viewer"]
    if role not in valid_roles:
        return {"error": f"Invalid role. Must be one of {valid_roles}"}

    # Check role permission for pharmacy_id
    if role in ("pharmacy_manager", "data_steward") and not pharmacy_id:
        return {"error": f"Role '{role}' requires a pharmacy_id"}

    user = Users(
        email=email,
        hashed_password=hash_password("ChangeMe123!"),
        full_name=full_name,
        role=role,
    )

    if pharmacy_id:
        pharmacy = db.query(Pharmacies).get(pharmacy_id)
        if pharmacy:
            user.pharmacy_id = pharmacy.id

    db.add(user)
    db.commit()
    db.refresh(user)
    return {"id": str(user.id), "role": user.role, "pharmacy_id": str(user.pharmacy_id) if user.pharmacy_id else None}