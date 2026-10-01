"""Financial aggregates must ignore Crystal Reports structural rows.

These failures were all silent: the numbers were arithmetically correct sums
over a frame that contained per-invoice header blocks alongside the line items
they described. The three tests below pin the real behaviour against the actual
sample workbooks, and the synthetic ones keep the rules honest for inputs that
are not in the sample set.
"""
from pathlib import Path

import pandas as pd
import pytest

from app.analytics_service import classification, ingestion
from tests.conftest import api_headers
from app.analytics_service.ingestion import read_file

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

INVENTORY = DATA_DIR / "inventory-salem.xls"
PURCHASE = DATA_DIR / "PURCHASE -ABDELHAMID.xls"
STOCK = DATA_DIR / "STOCK.xls"

# Applied per-test rather than as a module-level `pytestmark`: the sample
# workbooks are not in the repository, so a module-level skip silenced the nine
# synthetic tests too — and those are the ones that pin the classification
# guards. CI would have gone green without ever exercising them.
requires_sample_data = pytest.mark.skipif(
    not (INVENTORY.exists() and PURCHASE.exists() and STOCK.exists()),
    reason="sample workbooks are not present",
)


def _sheet(path: Path, sheet: str = "Sheet1") -> pd.DataFrame:
    ing = read_file(path.read_bytes(), path.name)
    return ing.sheets[sheet]


def _identified(df: pd.DataFrame) -> pd.Series:
    """True where a row carries any available product identity column."""
    cols = [c for c in ingestion.IDENTITY_COLUMNS if c in df.columns]
    return df[cols].notna().any(axis=1)


# --- the structural rows are identified ---------------------------------


@requires_sample_data
def test_structural_rows_excluded_from_inventory_money_columns():
    """94 spacer rows carried 67% of the sales column before the filter."""
    df = _sheet(INVENTORY)
    kept, stats = ingestion.transactional_rows(df)

    assert stats["applied"] is True
    assert stats["dropped_rows"] == 94
    assert stats["kept_rows"] == 2485
    assert len(kept) == 2485
    # every kept row is identified by a product code or name
    assert _identified(kept).all()

    # the excluded rows were the source of most of the sales total
    sales = pd.to_numeric(df["إجمالى_البيع"], errors="coerce")
    assert sales.sum() > kept["إجمالى_البيع"].sum() * 2


@requires_sample_data
def test_structural_rows_excluded_from_purchase_file():
    """686 repeated-invoice-header rows leaked text into net_profit."""
    df = _sheet(PURCHASE)
    kept, stats = ingestion.transactional_rows(df)

    assert stats["dropped_rows"] == 684
    assert len(kept) == 4498
    # a few real lines are identified by code only; none is unidentified
    assert _identified(kept).all()
    # supplier names were landing in a numeric profit column
    assert pd.to_numeric(df["net_profit"], errors="coerce").isna().sum() == 684
    assert pd.to_numeric(kept["net_profit"], errors="coerce").notna().sum() == len(kept)


@requires_sample_data
def test_clean_file_keeps_every_row():
    """A file with no structural noise must not lose a single row."""
    df = _sheet(STOCK)
    kept, stats = ingestion.transactional_rows(df)

    assert stats["dropped_rows"] == 0
    assert stats["applied"] is False
    assert len(kept) == len(df)


# --- the filter must not misfire ----------------------------------------


def test_filter_stands_down_without_an_identity_column():
    """A ledger with no product column is not 'structural noise'."""
    df = pd.DataFrame({"date": ["2026-01-01"] * 8, "amount": range(8)})
    kept, stats = ingestion.transactional_rows(df)

    assert len(kept) == 8
    assert stats["applied"] is False
    assert "no product/item column" in stats["reason"]


def test_filter_stands_down_when_most_rows_lack_money_values():
    """Dropping the majority of a file would destroy it, not clean it."""
    rows = [{"product_name": f"P{i}", "cost": i} for i in range(3)]
    rows += [{"product_name": None, "cost": None} for _ in range(9)]
    df = pd.DataFrame(rows)
    kept, stats = ingestion.transactional_rows(df)

    assert len(kept) == 12
    assert stats["applied"] is False
    assert "without product identity" in stats["reason"]


def test_rows_without_a_money_value_are_dropped_but_keep_their_identity():
    """A named row with no money value is still not a transaction."""
    df = pd.DataFrame({
        "product_name": ["A", "B", None, None],
        "cost": [1.0, 2.0, None, None],
    })
    kept, stats = ingestion.transactional_rows(df)

    assert list(kept["product_name"]) == ["A", "B"]
    assert stats["dropped_rows"] == 2


# --- a ratio column is not money ----------------------------------------


