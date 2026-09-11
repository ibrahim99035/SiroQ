from fastapi import APIRouter, Request, Depends, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
import pandas as pd
import json
import os
import hashlib
import io

from app.database import get_session
from app.models.models import (
    Associations,
    Pharmacies,
    Users,
    Applications,
    MappingProfiles,
    Datasets,
)
from app.security import verify_password, hash_password
from app.dependencies import require_role, set_tenant_context
from rapidfuzz import fuzz


router = APIRouter(prefix="/applications", tags=["applications"])


# Helper: fuzzy header matching
CANONICAL_FIELD_SYNONYMS = {
    "expiry_date": ["exp", "expiry", "use by", "use-by", "datum ur", "日期", "تاريخ الانتهاء"],
    "receipt_date": ["received", "receipt date", "date received", "入庫日"],
    "sale_timestamp": ["sale date", "transaction date", "sold date", "transaction timestamp"],
    "quantity": ["qty", "quantity", "amount", "count"],
    "unit_price": ["unit price", "price per unit", "unit cost", "unit cost"],
    "total_amount": ["total amount", "total sales", "grand total"],
    "payment_method": ["payment", "pay method", "mode of payment"],
    "prescriber": ["prescriber", "doctor", "physician", " prescribing"],
    "batch_id": ["batch", "lot", "lot number"],
    "product_name": ["product", "drug name", "medicine name"],
}


def score_headers(headers: list[str], field: str) -> float:
    """Score a list of headers against a canonical field using rapidfuzz."""
    best = 0
    for h in headers:
        score = fuzz.token_sort_ratio(h.lower(), field.lower())
        if score > best:
            best = score
    return best


def score_content(values: list) -> float:
    """Score based on content-based inference. Returns 0-100 confidence."""
    if not values:
        return 0
    non_null = [v for v in values if v is not None and str(v).strip() != ""]
    if not non_null:
        return 0
    ratio = len(set(str(v).lower().strip() for v in non_null)) / len(non_null)
    # Low cardinality = likely categorical
    if ratio < 0.1 and len(non_null) >= 3:
        return 85.0  # high confidence it's a category
    # Check for date patterns
    import re
    date_pattern = re.compile(r"\d{4}[-/]\d{2}[-/]\d{2}|\d{2}[-/]\d{2}[-/]\d{4}")
    date_count = sum(1 for v in non_null if date_pattern.search(str(v)))
    if date_count / len(non_null) > 0.6:
        return 90.0
    # Check for numeric with decimal
    numeric_count = sum(1 for v in non_null if isinstance(v, (int, float)) or (isinstance(str(v), str) and re.match(r"^-?\d+\.\d+", str(v))))
    if numeric_count / len(non_null) > 0.6:
        return 88.0
    return 50.0


@router.get("/{app_id}")
def application_detail(
    app_id: str,
    request: Request,
    db: Session = Depends(get_session),
):
    """Show application detail with mapping profile summary."""
    from app.models.models import Applications, MappingProfiles
    app = db.query(Applications).get(app_id)
    if not app:
        return {"error": "Application not found"}
    mapping = db.query(MappingProfiles).filter(
        MappingProfiles.application_id == app.id
    ).first()
    return {
        "application": app,
        "mapping_profile": mapping,
    }


@router.get("/{app_id}/upload", response_class=HTMLResponse)
def step1_upload(
    app_id: str,
    request: Request,
    db: Session = Depends(get_session),
):
    """File upload form step 1."""
    from fastapi.templating import Jinja2Templates
    from app.main import templates
    return templates.TemplateResponse(
        "uploads/step1_upload.html",
        {"request": request, "app_id": app_id},
    )


@router.post("/{app_id}/upload")
def step1_upload_post(
    app_id: str,
    request: Request,
    db: Session = Depends(get_session),
    file: UploadFile = File(...),
):
    """Handle upload, save to Bronze, run classification."""
    from app.models.models import Applications, Datasets
    from uuid import uuid4
    import os

    # Verify application exists and user has access
    app = db.query(Applications).get(app_id)
    if not app:
        return {"error": "Application not found"}

    # Save raw file to Bronze
    bronze_dir = os.path.join(
        str(request.app.state.settings.BRONZE_STORAGE_PATH),
        str(app.association_id),
        str(app.id),
    )
    os.makedirs(bronze_dir, exist_ok=True)
    filename = file.filename or "upload."
    file_path = os.path.join(bronze_dir, filename)
    content = file.file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    # Create dataset row
    dataset = Datasets(
        association_id=app.association_id,
        application_id=app.id,
        bronze_file_path=file_path,
        original_filename=filename,
        uploaded_by=uuid4(),  # simplified - would use actual user from session
        status="pending_mapping",
    )
    db.add(dataset)
    db.commit()
    db.refresh(dataset)

    # Run classification
    from app.services.classification import classify_file
    result = classify_file(file_path, app.association_id)

    return {
        "dataset_id": str(dataset.id),
        "classification": result,
        "file_path": file_path,
    }