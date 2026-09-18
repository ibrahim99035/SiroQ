"""Authentication + RBAC tests.

Each check asserts a real HTTP status from the running app, including a negative
case per protected route (a role that must be denied actually gets 403).
"""
from conftest import login


def test_anon_redirected_to_login(client):
    for path in ("/admin/associations", "/applications", "/dashboards/sales"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code in (303, 307), f"{path} should redirect anon -> {r.status_code}"


def test_login_and_logout_flow(client, seeded_db):
    r = client.post("/login", data={"email": "admin@siroq.local", "password": "ChangeMe123!"},
                    follow_redirects=False)
    assert r.status_code == 303
    r = client.get("/dashboards/sales")
    assert r.status_code == 200

    r = client.post("/logout", follow_redirects=False)
    assert r.status_code == 303
    r = client.get("/dashboards/sales", follow_redirects=False)
    assert r.status_code in (303, 307)


def test_viewer_denied_admin_and_applications(client, seeded_db):
    login(client, "viewer@siroq.local")
    # viewer must be denied Admin (association_admin only) with 403
    r = client.get("/admin/associations", follow_redirects=False)
    assert r.status_code == 403, f"viewer /admin/associations -> {r.status_code}"
    # viewer must be denied the Applications module (ingest roles only) with 403
    r = client.get("/applications", follow_redirects=False)
    assert r.status_code == 403, f"viewer /applications -> {r.status_code}"
    # viewer CAN view dashboards (all roles)
    r = client.get("/dashboards/sales")
    assert r.status_code == 200, f"viewer /dashboards/sales -> {r.status_code}"


def test_analyst_denied_admin_but_allowed_dashboards(client, seeded_db):
    login(client, "analyst@siroq.local")
    assert client.get("/admin/associations", follow_redirects=False).status_code == 403
    r = client.get("/dashboards/sales")
    assert r.status_code == 200


def test_data_steward_allowed_applications(client, seeded_db):
    login(client, "steward@siroq.local")
    assert client.get("/applications", follow_redirects=False).status_code in (200, 303)
    assert client.get("/admin/associations", follow_redirects=False).status_code == 403