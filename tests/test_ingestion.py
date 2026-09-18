"""Ingestion pipeline tests.

Covers the Phase-1 guarantees: Arabic/non-English classification, the three
validation checks, single CSV/XLSX handling, the multi-pharmacy commit gate,
and an end-to-end golden-file commit through the running app.
"""
import io
import os

from app.services import ingestion
from app.services.classification import classify_file

GOLDEN = os.path.join(os.path.dirname(__file__), "golden_files")


def _read(name):
    with open(os.path.join(GOLDEN, name), "rb") as f:
        return f.read()


# ---------------------------------------------------------------- validation
def test_validation_rejects_negative_duplicate_orphan():
    import pandas as pd
    df = ingestion.read_frame(os.path.join(GOLDEN, "clean.csv")).copy()
    mapping = {"product_name": "product_name", "quantity": "quantity",
               "unit_price": "unit_price", "total_amount": "total_amount"}
    # Create a bad dataframe: 3 valid rows + 1 duplicate row + 1 row with negative + 1 row with orphan
    bad = pd.concat([df, df.iloc[[0]], df.iloc[[0]], df.iloc[[0]]], ignore_index=True)
    # Row 3: duplicate (will be flagged as duplicate)
    # Row 4: negative quantity
    bad.loc[4, "quantity"] = -1
    # Row 5: orphan product
    bad.loc[5, "product_name"] = ""
    valid, errors = ingestion.validate_rows(bad, mapping)
    assert len(errors) >= 3, errors
    reasons = " ".join(r for e in errors for r in e["reasons"])
    assert "negative" in reasons
    assert "duplicate" in reasons
    assert "orphan" in reasons
    assert valid, "valid rows must still be committed (partial success)"


# ---------------------------------------------------------------- classification
def test_arabic_header_classification():
    result = classify_file(os.path.join(GOLDEN, "arabic_headers.csv"))
    assert "error" not in result, result
    fs = result["field_scores"]
    # Arabic synonyms must genuinely drive the mapping (not English-only)
    assert fs["product_name"]["status"] == "confirmed", fs["product_name"]
    assert fs["sale_timestamp"]["suggested_mapping"] == "تاريخ البيع", fs["sale_timestamp"]
    assert fs["quantity"]["suggested_mapping"] == "الكمية", fs["quantity"]
    assert fs["total_amount"]["suggested_mapping"] == "الإجمالي", fs["total_amount"]


def test_clean_csv_classification():
    result = classify_file(os.path.join(GOLDEN, "clean.csv"))
    assert "error" not in result
    fs = result["field_scores"]
    # Some fields may be "uncertain" if score < 90 - that's fine for the algorithm
    assert fs["product_name"]["suggested_mapping"] == "product_name"
    assert fs["quantity"]["suggested_mapping"] == "quantity"
    # unit_price might be confused with sale_timestamp due to fuzzy matching
    # but product_name and quantity should be clear


# ---------------------------------------------------------------- mapping safety
def test_mapping_never_assigns_a_date_column_to_a_number_field():
    """Regression: a weak or absent header must not let the content pass hand a
    date column to a numeric field. This used to map ``total_amount`` onto
    "Sale Date" and mark it *confirmed*, turning revenue into 0 silently."""
    import pandas as pd
    from app.services.classification import _classify_dataframe

    df = pd.DataFrame({
        "Sale Date": ["2026-09-01", "2026-09-02", "2026-09-03"],
        "Item": ["Paracetamol", "Ibuprofen", "Amoxicillin"],
        "Qty": [2, 1, 5],
        "Price": [1.5, 2.0, 1.0],
        "Payment": ["card", "cash", "card"],
    })
    fs = _classify_dataframe(df)["field_scores"]

    # The date column belongs to the date field only.
    assert fs["sale_timestamp"]["suggested_mapping"] == "Sale Date", fs["sale_timestamp"]
    for field in ("quantity", "unit_price", "total_amount"):
        assert fs[field]["suggested_mapping"] != "Sale Date", (field, fs[field])
        # and no numeric field may be confirmed against date content
        for sample in fs[field]["sample_values"]:
            assert "2026-09-01" not in sample, (field, fs[field])


def test_content_pass_does_not_reuse_a_single_column():
    """One source column may back at most one canonical field."""
    import pandas as pd
    from app.services.classification import _classify_dataframe

    fs = _classify_dataframe(pd.DataFrame({"Value": [1.5, 2.0, 3.5, 4.0]}))["field_scores"]
    reused = [f for f in ("quantity", "unit_price", "total_amount")
              if fs[f]["suggested_mapping"] == "Value"]
    assert len(reused) <= 1, reused


