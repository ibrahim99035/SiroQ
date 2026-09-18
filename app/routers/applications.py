"""Applications + Data Explorer routes.

Prefix: /applications (wired in app/main.py). The dataset confirm route is also
exposed as a dedicated top-level ``datasets_router`` so the browser can post to
``/datasets/{id}/confirm``.
"""
import os
import uuid

from fastapi import APIRouter, Request, Depends, UploadFile, File, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_session
from app.dependencies import require_role
from app.models.models import (
    Users, Applications, Pharmacies, Associations, Datasets,
    MappingProfiles, Sales, SaleLines, EditAuditLog,
)
from app.services import ingestion, mapping as mapping_service, audit as audit_service
from app.services.classification import classify_file, classify_excel_sheets, classify_zip

router = APIRouter(tags=["applications"])
datasets_router = APIRouter(tags=["datasets"])
templates = Jinja2Templates(directory="app/templates")

INGEST_ROLES = ("association_admin", "pharmacy_manager", "data_steward")
EDIT_ROLES = ("association_admin", "pharmacy_manager", "data_steward")


# ---------------------------------------------------------------- list / new
@router.get("")
def applications_list(request: Request, db: Session = Depends(get_session),
                      user: Users = Depends(require_role(*INGEST_ROLES))):
    apps = db.query(Applications).filter(
        Applications.association_id == user.association_id
    ).order_by(Applications.created_at).all()
    return templates.TemplateResponse(
        request, "applications/list.html", {"request": request, "user": user, "apps": apps}
    )


@router.get("/new")
def application_new(request: Request, db: Session = Depends(get_session),
                    user: Users = Depends(require_role(*INGEST_ROLES))):
    pharmacies = db.query(Pharmacies).filter(
        Pharmacies.association_id == user.association_id
    ).all()
    return templates.TemplateResponse(
        request, "applications/new.html", {"request": request, "user": user,
                                  "pharmacies": pharmacies}
    )


@router.post("/new")
def application_create(request: Request, db: Session = Depends(get_session),
                       user: Users = Depends(require_role(*INGEST_ROLES)),
                       name: str = Form(...),
                       pharmacy_id: str | None = Form(None),
                       source_type: str = Form("manual_upload")):
    app = Applications(
        id=str(uuid.uuid4()),
        association_id=user.association_id,
        pharmacy_id=pharmacy_id or None,
        name=name,
        source_type=source_type,
        status="active",
    )
    db.add(app)
    db.flush()
    app_id = app.id
    db.commit()
    return RedirectResponse(url=f"/applications/{app_id}", status_code=303)


@router.get("/{app_id}")
def application_detail(request: Request, app_id: str,
                       db: Session = Depends(get_session),
                       user: Users = Depends(require_role(*INGEST_ROLES))):
    app = db.query(Applications).get(app_id)
    if not app:
        return HTMLResponse("<h1>Not found</h1>", status_code=404)
    datasets = db.query(Datasets).filter(Datasets.application_id == app.id).order_by(
        Datasets.uploaded_at.desc()).all()
    profile = db.query(MappingProfiles).filter(
        MappingProfiles.application_id == app.id).first()
    return templates.TemplateResponse(
        request, "applications/detail.html", {"request": request, "user": user,
                                      "app": app, "datasets": datasets,
                                      "profile": profile}
    )


# ---------------------------------------------------------------- upload
@router.get("/{app_id}/upload")
def step1_upload(request: Request, app_id: str, db: Session = Depends(get_session),
                 user: Users = Depends(require_role(*INGEST_ROLES))):
    return templates.TemplateResponse(
        request, "uploads/step1_upload.html", {"request": request, "user": user,
                                       "application_id": app_id}
    )


