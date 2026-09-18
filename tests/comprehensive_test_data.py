#!/usr/bin/env python3
"""Generate comprehensive test CSV and Excel files for SiroQ testing."""
import pandas as pd
import os
from datetime import datetime, timedelta

OUTPUT_DIR = "/media/ibrahim/New Volume2/Projects/SiroQ/tests/generated_test_data"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 1. Basic clean sales data (single pharmacy)
def generate_clean_csv():
    data = [
        {"product_name": "Paracetamol 500mg", "sale_timestamp": "2026-09-01 10:30:00", "quantity": 2, "unit_price": 1.50, "total_amount": 3.00, "payment_method": "card"},
        {"product_name": "Ibuprofen 400mg", "sale_timestamp": "2026-09-01 11:15:00", "quantity": 1, "unit_price": 2.00, "total_amount": 2.00, "payment_method": "cash"},
        {"product_name": "Amoxicillin 250mg", "sale_timestamp": "2026-09-02 09:45:00", "quantity": 5, "unit_price": 1.00, "total_amount": 5.00, "payment_method": "card"},
        {"product_name": "Vitamin C 1000mg", "sale_timestamp": "2026-09-02 14:20:00", "quantity": 3, "unit_price": 3.50, "total_amount": 10.50, "payment_method": "cash"},
        {"product_name": "Omeprazole 20mg", "sale_timestamp": "2026-09-03 16:00:00", "quantity": 2, "unit_price": 4.25, "total_amount": 8.50, "payment_method": "card"},
    ]
    df = pd.DataFrame(data)
    path = os.path.join(OUTPUT_DIR, "clean_sales.csv")
    df.to_csv(path, index=False)
    print(f"Generated: {path} ({len(df)} rows)")
    return path

# 2. Multi-pharmacy data with pharmacy column
def generate_multi_pharmacy_csv():
    data = [
        {"pharmacy": "Branch A", "product_name": "Paracetamol 500mg", "sale_timestamp": "2026-09-01 10:30:00", "quantity": 3, "unit_price": 1.50, "total_amount": 4.50, "payment_method": "card"},
        {"pharmacy": "Branch B", "product_name": "Ibuprofen 400mg", "sale_timestamp": "2026-09-01 11:15:00", "quantity": 1, "unit_price": 2.00, "total_amount": 2.00, "payment_method": "cash"},
        {"pharmacy": "Branch A", "product_name": "Amoxicillin 250mg", "sale_timestamp": "2026-09-02 09:45:00", "quantity": 5, "unit_price": 1.00, "total_amount": 5.00, "payment_method": "card"},
        {"pharmacy": "Branch C", "product_name": "Vitamin C 1000mg", "sale_timestamp": "2026-09-02 14:20:00", "quantity": 2, "unit_price": 3.50, "total_amount": 7.00, "payment_method": "card"},
        {"pharmacy": "Branch B", "product_name": "Omeprazole 20mg", "sale_timestamp": "2026-09-03 16:00:00", "quantity": 4, "unit_price": 4.25, "total_amount": 17.00, "payment_method": "cash"},
    ]
    df = pd.DataFrame(data)
    path = os.path.join(OUTPUT_DIR, "multi_pharmacy_sales.csv")
    df.to_csv(path, index=False)
    print(f"Generated: {path} ({len(df)} rows)")
    return path

# 3. Data with validation issues (negative, duplicates, orphan)
def generate_validation_test_csv():
    data = [
        # Valid rows
        {"product_name": "Paracetamol", "sale_timestamp": "2026-09-01", "quantity": 2, "unit_price": 1.50, "total_amount": 3.00, "payment_method": "card"},
        {"product_name": "Ibuprofen", "sale_timestamp": "2026-09-01", "quantity": 1, "unit_price": 2.00, "total_amount": 2.00, "payment_method": "cash"},
        # Negative quantity (should be rejected)
        {"product_name": "Amoxicillin", "sale_timestamp": "2026-09-02", "quantity": -5, "unit_price": 1.00, "total_amount": -5.00, "payment_method": "card"},
        # Negative unit_price (should be rejected)
        {"product_name": "Vitamin D", "sale_timestamp": "2026-09-02", "quantity": 3, "unit_price": -2.00, "total_amount": -6.00, "payment_method": "cash"},
        # Negative total_amount (should be rejected)
        {"product_name": "Aspirin", "sale_timestamp": "2026-09-03", "quantity": 10, "unit_price": 0.50, "total_amount": -5.00, "payment_method": "card"},
        # Exact duplicate of row 0 (should be rejected)
        {"product_name": "Paracetamol", "sale_timestamp": "2026-09-01", "quantity": 2, "unit_price": 1.50, "total_amount": 3.00, "payment_method": "card"},
        # Orphan product (empty product_name)
        {"product_name": "", "sale_timestamp": "2026-09-03", "quantity": 1, "unit_price": 5.00, "total_amount": 5.00, "payment_method": "cash"},
        # Orphan product (NaN)
        {"product_name": None, "sale_timestamp": "2026-09-03", "quantity": 2, "unit_price": 3.00, "total_amount": 6.00, "payment_method": "card"},
    ]
    df = pd.DataFrame(data)
    path = os.path.join(OUTPUT_DIR, "validation_test.csv")
    df.to_csv(path, index=False)
    print(f"Generated: {path} ({len(df)} rows)")
    return path

