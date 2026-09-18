#!/usr/bin/env python3
"""Test all SiroQ endpoints using the test client."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from starlette.testclient import TestClient
from scripts.seed import idempotent_create

# Seed database first
print("Seeding database...")
idempotent_create()

from app.main import app

client = TestClient(app)

def login(email, password="ChangeMe123!"):
    """Login and return cookies."""
    r = client.post("/login", data={"email": email, "password": password}, follow_redirects=False)
    return r

def test_endpoint(method, path, expected_status=None, **kwargs):
    """Test an endpoint and return response."""
    func = getattr(client, method.lower())
    r = func(path, **kwargs)
    status = "✓" if expected_status is None or r.status_code == expected_status else "✗"
    print(f"  {status} {method} {path} -> {r.status_code}")
    if expected_status and r.status_code != expected_status:
        print(f"    Expected: {expected_status}, Got: {r.status_code}")
        print(f"    Response: {r.text[:200]}")
    return r

print("\n" + "="*60)
print("TESTING ALL ENDPOINTS")
print("="*60)

# ============================================
# AUTH ENDPOINTS
# ============================================
print("\n--- AUTH ---")
# Login as admin
r = login("admin@siroq.local")
assert r.status_code == 303, f"Admin login failed: {r.status_code}"
print("✓ Admin login successful")

# Test login page
r = test_endpoint("GET", "/login", 200)

# Test logout
r = test_endpoint("POST", "/logout", 200)  # Returns login page, not redirect

# Test viewer login
r = login("viewer@siroq.local")
assert r.status_code == 303
print("✓ Viewer login successful")

# ============================================
# DASHBOARD ENDPOINTS (all roles)
# ============================================
print("\n--- DASHBOARDS ---")
# Login as admin for dashboard tests
login("admin@siroq.local")

test_endpoint("GET", "/dashboards/sales", 200)
test_endpoint("GET", "/dashboards/inventory", 200)

# Test with viewer
login("viewer@siroq.local")
test_endpoint("GET", "/dashboards/sales", 200)
test_endpoint("GET", "/dashboards/inventory", 200)

# Test with analyst
login("analyst@siroq.local")
test_endpoint("GET", "/dashboards/sales", 200)

# ============================================
# APPLICATIONS ENDPOINTS (ingest roles)
# ============================================
print("\n--- APPLICATIONS ---")
login("admin@siroq.local")

# List applications
test_endpoint("GET", "/applications", 200)

# New application page
test_endpoint("GET", "/applications/new", 200)

# Create application
r = test_endpoint("POST", "/applications/new", 303, data={
    "name": "Test App",
    "pharmacy_id": "",
    "source_type": "manual_upload"
})
# Get the created app ID from redirect
app_id = None
if r.status_code == 303:
    location = r.headers.get("location", "")
    if "/applications/" in location:
        app_id = location.split("/applications/")[-1]
        print(f"  Created application: {app_id}")
else:
    # If it returns 200, the form might have validation error, try with a pharmacy_id
    from app.database import SessionLocal
    from app.models.models import Pharmacies
    db = SessionLocal()
    pharm = db.query(Pharmacies).first()
    db.close()
    if pharm:
        r = test_endpoint("POST", "/applications/new", 303, data={
            "name": "Test App",
            "pharmacy_id": str(pharm.id),
            "source_type": "manual_upload"
        })
        if r.status_code == 303:
            location = r.headers.get("location", "")
            if "/applications/" in location:
                app_id = location.split("/applications/")[-1]
                print(f"  Created application: {app_id}")

if app_id:
    # Application detail
    test_endpoint("GET", f"/applications/{app_id}", 200)
    
    # Upload page
    test_endpoint("GET", f"/applications/{app_id}/upload", 200)
    
    # Upload a CSV file
    with open("tests/generated_test_data/clean_sales.csv", "rb") as f:
        r = test_endpoint("POST", f"/applications/{app_id}/upload", 303, files={"file": ("clean.csv", f, "text/csv")})
    
    if r.status_code == 303:
        location = r.headers.get("location", "")
        if "/datasets/" in location and "/mapping" in location:
            dataset_id = location.split("/datasets/")[1].split("/")[0]
            print(f"  Created dataset: {dataset_id}")
            
            # Mapping page
            test_endpoint("GET", f"/applications/datasets/{dataset_id}/mapping", 200)
            
            # Confirm mapping
            field_map = '{"product_name": "product_name", "sale_timestamp": "sale_timestamp", "quantity": "quantity", "unit_price": "unit_price", "total_amount": "total_amount", "payment_method": "payment_method"}'
            r = test_endpoint("POST", f"/datasets/{dataset_id}/confirm", 200, data={
                "field_map": field_map,
                "pharmacy_identifier_column": ""
            })

# Test with data_steward
login("steward@siroq.local")
test_endpoint("GET", "/applications", 200)

# Test viewer denied
login("viewer@siroq.local")
test_endpoint("GET", "/applications", 403)

# ============================================
# DATA EXPLORER ENDPOINTS (edit roles)
# ============================================
print("\n--- DATA EXPLORER ---")
login("admin@siroq.local")

# Need to find an existing app with data
from app.database import SessionLocal
from app.models.models import Applications
from sqlalchemy import text

db = SessionLocal()
try:
    # Set RLS context
    db.execute(text("SELECT set_config('app.current_association_id', '11111111-1111-4111-8111-111111111111', true)"))
    app = db.query(Applications).first()
    if app:
        test_endpoint("GET", f"/applications/{app.id}/explorer", 200)
        test_endpoint("GET", f"/applications/{app.id}/explorer/rows", 200)
    else:
        print("  No applications found for explorer test")
finally:
    db.close()

# Test viewer denied
login("viewer@siroq.local")
test_endpoint("GET", "/applications", 403)

# ============================================
# ADMIN ENDPOINTS (association_admin only)
# ============================================
print("\n--- ADMIN ---")
login("admin@siroq.local")

test_endpoint("GET", "/admin/", 200)
test_endpoint("GET", "/admin/associations", 200)
test_endpoint("GET", "/admin/pharmacies", 200)
test_endpoint("GET", "/admin/users", 200)

# Test viewer denied
login("viewer@siroq.local")
test_endpoint("GET", "/admin/associations", 403)
test_endpoint("GET", "/admin/pharmacies", 403)
test_endpoint("GET", "/admin/users", 403)

# ============================================
# ROOT & HEALTH
# ============================================
print("\n--- ROOT & HEALTH ---")
test_endpoint("GET", "/", 303)  # Redirects to dashboards
test_endpoint("GET", "/health", 200)

# ============================================
# 403 PAGE
# ============================================
print("\n--- ERROR PAGES ---")
login("viewer@siroq.local")
test_endpoint("GET", "/admin/associations", 403)

print("\n" + "="*60)
print("ALL ENDPOINT TESTS COMPLETE")
print("="*60)