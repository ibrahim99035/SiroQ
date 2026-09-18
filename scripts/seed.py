from uuid import uuid4

from sqlalchemy import text, select

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal
from app.models.models import Associations, Pharmacies, Users
from app.security import hash_password

# Stable demo tenant id so RLS context and idempotent lookups agree.
DEMO_ASSOC_ID = "11111111-1111-4111-8111-111111111111"


def idempotent_create():
    """Create seed data idempotently, respecting row-level security.

    The script connects as the least-privilege ``siroq_app`` role, so it must
    set the RLS tenant variable to the demo association before doing any work,
    otherwise FORCE RLS would hide every row and block every insert.
    """
    db = SessionLocal()
    try:
        db.execute(
            text("SELECT set_config('app.current_association_id', :aid, true)"),
            {"aid": DEMO_ASSOC_ID},
        )

        # 1. Association "Demo Pharmacy Group"
        assoc = db.execute(
            select(Associations).where(Associations.id == DEMO_ASSOC_ID)
        ).scalar_one_or_none()
        if not assoc:
            assoc = Associations(id=DEMO_ASSOC_ID, name="Demo Pharmacy Group")
            db.add(assoc)
            db.flush()
            print(f"Created association: {assoc.id}")

        # 2. Pharmacy "Demo Branch 1"
        pharm = db.execute(
            select(Pharmacies).where(
                Pharmacies.association_id == DEMO_ASSOC_ID,
                Pharmacies.name == "Demo Branch 1",
            )
        ).scalar_one_or_none()
        if not pharm:
            pharm = Pharmacies(
                name="Demo Branch 1",
                association_id=DEMO_ASSOC_ID,
                lat=40.7128,
                lng=-74.0060,
                region="NY",
                timezone="America/New_York",
            )
            db.add(pharm)
            db.flush()
            print(f"Created pharmacy: {pharm.id}")

        seed_users = [
            ("admin@siroq.local", "Demo Admin", "association_admin", None),
            ("manager@siroq.local", "Demo Manager", "pharmacy_manager", pharm.id),
            ("steward@siroq.local", "Demo Steward", "data_steward", pharm.id),
            ("analyst@siroq.local", "Demo Analyst", "analyst", None),
            ("viewer@siroq.local", "Demo Viewer", "viewer", None),
        ]
        for email, full_name, role, pharmacy_id in seed_users:
            existing = db.execute(
                select(Users).where(Users.email == email)
            ).scalar_one_or_none()
            if existing:
                # Update password hash to current scheme
                existing.hashed_password = hash_password("ChangeMe123!")
                db.flush()
                print(f"Updated password for {role}: {email}")
                continue
            user = Users(
                email=email,
                hashed_password=hash_password("ChangeMe123!"),
                full_name=full_name,
                role=role,
                association_id=DEMO_ASSOC_ID,
                pharmacy_id=pharmacy_id,
            )
            db.add(user)
            db.flush()
            print(f"Created {role}: {email}")

        db.commit()
        print("Seed data committed successfully.")
    except Exception as e:  # noqa: BLE001
        db.rollback()
        print(f"Error seeding data: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    # Tables are created/managed by Alembic (`make migrate`), never here.
    idempotent_create()