# 4. Arabic headers (tests classification)
def generate_arabic_headers_csv():
    data = [
        {"اسم_المنتج": "باراسيتامول", "تاريخ_البيع": "2026-09-01", "الكمية": 2, "سعر_الوحدة": 1.50, "المبلغ_الكلي": 3.00, "طريقة_الدفع": "نقدي"},
        {"اسم_المنتج": "إيبوبروفين", "تاريخ_البيع": "2026-09-02", "الكمية": 1, "سعر_الوحدة": 2.00, "المبلغ_الكلي": 2.00, "طريقة_الدفع": "بطاقة"},
    ]
    df = pd.DataFrame(data)
    path = os.path.join(OUTPUT_DIR, "arabic_headers.csv")
    df.to_csv(path, index=False)
    print(f"Generated: {path} ({len(df)} rows)")
    return path

# 5. Large dataset (100 rows) for performance testing
def generate_large_csv():
    products = ["Paracetamol", "Ibuprofen", "Amoxicillin", "Vitamin C", "Omeprazole", "Aspirin", "Cetirizine", "Loratadine", "Metformin", "Atorvastatin"]
    payment_methods = ["card", "cash", "insurance"]
    base_date = datetime(2026, 9, 1)
    
    data = []
    for i in range(100):
        product = products[i % len(products)]
        qty = (i % 10) + 1
        price = round(1.0 + (i % 5) * 0.75, 2)
        data.append({
            "product_name": f"{product} {(i % 5) + 1}mg",
            "sale_timestamp": (base_date + timedelta(days=i % 30)).strftime("%Y-%m-%d %H:%M:%S"),
            "quantity": qty,
            "unit_price": price,
            "total_amount": round(qty * price, 2),
            "payment_method": payment_methods[i % 3],
        })
    df = pd.DataFrame(data)
    path = os.path.join(OUTPUT_DIR, "large_dataset_100rows.csv")
    df.to_csv(path, index=False)
    print(f"Generated: {path} ({len(df)} rows)")
    return path

# 6. Excel with multiple sheets
def generate_multi_sheet_excel():
    path = os.path.join(OUTPUT_DIR, "multi_sheet_sales.xlsx")
    
    # Sheet 1: Branch A
    df1 = pd.DataFrame([
        {"product_name": "Paracetamol", "sale_timestamp": "2026-09-01", "quantity": 2, "unit_price": 1.50, "total_amount": 3.00},
        {"product_name": "Ibuprofen", "sale_timestamp": "2026-09-01", "quantity": 1, "unit_price": 2.00, "total_amount": 2.00},
    ])
    
    # Sheet 2: Branch B
    df2 = pd.DataFrame([
        {"product_name": "Amoxicillin", "sale_timestamp": "2026-09-02", "quantity": 5, "unit_price": 1.00, "total_amount": 5.00},
        {"product_name": "Vitamin C", "sale_timestamp": "2026-09-02", "quantity": 3, "unit_price": 3.50, "total_amount": 10.50},
    ])
    
    # Sheet 3: Branch C (with pharmacy column)
    df3 = pd.DataFrame([
        {"pharmacy": "Branch C", "product_name": "Omeprazole", "sale_timestamp": "2026-09-03", "quantity": 2, "unit_price": 4.25, "total_amount": 8.50},
        {"pharmacy": "Branch C", "product_name": "Aspirin", "sale_timestamp": "2026-09-03", "quantity": 10, "unit_price": 0.50, "total_amount": 5.00},
    ])
    
    with pd.ExcelWriter(path, engine='openpyxl') as writer:
        df1.to_excel(writer, sheet_name='Branch_A', index=False)
        df2.to_excel(writer, sheet_name='Branch_B', index=False)
        df3.to_excel(writer, sheet_name='Branch_C', index=False)
    
    print(f"Generated: {path} (3 sheets)")
    return path