@router.post("/{app_id}/upload")
def step1_upload_post(request: Request, app_id: str,
                      db: Session = Depends(get_session),
                      user: Users = Depends(require_role(*INGEST_ROLES)),
                      file: UploadFile = File(...)):
    app = db.get(Applications, app_id)
    if not app:
        return HTMLResponse("<h1>Not found</h1>", status_code=404)
    filename = file.filename or "upload.csv"
    content = file.file.read()
    
    # Check if it's a zip file
    is_zip = filename.lower().endswith(".zip")
    # Check if it's an Excel file with multiple sheets
    is_excel = filename.lower().endswith((".xlsx", ".xls"))
    
    dataset = Datasets(
        id=str(uuid.uuid4()),
        association_id=app.association_id,
        application_id=app.id,
        bronze_file_path="",  # set after saving below
        original_filename=filename,
        uploaded_by=user.id,
        status="pending_mapping",
    )
    db.add(dataset)
    db.flush()
    dataset_id = dataset.id
    
    bronze_root = str(request.app.state.settings.BRONZE_STORAGE_PATH)
    full_path = ingestion.save_bronze(
        content, bronze_root, app.association_id, dataset_id, filename
    )
    dataset.bronze_file_path = full_path
    db.commit()
    
    # For zip or multi-sheet Excel, redirect to bulk mapping page
    if is_zip or is_excel:
        # Check if Excel has multiple sheets
        if is_excel:
            sheets = ingestion.get_excel_sheets(full_path)
            if len(sheets) > 1:
                return RedirectResponse(
                    url=f"/applications/datasets/{dataset_id}/bulk_mapping", status_code=303
                )
        if is_zip:
            return RedirectResponse(
                url=f"/applications/datasets/{dataset_id}/bulk_mapping", status_code=303
            )
    
    return RedirectResponse(
        url=f"/applications/datasets/{dataset_id}/mapping", status_code=303
    )


# ---------------------------------------------------------------- bulk mapping (zip/multi-sheet)
@router.get("/datasets/{dataset_id}/bulk_mapping")
def bulk_mapping_page(request: Request, dataset_id: str,
                      db: Session = Depends(get_session),
                      user: Users = Depends(require_role(*INGEST_ROLES))):
    dataset = db.query(Datasets).get(dataset_id)
    if not dataset:
        return HTMLResponse("<h1>Not found</h1>", status_code=404)
    
    file_path = dataset.bronze_file_path
    classification_results = {}
    
    if file_path.lower().endswith(".zip"):
        classification_results = classify_zip(file_path)
    elif file_path.lower().endswith((".xlsx", ".xls")):
        classification_results = classify_excel_sheets(file_path)
    else:
        classification_results = {dataset.original_filename: classify_file(file_path)}
    
    app = db.query(Applications).get(dataset.application_id)
    
    return templates.TemplateResponse(
        request, "uploads/bulk_mapping.html",
        {"request": request, "user": user, "dataset": dataset,
         "application_id": dataset.application_id, "classification_results": classification_results,
         "app": app},
    )


@router.post("/datasets/{dataset_id}/bulk_mapping")
def bulk_mapping_confirm(request: Request, dataset_id: str,
                         db: Session = Depends(get_session),
                         user: Users = Depends(require_role(*INGEST_ROLES)),
                         field_maps: str = Form("{}"),
                         pharmacy_identifier_columns: str = Form("{}")):
    import json
    field_maps = json.loads(field_maps or "{}")
    pharmacy_identifier_columns = json.loads(pharmacy_identifier_columns or "{}")
    
    dataset = db.get(Datasets, dataset_id)
    if not dataset:
        return HTMLResponse("<h1>Not found</h1>", status_code=404)
    app = db.get(Applications, dataset.application_id)
    
    file_path = dataset.bronze_file_path
    total_committed = 0
    all_errors = []
    
    if file_path.lower().endswith(".zip"):
        extracted = ingestion.extract_zip(open(file_path, "rb").read())
        for filename, content in extracted:
            if filename.lower().endswith((".csv", ".xlsx", ".xls")):
                # Save extracted file temporarily
                import tempfile
                with tempfile.NamedTemporaryFile(delete=False, suffix=f".{filename.split('.')[-1]}") as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name
                
                try:
                    df = ingestion.read_frame(tmp_path)
                    mapping = field_maps.get(filename, {})
                    pharm_col = pharmacy_identifier_columns.get(filename)
                    committed, errors = _process_dataframe(db, app, dataset.id, df, mapping, pharm_col, user.id)
                    total_committed += committed
                    all_errors.extend(errors)
                finally:
                    os.unlink(tmp_path)
                    
    elif file_path.lower().endswith((".xlsx", ".xls")):
        sheets = ingestion.get_excel_sheets(file_path)
        for sheet_name in sheets:
            df = ingestion.read_excel_sheet(file_path, sheet_name)
            mapping = field_maps.get(sheet_name, {})
            pharm_col = pharmacy_identifier_columns.get(sheet_name)
            committed, errors = _process_dataframe(db, app, dataset.id, df, mapping, pharm_col, user.id)
            total_committed += committed
            all_errors.extend(errors)
    else:
        df = ingestion.read_frame(file_path)
        mapping = field_maps.get(dataset.original_filename, {})
        pharm_col = pharmacy_identifier_columns.get(dataset.original_filename)
        committed, errors = _process_dataframe(db, app, dataset.id, df, mapping, pharm_col, user.id)
        total_committed += committed
        all_errors.extend(errors)
    
    mapping_service.save_mapping_profile(db, app.id, app.association_id, field_maps, user.id)
    dataset.status = "committed"
    dataset.row_count = total_committed
    db.commit()
    
    return templates.TemplateResponse(
        request, "uploads/step3_result.html",
        {"request": request, "user": user, "status": "committed",
         "message": f"Committed {total_committed} row(s) across {len(classification_results)} file(s)/sheet(s).", 
         "application_id": app.id, "errors": all_errors},
    )


