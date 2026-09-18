"""Application-level security tests.

Covers path-traversal neutralisation, zip-bomb/size guard rails, the Data
Explorer field allow-list and pharmacy scope on writes, and the production
secret guard.
"""
import io
import os
import uuid
import zipfile
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from app.config import DEV_SECRET_KEY, Settings, settings
from app.database import SessionLocal
from app.models.models import Applications, EditAuditLog, Pharmacies, Sales
from app.services.ingestion import extract_zip, safe_filename, save_bronze

DEMO_ASSOC_ID = "11111111-1111-4111-8111-111111111111"


# ---------------------------------------------------------------- filenames
def test_safe_filename_strips_directories_and_unsafe_chars():
    assert safe_filename("../../etc/passwd") == "passwd"
    assert safe_filename("..\\..\\windows\\evil.csv") == "evil.csv"
    assert safe_filename("/abs/path/sales.csv") == "sales.csv"
    assert safe_filename("..") == "upload"
    assert safe_filename("") == "upload"
    assert "/" not in safe_filename("a/b.csv")


def test_save_bronze_confines_writes_to_root(tmp_path):
    path = save_bronze(b"data", str(tmp_path), "assoc-1", "ds-1",
                       "../../../etc/passwd")
    root = str(tmp_path)
    assert os.path.commonpath([root, os.path.abspath(path)]) == root
    assert os.path.basename(path) == "passwd"


# ---------------------------------------------------------------- zip handling
def test_extract_zip_neutralises_zip_slip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../../evil.csv", "a,b\n1,2\n")
        zf.writestr("nested/dir/clean.csv", "a,b\n1,2\n")
    extracted = dict(extract_zip(buf.getvalue()))
    assert set(extracted) == {"evil.csv", "clean.csv"}
    assert not any("/" in name or ".." in name for name in extracted)


def test_extract_zip_enforces_uncompressed_cap():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.csv", "x" * 100_000)
    with pytest.raises(ValueError):
        extract_zip(buf.getvalue(), max_uncompressed=1000)


# ---------------------------------------------------------------- upload limits
def test_upload_over_size_limit_is_rejected(admin_session, monkeypatch):
    monkeypatch.setattr(settings, "MAX_UPLOAD_BYTES", 16)
    client = admin_session
    r = client.post("/applications/new",
                    data={"name": "Oversize Test", "source_type": "manual_upload"},
                    follow_redirects=False)
    app_id = r.headers["location"].rstrip("/").rsplit("/", 1)[-1]
    r = client.post(f"/applications/{app_id}/upload",
                    files={"file": ("big.csv", b"x" * 100, "text/csv")},
                    follow_redirects=False)
    assert r.status_code == 200, r.status_code
    assert "limit" in r.text, r.text[:300]


# ---------------------------------------------------------------- explorer writes
def _demo_sale_id():
    """Insert a sale in the demo tenant and return its id."""
    db = SessionLocal()
    try:
        db.execute(text("SELECT set_config('app.current_association_id', :a, true)"),
                   {"a": DEMO_ASSOC_ID})
        pharmacy = db.query(Pharmacies).filter(Pharmacies.name == "Demo Branch 1").one()
        application = Applications(id=str(uuid.uuid4()), association_id=DEMO_ASSOC_ID,
                                   name="Security Test App",
                                   source_type="manual_upload", status="active")
        db.add(application)
        db.flush()
        sale = Sales(id=str(uuid.uuid4()), association_id=DEMO_ASSOC_ID,
                     pharmacy_id=pharmacy.id, application_id=application.id,
                     sale_timestamp=datetime(2026, 9, 1, tzinfo=timezone.utc),
                     total_amount=10.0, currency="EGP")
        db.add(sale)
        sale_id = sale.id
        db.commit()
        return sale_id
    finally:
        db.close()


def test_explorer_rejects_field_outside_allowlist(admin_session, seeded_db):
    sale_id = _demo_sale_id()
    r = admin_session.post("/applications/x/explorer/cell",
                           data={"row_id": sale_id, "field": "association_id",
                                 "value": DEMO_ASSOC_ID})
    assert r.status_code == 400, r.status_code
    assert "not editable" in r.text


def test_explorer_rejects_non_numeric_amount(admin_session, seeded_db):
    sale_id = _demo_sale_id()
    r = admin_session.post("/applications/x/explorer/cell",
                           data={"row_id": sale_id, "field": "total_amount",
                                 "value": "not-a-number"})
    assert r.status_code == 400, r.status_code


def test_explorer_allowed_edit_persists_and_audits(admin_session, seeded_db):
    sale_id = _demo_sale_id()
    r = admin_session.post("/applications/x/explorer/cell",
                           data={"row_id": sale_id, "field": "total_amount",
                                 "value": "12.50"})
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        db.execute(text("SELECT set_config('app.current_association_id', :a, true)"),
                   {"a": DEMO_ASSOC_ID})
        assert float(db.query(Sales).get(sale_id).total_amount) == 12.50
        audit = db.query(EditAuditLog).filter(
            EditAuditLog.entity_id == sale_id,
            EditAuditLog.field == "total_amount",
        ).first()
        assert audit is not None, "edit must be audited"
        assert audit.new_value == "12.5"
    finally:
        db.close()