# 7. Excel with header row offset (tests unnamed column handling)
def generate_excel_with_offset_header():
    path = os.path.join(OUTPUT_DIR, "excel_with_offset_header.xlsx")
    
    # Create data with a title row that should be skipped
    df = pd.DataFrame([
        {"product_name": "Paracetamol", "sale_timestamp": "2026-09-01", "quantity": 2, "unit_price": 1.50, "total_amount": 3.00},
        {"product_name": "Ibuprofen", "sale_timestamp": "2026-09-01", "quantity": 1, "unit_price": 2.00, "total_amount": 2.00},
    ])
    
    with pd.ExcelWriter(path, engine='openpyxl') as writer:
        # Write a title row first
        workbook = writer.book
        worksheet = workbook.create_sheet("Sales")
        worksheet.cell(row=1, column=1, value="Monthly Sales Report - September 2026")
        # Write actual headers at row 2
        for col_idx, col_name in enumerate(df.columns, 1):
            worksheet.cell(row=2, column=col_idx, value=col_name)
        for row_idx, row in df.iterrows():
            for col_idx, value in enumerate(row, 1):
                worksheet.cell(row=row_idx + 3, column=col_idx, value=value)
    
    print(f"Generated: {path} (with offset header)")
    return path

# 8. Inventory events CSV
def generate_inventory_csv():
    data = [
        {"product_name": "Paracetamol 500mg", "batch_id": "BATCH-001", "event_type": "receipt", "quantity": 100, "unit_cost": 0.75, "event_timestamp": "2026-09-01 08:00:00", "expiry_date": "2027-08-01"},
        {"product_name": "Paracetamol 500mg", "batch_id": "BATCH-001", "event_type": "sale", "quantity": -10, "unit_cost": 0.75, "event_timestamp": "2026-09-01 10:30:00", "expiry_date": "2027-08-01"},
        {"product_name": "Ibuprofen 400mg", "batch_id": "BATCH-002", "event_type": "receipt", "quantity": 50, "unit_cost": 1.20, "event_timestamp": "2026-09-01 09:00:00", "expiry_date": "2027-07-15"},
        {"product_name": "Amoxicillin 250mg", "batch_id": "BATCH-003", "event_type": "expiry_writeoff", "quantity": -5, "unit_cost": 0.80, "event_timestamp": "2026-09-02 10:00:00", "expiry_date": "2026-09-01"},
        {"product_name": "Vitamin C 1000mg", "batch_id": "BATCH-004", "event_type": "damage", "quantity": -2, "unit_cost": 2.00, "event_timestamp": "2026-09-02 14:00:00", "expiry_date": "2027-06-30"},
    ]
    df = pd.DataFrame(data)
    path = os.path.join(OUTPUT_DIR, "inventory_events.csv")
    df.to_csv(path, index=False)
    print(f"Generated: {path} ({len(df)} rows)")
    return path

# 9. ZIP archive with multiple CSVs
def generate_zip_archive():
    import zipfile
    import io
    
    zip_path = os.path.join(OUTPUT_DIR, "bulk_upload.zip")
    
    # Create CSV 1
    df1 = pd.DataFrame([
        {"product_name": "Paracetamol", "sale_timestamp": "2026-09-01", "quantity": 2, "unit_price": 1.50, "total_amount": 3.00},
        {"product_name": "Ibuprofen", "sale_timestamp": "2026-09-01", "quantity": 1, "unit_price": 2.00, "total_amount": 2.00},
    ])
    
    # Create CSV 2
    df2 = pd.DataFrame([
        {"product_name": "Amoxicillin", "sale_timestamp": "2026-09-02", "quantity": 5, "unit_price": 1.00, "total_amount": 5.00},
        {"product_name": "Vitamin C", "sale_timestamp": "2026-09-02", "quantity": 3, "unit_price": 3.50, "total_amount": 10.50},
    ])
    
    # Create CSV 3 (multi-pharmacy)
    df3 = pd.DataFrame([
        {"pharmacy": "Branch A", "product_name": "Omeprazole", "sale_timestamp": "2026-09-03", "quantity": 2, "unit_price": 4.25, "total_amount": 8.50},
        {"pharmacy": "Branch B", "product_name": "Aspirin", "sale_timestamp": "2026-09-03", "quantity": 10, "unit_price": 0.50, "total_amount": 5.00},
    ])
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("branch_a_sales.csv", df1.to_csv(index=False))
        zf.writestr("branch_b_sales.csv", df2.to_csv(index=False))
        zf.writestr("multi_branch_sales.csv", df3.to_csv(index=False))
    
    print(f"Generated: {zip_path} (3 files in zip)")
    return zip_path

if __name__ == "__main__":
    print("Generating comprehensive test data files...\n")
    
    generate_clean_csv()
    generate_multi_pharmacy_csv()
    generate_validation_test_csv()
    generate_arabic_headers_csv()
    generate_large_csv()
    generate_multi_sheet_excel()
    generate_excel_with_offset_header()
    generate_inventory_csv()
    generate_zip_archive()
    
    print(f"\nAll files generated in: {OUTPUT_DIR}")
    print("\nFiles created:")
    for f in sorted(os.listdir(OUTPUT_DIR)):
        size = os.path.getsize(os.path.join(OUTPUT_DIR, f))
        print(f"  {f} ({size} bytes)")