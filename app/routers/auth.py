from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_session
from app.security import verify_password

router = APIRouter(tags=["auth"])


def visible_user_by_email(db: Session, email: str):
    """Authenticate a credential lookup under Row-Level-Security.

    ``users`` is RLS-protected on ``association_id``. Before authentication we
    know neither the association nor the tenant context, so the exact-email
    lookup runs through the ``auth_find_user`` SECURITY DEFINER function (owned
    by the migration role, which carries BYPASSRLS) that returns only the
    single matching row.
    """
    return db.execute(
        text("SELECT id, association_id, hashed_password, role, full_name, "
             "active, pharmacy_id FROM auth_find_user(:email)"),
        {"email": email},
    ).mappings().first()


def visible_user_by_id(db: Session, user_id):
    return db.execute(
        text("SELECT id, association_id, hashed_password, role, full_name, "
             "active, pharmacy_id FROM auth_find_user_by_id(:uid)"),
        {"uid": str(user_id)},
    ).mappings().first()


def _login_page(request: Request, error):
    html = open("app/templates/login.html").read()
    if error:
        html = html.replace(
            '<button class="w-100 btn btn-lg btn-primary mt-3" type="submit">Sign in</button>',
            f'<div class="alert alert-danger mt-3">{error}</div>'
            '<button class="w-100 btn btn-lg btn-primary mt-3" type="submit">Sign in</button>',
        )
    return html


@router.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse(url="/dashboards/sales", status_code=303)
    return HTMLResponse(_login_page(request, error=None), status_code=200)


@router.post("/login", response_class=HTMLResponse)
def login_post(
    request: Request,
    db: Session = Depends(get_session),
    email: str = Form(...),
    password: str = Form(...),
):
    user = visible_user_by_email(db, email)
    if not user or not verify_password(password, user["hashed_password"]):
        return HTMLResponse(_login_page(request, "Incorrect email or password"), status_code=400)
    if not user["active"]:
        return HTMLResponse(_login_page(request, "This account is disabled"), status_code=403)
    request.session["user_id"] = str(user["id"])
    return RedirectResponse(url="/dashboards/sales", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)