def _process_dataframe(db, app, dataset_id, df, field_map, pharmacy_identifier_column, user_id):
    """Process a single dataframe with the given mapping."""
    if app.pharmacy_id is not None:
        committed, errors, _ = ingestion.commit_dataset(
            db, app.association_id, app.id, dataset_id, df, field_map,
            app.pharmacy_id, user_id)
        return committed, errors
    else:
        col = pharmacy_identifier_column or app.pharmacy_identifier_column
        if not col:
            return 0, [{"row": "N/A", "reasons": ["No pharmacy identifier column specified"]}]
        
        app.pharmacy_identifier_column = col
        total_committed = 0
        all_errors = []
        for i, row in df.iterrows():
            ph_id = mapping_service.resolve_pharmacy(db, app.association_id, row.get(col))
            c, e, _ = ingestion.commit_dataset(
                db, app.association_id, app.id, dataset_id, df.iloc[[i]], field_map,
                ph_id, user_id)
            total_committed += c
            all_errors.extend(e)
        return total_committed, all_errors


# ---------------------------------------------------------------- mapping
@router.get("/datasets/{dataset_id}/mapping")
def mapping_page(request: Request, dataset_id: str,
                 db: Session = Depends(get_session),
                 user: Users = Depends(require_role(*INGEST_ROLES))):
    dataset = db.query(Datasets).get(dataset_id)
    if not dataset:
        return HTMLResponse("<h1>Not found</h1>", status_code=404)
    classification = classify_file(dataset.bronze_file_path)
    if "error" in classification:
        return HTMLResponse(f"<h1>Error</h1><p>{classification['error']}</p>", status_code=400)
    df = ingestion.read_frame(dataset.bronze_file_path)
    app = db.query(Applications).get(dataset.application_id)

    pharm_hint = None
    needs_pharmacy_identifier = app.pharmacy_id is None
    if needs_pharmacy_identifier:
        pharm_hint = mapping_service.pick_pharmacy_identifier_column(df)

    return templates.TemplateResponse(
        request, "uploads/step2_mapping.html",
        {"request": request, "user": user, "dataset": dataset,
         "application_id": dataset.application_id, "classification": classification,
         "needs_pharmacy_identifier": needs_pharmacy_identifier,
         "pharmacies": db.query(Pharmacies).filter(
             Pharmacies.association_id == app.association_id).all(),
         "pharmacy_columns": df.columns.tolist(),
         "pharm_hint": pharm_hint},
    )


# ---- confirm (datasets router, mounted at top-level so the browser can POST
# ---- to /datasets/{id}/confirm as the spec's route table requires)
@datasets_router.post("/datasets/{dataset_id}/confirm", response_class=HTMLResponse)
def dataset_confirm(dataset_id: str, request: Request,
                    db: Session = Depends(get_session),
                    user: Users = Depends(require_role(*INGEST_ROLES)),
                    field_map: str = Form("{}"),
                    pharmacy_identifier_column: str | None = Form(None)):
    import json
    field_map = json.loads(field_map or "{}")
    dataset = db.get(Datasets, dataset_id)
    if not dataset:
        return HTMLResponse("<h1>Not found</h1>", status_code=404)
    app = db.get(Applications, dataset.application_id)
    app_id = app.id
    assoc = db.get(Associations, app.association_id)
    currency = assoc.default_currency if assoc and assoc.default_currency else "EGP"

