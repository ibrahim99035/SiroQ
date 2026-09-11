from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_session
from app.models.models import Users
from app.security import verify_password, hash_password


router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login")
def login_get(request: Request):
    """Show login form."""
    return None  # template rendered by the route handler


@router.post("/login")
def login_post(
    request: Request,
    db: Session = Depends(get_session),
    email: str = Form(...),
    password: str = Form(...),
):
    """Verify credentials and set session."""
    from app.config import settings
    user = (
        db.query(Users)
        .filter(Users.email == email)
        .first()
    )
    if not user or not verify_password(password, user.hashed_password):
        from fastapi.responses import JSONResponse
        return JSONResponse(
            content={"detail": "Incorrect email or password"},
            status_code=400,
        )
    request.session["user_id"] = str(user.id)
    return RedirectResponse(url="/", status_code=303)


@router.post("/logout")
def logout(request: Request):
    """Clear session and redirect to login."""
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)