# ---------------------------------------------------------------- secret guard
def test_settings_refuse_default_secret_outside_development():
    with pytest.raises(ValidationError):
        Settings(ENVIRONMENT="production", SECRET_KEY=DEV_SECRET_KEY)


def test_settings_accept_a_real_secret_outside_development():
    s = Settings(ENVIRONMENT="production", SECRET_KEY="a-real-production-secret")
    assert s.SECRET_KEY == "a-real-production-secret"


# ---------------------------------------------------------------- same-origin guard
SECOND_ASSOC = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def _sale_in_other_tenant():
    """Insert a sale under a second, isolated tenant (owner role bypasses RLS).

    Follows the pattern of ``tests/test_rls.py``: create the association with
    the owner engine, then everything else as ``siroq_app`` under that tenant
    context, cleaning up in reverse dependency order afterwards.
    """
    from sqlalchemy import create_engine
    from app.config import settings

    owner = create_engine(settings.MIGRATIONS_DATABASE_URL, future=True)
    app_eng = create_engine(settings.DATABASE_URL, future=True)
    ids = {}
    with owner.begin() as conn:
        conn.execute(text(
            "INSERT INTO associations (id, name) VALUES (CAST(:a AS uuid), 'CSRF-Beta') "
            "ON CONFLICT DO NOTHING"), {"a": SECOND_ASSOC})
    try:
        with app_eng.begin() as conn:
            conn.execute(text(
                "SELECT set_config('app.current_association_id', :a, true)"),
                {"a": SECOND_ASSOC})
            row = conn.execute(text(
                "INSERT INTO pharmacies (id, association_id, name) "
                "VALUES (gen_random_uuid(), CAST(:a AS uuid), 'CSRF Branch') RETURNING id"),
                {"a": SECOND_ASSOC}).one()
            ids["pharmacy_id"] = str(row[0])
            row = conn.execute(text(
                "INSERT INTO applications (id, association_id, name, source_type, status) "
                "VALUES (gen_random_uuid(), CAST(:a AS uuid), 'CSRF App', 'manual_upload', "
                "'active') RETURNING id"), {"a": SECOND_ASSOC}).one()
            ids["application_id"] = str(row[0])
            row = conn.execute(text(
                "INSERT INTO sales (id, association_id, pharmacy_id, application_id, "
                "sale_timestamp, total_amount, currency) VALUES (gen_random_uuid(), "
                "CAST(:a AS uuid), CAST(:p AS uuid), CAST(:ap AS uuid), "
                "CAST('2026-09-01' AS timestamptz), 99.0, 'USD') RETURNING id"),
                {"a": SECOND_ASSOC, "p": ids["pharmacy_id"],
                 "ap": ids["application_id"]}).one()
            ids["sale_id"] = str(row[0])
    finally:
        with app_eng.begin() as conn:
            conn.execute(text(
                "SELECT set_config('app.current_association_id', :a, true)"),
                {"a": SECOND_ASSOC})
            conn.execute(text("DELETE FROM sales WHERE association_id=CAST(:a AS uuid)"),
                         {"a": SECOND_ASSOC})
            conn.execute(text("DELETE FROM applications WHERE association_id=CAST(:a AS uuid)"),
                         {"a": SECOND_ASSOC})
            conn.execute(text("DELETE FROM pharmacies WHERE association_id=CAST(:a AS uuid)"),
                         {"a": SECOND_ASSOC})
        with owner.begin() as conn:
            conn.execute(text("DELETE FROM associations WHERE id=CAST(:a AS uuid)"),
                         {"a": SECOND_ASSOC})
    return ids["sale_id"]


def test_cross_site_post_is_rejected(admin_session, seeded_db):
    """A forged form post from another site must not reach the handler."""
    sale_id = _sale_in_other_tenant()
    r = admin_session.post(
        "/applications/x/explorer/cell",
        data={"row_id": sale_id, "field": "total_amount", "value": "1"},
        headers={"Origin": "http://evil.example"},
    )
    assert r.status_code == 403, r.status_code
    assert "cross-origin" in r.text


def test_cross_site_post_is_rejected_by_referer_too(admin_session, seeded_db):
    """Browsers send Origin on same-origin posts; a spoofed Referer-only post
    must be blocked as well."""
    sale_id = _sale_in_other_tenant()
    r = admin_session.post(
        "/applications/x/explorer/cell",
        data={"row_id": sale_id, "field": "total_amount", "value": "1"},
        headers={"Referer": "http://evil.example/form"},
    )
    assert r.status_code == 403, r.status_code
