"""Crystal Reports-style export ingestion.

Real-world exports (data/samples/*) put scattered title/date rows above the
actual header row, offset Arabic header cells a column or two from their data,
interleave blank rows, and sometimes repeat whole blocks down one sheet. The
fixtures below are synthetic stand-ins (never the real pharmacy data) that
reproduce that layout so the discovery logic stays locked in.
"""
import datetime
from io import BytesIO

import openpyxl

from app.analytics_service.ingestion import detect_schema_category, read_file

SALES_HEADER_CELLS = {
    1: "ن.الربح",
    3: "إجمالى التكلفة",
    7: "إجمالى س.البيع",
    12: "سعر البيع",
    15: "الرصيد",
    20: "كمية البيع",
    22: "الشركة",
    28: "إسم الصنف",
    36: "ك.الصنف",
}
DATA_COLS = [0, 2, 6, 11, 13, 17, 21, 23, 34]
SALES_DATA = [
    (2.0, 220.0, 225.0, 225.0, 45.0, 10, "شركة الخليج", "PARACETAMOL 500MG 20 TAB", 101),
    (3.5, 310.0, 320.0, 320.0, 30.0, 5, "الوطنية", "VENTOLIN SYRUP 150ML", 102),
    (1.0, 90.0, 95.0, 95.0, 60.0, 8, "شركة الخليج", "سيبروفلوكساسين 500 مجم", 103),
    (4.0, 400.0, 415.0, 415.0, 12.0, 3, "النور", "MEGAMOX 1 GM 14 TAB", 104),
]


def _crystal_report_xlsx(extra_blocks=0, stray_header_row=False) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    row = 1
    ws.cell(row, 8, datetime.datetime(2026, 9, 1).date())
    ws.cell(row, 16, "إلى")
    ws.cell(row, 19, datetime.datetime(2026, 9, 30).date())
    ws.cell(row, 26, "من")
    row += 1
    ws.cell(row, 12, "صيدلية التجربة")
    row += 1
    row += 1
    for col, label in SALES_HEADER_CELLS.items():
        ws.cell(row, col + 1, label)
    row += 1
    for data in SALES_DATA:
        row += 1
        for c, v in zip(DATA_COLS, data):
            ws.cell(row, c + 1, v)
    for _ in range(extra_blocks):
        row += 2
        for col, label in SALES_HEADER_CELLS.items():
            ws.cell(row, col + 1, label)
        row += 1
        for c, v in zip(DATA_COLS, SALES_DATA[0]):
            ws.cell(row, c + 1, v)
    if stray_header_row:
        row += 2
        ws.cell(row, 37, "الكود")
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def _ingest(payload: bytes, name: str = "crystal_report.xlsx"):
    return read_file(payload, name)


def test_report_table_is_discovered_and_mapped():
    ing = _ingest(_crystal_report_xlsx())
    assert ing.errors == []
    df = ing.dataframe
    assert ing.notes == [
        "Sheet1: report-table discovery: header row at row 3, 9 columns, 4 data rows"
    ]
    assert list(df.columns) == [
        "net_profit",
        "total_cost",
        "total_revenue",
        "selling_price",
        "stock_balance",
        "quantity_sold",
        "manufacturer",
        "product_name",
        "product_code",
    ]
    assert len(df) == 4
    assert df.iloc[0]["product_name"] == "PARACETAMOL 500MG 20 TAB"
    assert float(df.iloc[0]["net_profit"]) == 2.0
    assert int(df.iloc[0]["product_code"]) == 101


def test_report_classifies_as_sales():
    df = _ingest(_crystal_report_xlsx()).dataframe
    cats = detect_schema_category(df)
    assert max(cats, key=cats.get) == "sales"


def test_repeated_block_report_is_noted():
    ing = _ingest(_crystal_report_xlsx(extra_blocks=2))
    assert any("repeated-block report" in n for n in ing.notes)


def test_stray_single_header_cell_does_not_leak_into_rows():
    ing = _ingest(_crystal_report_xlsx(stray_header_row=True, extra_blocks=2))
    df = ing.dataframe
    codes = [v for v in df["product_code"] if not _missing(v)]
    assert not any(str(v).strip() == "الكود" for v in codes)
    assert all(v not in ("الكود", "الرصيد") for v in df["product_code"].dropna())


def _missing(v) -> bool:
    import pandas as pd

    return v is None or (isinstance(v, float) and pd.isna(v))