@requires_sample_data
def test_profit_percentage_column_is_not_selected_as_revenue():
    """This produced a reported -977% gross margin on a healthy file."""
    df = _sheet(INVENTORY)
    fields = classification.classify_dataframe(df)["field_scores"]
    amount = fields["total_amount"]["suggested_mapping"]

    assert amount == "إجمالى_البيع"
    assert not classification._is_ratio_header(amount)


@pytest.mark.parametrize("header", [
    "نسبة_الربح", "نسبة الربح", "discount_pct", "percentage", "tax rate",
    "margin_ratio", "٪",
])
def test_ratio_headers_are_recognised(header):
    assert classification._is_ratio_header(header)


@pytest.mark.parametrize("header", [
    "total_revenue", "إجمالى البيع", "selling_price", "cost", "product_name",
])
def test_money_headers_are_not_mistaken_for_ratios(header):
    assert not classification._is_ratio_header(header)


def test_ratio_column_never_backs_a_money_field():
    """Content inference must not reach a rate column, whatever it scores."""
    df = pd.DataFrame({
        "sale_timestamp": pd.date_range("2026-01-01", periods=6),
        "transaction_ref": ["A", "B", "C", "D", "E", "F"],
        "نسبة_الربح": [1.5] * 6,
        "amount_placeholder": [10, 20, 30, 40, 50, 60],
    })
    fields = classification.classify_dataframe(df)["field_scores"]

    for field in classification.MONEY_FIELDS:
        mapped = fields.get(field, {}).get("suggested_mapping")
        assert not (mapped and classification._is_ratio_header(mapped)), (
            f"{field} was mapped onto ratio column {mapped!r}"
        )


# --- end to end: the fix has to reach the report ------------------------


def _analyze(client, path: Path, name: str):
    r = client.post(
        "/api/v1/analyze",
        headers=api_headers(),
        data={"application_name": name},
        files=[("files", (path.name, path.open("rb"),
                          "application/vnd.ms-excel"))],
    )
    assert r.status_code == 200, r.text
    return r.json()


@requires_sample_data
def test_report_does_not_count_structural_rows_as_money(client):
    """End-to-end guard on the file that produced the -977% margin."""
    payload = _analyze(client, INVENTORY, "structural-rows")
    section = payload["report"]["files"][0]

    assert section["row_filter"]["dropped_rows"] == 94

    gross = next(
        i for i in section["insights"] if i["key"] == "gross_margin_pct"
    )
    # a ratio column as revenue produced -977%; a real sales column does not
    assert gross["status"] == "ok"
    assert -200.0 < float(gross["value"]) < 200.0, gross["value"]


@requires_sample_data
def test_client_projection_states_the_excluded_rows(client):
    """A filtered row count must never read as full coverage."""
    payload = _analyze(client, INVENTORY, "structural-projection")
    app_id = payload["report"]["application_id"]
    r = client.get(
        f"/api/v1/applications/{app_id}/analyses/{payload['analysis_id']}/report",
        headers=api_headers(),
        params={"format": "client"},
    )
    assert r.status_code == 200, r.text
    files = r.json()["Files"]

    branch = files["inventory-salem.xls"]
    assert "Rows analysed" in branch
    assert "2,485 of 2,579" in branch["Rows analysed"]
    assert "94 structural row(s)" in branch["Rows excluded"]

    gaps = " ".join(r.json().get("Evidence gaps") or [])
    assert "94 of 2,579 source rows" in gaps


def test_weak_fuzzy_match_cannot_become_revenue():
    """`vat_amount` scores 73 against `total_amount`; that is not revenue."""
    df = pd.DataFrame({
        "product_name": ["A", "B", "C"],
        "vat_amount": [0.23, 0.46, 0.69],
        "cost": [1.0, 2.0, 3.0],
    })
    fields = classification.classify_dataframe(df)["field_scores"]
    assert fields["total_amount"]["suggested_mapping"] is None


def test_strong_header_match_still_resolves_revenue():
    """The stricter bar must not reject a genuine revenue column."""
    df = pd.DataFrame({
        "product_name": ["A", "B"],
        "total_revenue": [10.0, 20.0],
        "total_cost": [4.0, 8.0],
    })
    fields = classification.classify_dataframe(df)["field_scores"]
    assert fields["total_amount"]["suggested_mapping"] == "total_revenue"


@requires_sample_data
def test_purchase_workbook_no_longer_reports_an_impossible_margin(client):
    """vat_amount totalled 1.03 and was read as revenue: -44,376,901% margin."""
    payload = _analyze(client, PURCHASE, "purchase-no-impossible-margin")
    section = payload["report"]["files"][0]

    assert section["domain_analytics"].get("amount_field") in (None, "selling_price")
    gross = next(
        (i for i in section["insights"] if i["key"] == "gross_margin_pct"), None
    )
    if gross is not None and gross["status"] == "ok":
        assert -1000.0 < float(gross["value"]) < 1000.0, gross["value"]