# ---- HARD GATE: a multi-pharmacy dataset must name its pharmacy column
    if app.pharmacy_id is None and not (pharmacy_identifier_column or app.pharmacy_identifier_column):
        dataset.status = "needs_pharmacy_identifier"
        db.commit()
        return templates.TemplateResponse(
            request, "uploads/step3_result.html",
            {"request": request, "user": user, "status": "blocked",
             "message": "Multi-pharmacy file: choose the column that identifies "
                          "the pharmacy before committing.",
             "application_id": app_id},
        )

    df = ingestion.read_frame(dataset.bronze_file_path)
    committed = 0
    errors = []
    if app.pharmacy_id is not None:
        committed, errors, _ = ingestion.commit_dataset(
            db, app.association_id, app.id, dataset.id, df, field_map,
            app.pharmacy_id, user.id, currency=currency)
    else:
        col = pharmacy_identifier_column or app.pharmacy_identifier_column
        if col:
            app.pharmacy_identifier_column = col
            for i, row in df.iterrows():
                ph_id = mapping_service.resolve_pharmacy(db, app.association_id, row.get(col))
                c, e, _ = ingestion.commit_dataset(
                    db, app.association_id, app.id, dataset.id, df.iloc[[i]], field_map,
                    ph_id, user.id, currency=currency)
                committed += c
                for err in e:
                    errors.append(err)

    mapping_service.save_mapping_profile(db, app.id, app.association_id, field_map, user.id)
    dataset.status = "committed"
    dataset.row_count = committed
    db.commit()

    return templates.TemplateResponse(
        request, "uploads/step3_result.html",
        {"request": request, "user": user, "status": "committed",
         "message": f"Committed {committed} row(s).", "application_id": app_id,
         "errors": errors},
    )
# ---------------------------------------------------------------- data explorer
@router.get("/{app_id}/explorer")
def explorer_page(request: Request, app_id: str, db: Session = Depends(get_session),
                  user: Users = Depends(require_role(*EDIT_ROLES))):
    return templates.TemplateResponse(
        request, "data_explorer/grid.html", {"request": request, "user": user, "application_id": app_id}
    )


@router.get("/{app_id}/explorer/rows")
def explorer_rows(request: Request, app_id: str, entity: str = "sales",
                  db: Session = Depends(get_session),
                  user: Users = Depends(require_role(*EDIT_ROLES))):
    rows = db.query(Sales).filter(Sales.application_id == app_id).order_by(
        Sales.sale_timestamp.desc()).limit(200).all()
    return templates.TemplateResponse(
        request, "data_explorer/_grid_rows.html", {"request": request, "user": user,
                                           "entity": entity, "rows": rows}
    )


@router.post("/{app_id}/explorer/cell")
def explorer_cell(app_id: str, request: Request,
                  db: Session = Depends(get_session),
                  user: Users = Depends(require_role(*EDIT_ROLES)),
                  row_id: str = Form(...), field: str = Form(...),
                  value: str = Form(...), entity: str = Form("sales")):
    row = db.query(Sales).get(row_id)
    if not row:
        return HTMLResponse("missing", status_code=404)
    old_value = getattr(row, field)
    # Audit FIRST (before applying the change) in the same transaction
    audit_service.log_edit(db, user.association_id, user.id, entity, row.id, field,
                           old_value, value, reason="cell edit")
    setattr(row, field, _cast(field, value))
    db.commit()
    return HTMLResponse("ok")


def _cast(field, value):
    if field in ("total_amount", "unit_price", "quantity"):
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    return value


@router.post("/{app_id}/explorer/revert/{edit_id}")
def explorer_revert(app_id: str, edit_id: str, request: Request,
                    db: Session = Depends(get_session),
                    user: Users = Depends(require_role(*EDIT_ROLES))):
    edit = db.query(EditAuditLog).get(edit_id)
    if not edit:
        return HTMLResponse("missing", status_code=404)
    row = db.query(Sales).get(edit.entity_id)
    if not row:
        return HTMLResponse("missing", status_code=404)
    current = getattr(row, edit.field)
    # Write a NEW audit row restoring the value; the original trail is kept.
    audit_service.log_edit(db, user.association_id, user.id, edit.entity_type,
                           row.id, edit.field, current, edit.old_value, reason="revert")
    setattr(row, edit.field, _cast(edit.field, edit.old_value))
    db.commit()
    return HTMLResponse("ok")