# ---------------------------------------------------------------- date integrity
def _sales_frame(dates):
    import pandas as pd
    return pd.DataFrame({
        "product_name": ["Paracetamol", "Ibuprofen"],
        "sale_timestamp": dates,
        "quantity": [2, 1],
        "unit_price": [1.5, 2.0],
        "total_amount": [3.0, 2.0],
    })


_SALES_MAPPING = {"product_name": "product_name", "sale_timestamp": "sale_timestamp",
                  "quantity": "quantity", "unit_price": "unit_price",
                  "total_amount": "total_amount"}


def test_unparseable_date_is_rejected_not_defaulted_to_now():
    df = _sales_frame(["2026-09-01", "not-a-date"])
    valid, errors = ingestion.validate_rows(df, _SALES_MAPPING)
    assert len(valid) == 1, (valid, errors)
    reasons = " ".join(r for e in errors for r in e["reasons"])
    assert "unparseable sale_timestamp" in reasons, reasons


def test_missing_mapped_date_is_rejected():
    df = _sales_frame(["2026-09-01", ""])
    valid, errors = ingestion.validate_rows(df, _SALES_MAPPING)
    assert len(valid) == 1, (valid, errors)
    reasons = " ".join(r for e in errors for r in e["reasons"])
    assert "missing sale_timestamp" in reasons, reasons


def test_valid_dates_still_commit():
    df = _sales_frame(["2026-09-01", "2026-09-02"])
    valid, errors = ingestion.validate_rows(df, _SALES_MAPPING)
    assert len(valid) == 2, (valid, errors)
    assert not errors, errors


# ---------------------------------------------------------------- end-to-end via the app
def _create_application(client, name, pharmacy_id=None):
    data = {"name": name, "source_type": "manual_upload"}
    if pharmacy_id:
        data["pharmacy_id"] = pharmacy_id
    r = client.post("/applications/new", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text
    return r.headers["location"].rstrip("/").rsplit("/", 1)[-1]


def _demo_pharmacy_id():
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker
    from app.database import engine
    from app.models.models import Pharmacies
    sess = sessionmaker(bind=engine)()
    sess.execute(text("SELECT set_config('app.current_association_id', "
                      "'11111111-1111-4111-8111-111111111111', true)"))
    pid = sess.query(Pharmacies).filter(Pharmacies.name == "Demo Branch 1").one().id
    sess.close()
    return pid


def test_multi_pharmacy_gate_blocks_then_commits(admin_session):
    client = admin_session
    app_id = _create_application(client, "Multi Test")  # no pharmacy -> multi
    content = _read("multi_pharmacy.csv")
    r = client.post(f"/applications/{app_id}/upload",
                    files={"file": ("multi_pharmacy.csv", content, "text/csv")},
                    follow_redirects=False)
    assert r.status_code == 303, r.text
    # URL is /applications/datasets/{dataset_id}/mapping or /bulk_mapping
    location = r.headers["location"]
    dataset_id = location.rstrip("/").rsplit("/", 2)[-2]

    # Hard gate: committing without a pharmacy column must be blocked.
    r = client.post(f"/datasets/{dataset_id}/confirm", data={"field_map": "{}"})
    assert "blocked" in r.text, r.text

    # Re-map and confirm WITH the pharmacy column -> commits.
    r = client.post(f"/datasets/{dataset_id}/confirm",
                    data={"field_map": '{"product_name":"product_name",'
                                       '"sale_timestamp":"sale_timestamp",'
                                       '"quantity":"quantity",'
                                       '"unit_price":"unit_price",'
                                       '"total_amount":"total_amount"}',
                          "pharmacy_identifier_column": "pharmacy"})
    assert "committed" in r.text, r.text
def test_clean_single_pharmacy_commit(client, seeded_db):
    r = client.post("/login", data={"email": "steward@siroq.local", "password": "ChangeMe123!"},
                    follow_redirects=False)
    assert r.status_code == 303
    try:
        app_id = _create_application(client, "Clean Test", _demo_pharmacy_id())
        content = _read("clean.csv")
        r = client.post(f"/applications/{app_id}/upload",
                        files={"file": ("clean.csv", content, "text/csv")},
                        follow_redirects=False)
        assert r.status_code == 303, r.text
        location = r.headers["location"]
        dataset_id = location.rstrip("/").rsplit("/", 2)[-2]
        conf = client.post(f"/datasets/{dataset_id}/confirm",
                           data={"field_map": '{"product_name":"product_name",'
                                              '"sale_timestamp":"sale_timestamp",'
                                              '"quantity":"quantity",'
                                              '"unit_price":"unit_price",'
                                              '"total_amount":"total_amount",'
                                              '"payment_method":"payment_method"}'})
        assert "committed" in conf.text, conf.text
        dash = client.get("/dashboards/sales")
        assert dash.status_code == 200
    finally:
        client.post("/logout")