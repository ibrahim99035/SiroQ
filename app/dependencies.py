from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_session
from app.models.models import Users


class Unauthenticated(HTTPException):
    pass


def get_current_user(request: Request, db: Session = Depends(get_session)):
    """Load the authenticated user from the signed session cookie.

    Redirects (303) to /login when there is no valid session. When a valid
    user is loaded, the Row-Level-Security tenant variable is set inside the
    same transaction so every following query in this request is tenant-scoped.
    """
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})

    # Re-load from the DB so we always reflect the latest role/scope/active state.
    from app.routers.auth import visible_user_by_id
    user = visible_user_by_id(db, user_id)
    if not user or not user.active:
        raise HTTPException(status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})

    # Set the RLS tenant context immediately after loading the user, in the
    # same transaction that subsequent queries in this request will use.
    # SET LOCAL cannot take bind parameters, so set_config(text, value,
    # is_local=true) is used instead — semantically identical to SET LOCAL.
    db.execute(
        text("SELECT set_config('app.current_association_id', :aid, true)"),
        {"aid": str(user.association_id)},
    )
    return user


def require_role(*allowed_roles):
    """Dependency factory that enforces a non-empty allow-list and always
    denies on mismatch (no fail-open branch)."""
    allowed = set(allowed_roles)
    if not allowed:
        raise RuntimeError("require_role() must be given at least one role")

    def dependency(current_user: Users = Depends(get_current_user)):
        if current_user.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Access denied")
        return current_user

    return dependency


def set_tenant_context(db: Session = Depends(get_session), current_user=Depends(get_current_user)):
    """Explicit tenant-context dependency for routes that need the db plus the
    RLS variable already wired (ensure the dependency graph resolves once)."""
    db.execute(
        text("SELECT set_config('app.current_association_id', :aid, true)"),
        {"aid": str(current_user.association_id)},
    )
    return db
