from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_session
from app.models.models import Users


def get_current_user(request: Request, db: Session = Depends(get_session)):
    """Load user from session, redirect to login if absent."""
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse(url="/login", status_code=303)
    user = db.query(Users).filter(Users.id == user_id).first()
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return user


def require_role(*allowed_roles):
    """FastAPI dependency that checks user role, returns 403 HTML if not authorized."""

    def dependency(current_user: Users = Depends(get_current_user)):
        if current_user.role not in allowed_roles:
            html = open("app/templates/403.html").read()
            return HTMLResponse(content=html, status_code=status.HTTP_403_FORBIDDEN)
        return current_user

    return dependency


def set_tenant_context(db: Session = Depends(get_session), current_user=Depends(get_current_user)):
    """Set the session-level RLS variable for the current transaction."""
    from sqlalchemy import text
    db.execute(
        text("SET LOCAL app.current_association_id = :aid"),
        {"aid": str(current_user.association_id)},
    )
    return db