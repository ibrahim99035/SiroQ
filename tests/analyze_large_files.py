#!/usr/bin/env python3
"""Generate large CSV files and analyze them through SiroQ ingestion pipeline."""
import os
import sys
import pandas as pd
from datetime import datetime, timedelta
import random
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.ingestion import read_frame, validate_rows, _number
from app.services.classification import classify_file

OUTPUT_DIR = Path("/media/ibrahim/New Volume2/Projects/SiroQ/tests/generated_test_data")
OUTPUT_DIR.mkdir(exist_ok=True)

# Seed for reproducibility
random.seed(42)

PRODUCTS = [
    "Paracetamol 500mg", "Ibuprofen 400mg", "Amoxicillin 250mg", "Vitamin C 1000mg",
    "Omeprazole 20mg", "Aspirin 100mg", "Cetirizine 10mg", "Loratadine 10mg",
    "Metformin 500mg", "Atorvastatin 20mg", "Amlodipine 5mg", "Losartan 50mg",
    "Simvastatin 40mg", "Levothyroxine 50mcg", "Pantoprazole 40mg", "Gabapentin 300mg",
    "Tramadol 50mg", "Codeine 30mg", "Diazepam 5mg", "Sertraline 50mg",
]

PHARMACIES = ["Branch A", "Branch B", "Branch C", "Branch D", "Branch E"]
PAYMENT_METHODS = ["card", "cash", "insurance", "mobile"]

def generate_large_sales_csv(filename: str, rows: int, include_pharmacy: bool = False):
    """Generate a large sales CSV file."""
    data = []
    base_date = datetime(2026, 1, 1)
    
    for i in range(rows):
        product = random.choice(PRODUCTS)
        qty = random.randint(1, 10)
        price = round(random.uniform(0.50, 15.00), 2)
        date = base_date + timedelta(days=random.randint(0, 364), hours=random.randint(8, 20), minutes=random.randint(0, 59))
        
        row = {
            "product_name": product,
            "sale_timestamp": date.strftime("%Y-%m-%d %H:%M:%S"),
            "quantity": qty,
            "unit_price": price,
            "total_amount": round(qty * price, 2),
            "payment_method": random.choice(PAYMENT_METHODS),
        }
        if include_pharmacy:
            row["pharmacy"] = random.choice(PHARMACIES)
        data.append(row)
    
    df = pd.DataFrame(data)
    path = OUTPUT_DIR / filename
    df.to_csv(path, index=False)
    print(f"Generated: {path} ({len(df)} rows, {os.path.getsize(path) / 1024:.1f} KB)")
    return path

def generate_messy_csv(filename: str, rows: int, error_rate: float = 0.1):
    """Generate a CSV with intentional data quality issues."""
    data = []
    base_date = datetime(2026, 1, 1)
    
    for i in range(rows):
        product = random.choice(PRODUCTS)
        qty = random.randint(1, 10)
        price = round(random.uniform(0.50, 15.00), 2)
        date = base_date + timedelta(days=random.randint(0, 364))
        
        # Introduce errors
        if random.random() < error_rate:
            # 30% negative quantity
            if random.random() < 0.3:
                qty = -abs(qty)
            # 30% negative price
            elif random.random() < 0.3:
                price = -abs(price)
            # 20% empty product
            elif random.random() < 0.2:
                product = ""
            # 20% bad date
            elif random.random() < 0.2:
                date = "not-a-date"
        
        row = {
            "product_name": product,
            "sale_timestamp": date.strftime("%Y-%m-%d") if isinstance(date, datetime) else date,
            "quantity": qty,
            "unit_price": price,
            "total_amount": round(qty * price, 2),
            "payment_method": random.choice(PAYMENT_METHODS),
        }
        data.append(row)
    
    # Add some exact duplicates
    if rows > 10:
        for _ in range(min(5, rows // 20)):
            data.append(data[random.randint(0, len(data)-1)].copy())
    
    df = pd.DataFrame(data)
    path = OUTPUT_DIR / filename
    df.to_csv(path, index=False)
    print(f"Generated: {path} ({len(df)} rows, {os.path.getsize(path) / 1024:.1f} KB)")
    return path

def analyze_file(filepath: Path, name: str, mapping: dict = None):
    """Analyze a CSV file through the ingestion pipeline."""
    print(f"\n{'='*60}")
    print(f"ANALYZING: {name}")
    print(f"File: {filepath}")
    print(f"{'='*60}")
    
    # 1. Classification
    print("\n1. COLUMN CLASSIFICATION")
    classification = classify_file(str(filepath))
    if "error" in classification:
        print(f"   ERROR: {classification['error']}")
        return None
    
    print(f"   File type: {classification.get('file_type', 'unknown')}")
    print(f"   Rows: {classification.get('row_count', 'unknown')}")
    print(f"   Columns found: {list(classification.get('columns', {}).keys())}")
    print(f"   Suggested mapping: {classification.get('suggested_mapping', {})}")
    print(f"   Confidence: {classification.get('confidence', {})}")
    
    # 2. Read frame
    print("\n2. DATA FRAME READING")
    try:
        df = read_frame(str(filepath))
        print(f"   Shape: {df.shape}")
        print(f"   Columns: {list(df.columns)}")
        print(f"   Dtypes:\n{df.dtypes}")
        print(f"   Sample (first 3 rows):\n{df.head(3).to_string()}")
    except Exception as e:
        print(f"   ERROR reading file: {e}")
        return None
    
    # 3. Validation (if mapping provided)
    if mapping:
        print("\n3. VALIDATION")
        # Use classification's suggested mapping if not provided
        test_mapping = mapping or classification.get('suggested_mapping', {})
        print(f"   Using mapping: {test_mapping}")
        
        # Ensure required fields exist
        required = ['product_name', 'quantity', 'unit_price', 'total_amount', 'sale_timestamp']
        test_mapping = {k: v for k, v in test_mapping.items() if v in df.columns}
        
        valid_indices, errors = validate_rows(df, test_mapping)
        print(f"   Valid rows: {len(valid_indices)}")
        print(f"   Invalid rows: {len(errors)}")
        if errors:
            print(f"   Error details (first 10):")
            for err in errors[:10]:
                print(f"     Row {err['row']}: {', '.join(err['reasons'])}")
        
        # Summary stats
        valid_df = df.iloc[valid_indices]
        print(f"\n   VALID DATA STATS:")
        print(f"     Total amount range: {valid_df[test_mapping.get('total_amount', 'total_amount')].min():.2f} - {valid_df[test_mapping.get('total_amount', 'total_amount')].max():.2f}")
        print(f"     Quantity range: {valid_df[test_mapping.get('quantity', 'quantity')].min()} - {valid_df[test_mapping.get('quantity', 'quantity')].max()}")
        print(f"     Unique products: {valid_df[test_mapping.get('product_name', 'product_name')].nunique()}")
        print(f"     Date range: {valid_df[test_mapping.get('sale_timestamp', 'sale_timestamp')].min()} to {valid_df[test_mapping.get('sale_timestamp', 'sale_timestamp')].max()}")
        
        return {
            "classification": classification,
            "shape": df.shape,
            "valid_rows": len(valid_indices),
            "invalid_rows": len(errors),
            "errors": errors[:20],
            "stats": {
                "total_amount_min": float(valid_df[test_mapping.get('total_amount', 'total_amount')].min()),
                "total_amount_max": float(valid_df[test_mapping.get('total_amount', 'total_amount')].max()),
                "unique_products": int(valid_df[test_mapping.get('product_name', 'product_name')].nunique()),
            }
        }
    
    return {"classification": classification, "shape": df.shape}

if __name__ == "__main__":
    print("Generating large test files...\n")
    
    # Generate files
    files = [
        ("large_sales_10k.csv", 10000, False),
        ("large_sales_50k.csv", 50000, False),
        ("large_multi_pharmacy_10k.csv", 10000, True),
        ("messy_sales_5k.csv", 5000, 0.15),
        ("messy_sales_20k.csv", 20000, 0.10),
    ]
    
    for fname, rows, pharmacy in files:
        if "messy" in fname:
            generate_messy_csv(fname, rows, error_rate=0.15 if "5k" in fname else 0.10)
        else:
            generate_large_sales_csv(fname, rows, include_pharmacy=pharmacy)
    
    print("\n\n" + "="*60)
    print("ANALYZING ALL FILES")
    print("="*60)
    
    # Standard mapping for clean files
    standard_mapping = {
        "product_name": "product_name",
        "sale_timestamp": "sale_timestamp",
        "quantity": "quantity",
        "unit_price": "unit_price",
        "total_amount": "total_amount",
        "payment_method": "payment_method",
    }
    
    # Multi-pharmacy mapping
    multi_mapping = {
        **standard_mapping,
        "pharmacy_identifier": "pharmacy",
    }
    
    results = {}
    
    # Analyze clean files
    for fname in ["large_sales_10k.csv", "large_sales_50k.csv"]:
        path = OUTPUT_DIR / fname
        results[fname] = analyze_file(path, fname, standard_mapping)
    
    # Analyze multi-pharmacy
    path = OUTPUT_DIR / "large_multi_pharmacy_10k.csv"
    results["large_multi_pharmacy_10k.csv"] = analyze_file(path, "large_multi_pharmacy_10k.csv", multi_mapping)
    
    # Analyze messy files
    for fname in ["messy_sales_5k.csv", "messy_sales_20k.csv"]:
        path = OUTPUT_DIR / fname
        results[fname] = analyze_file(path, fname, standard_mapping)
    
    # Save results
    import json
    results_path = OUTPUT_DIR / "analysis_results.json"
    with open(results_path, 'w') as f:
        # Convert non-serializable objects
        serializable = {}
        for k, v in results.items():
            if v:
                serializable[k] = {k2: v2 for k2, v2 in v.items() if k2 != 'classification'}
                if 'classification' in v:
                    serializable[k]['classification'] = {
                        'file_type': v['classification'].get('file_type'),
                        'row_count': v['classification'].get('row_count'),
                        'suggested_mapping': v['classification'].get('suggested_mapping'),
                        'confidence': v['classification'].get('confidence'),
                    }
        json.dump(serializable, f, indent=2, default=str)
    
    print(f"\n\nResults saved to: {results_path}")