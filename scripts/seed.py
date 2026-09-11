from datetime import datetime

from app.database import Base, engine, SessionLocal
from app.models.models import Associations, Pharmacies, Users
from app.security import hash_password
from sqlalchemy import select


def idempotent_create():
    """Create seed data if it doesn't already exist."""
    db = SessionLocal()

    try:
        # 1. Create Association "Demo Pharmacy Group"
        assoc_name = "Demo Pharmacy Group"
        assoc_stmt = select(Associations).filter(
            Associations.name == assoc_name
        )
        assoc = db.execute(assoc_stmt).scalar_one_or_none()
        if not assoc:
            assoc = Associations(name=assoc_name)
            db.add(assoc)
            db.flush()
            print(f"Created association: {assoc.id}")

        # 2. Create Pharmacy "Demo Branch 1" under the association
        pharm_name = "Demo Branch 1"
        pharm_stmt = select(Pharmacies).filter(
            Pharmacies.name == pharm_name,
            Pharmacies.association_id == assoc.id,
        )
        pharmacy = db.execute(pharm_stmt).scalar_one_or_none()
        if not pharmacy:
            pharmacy = Pharmacies(
                name=pharm_name,
                association_id=assoc.id,
                lat=40.7128,
                lng=-74.0060,
                region="NY",
                timezone="America/New_York",
            )
            db.add(pharmacy)
            db.flush()
            print(f"Created pharmacy: {pharmacy.id}")

        # 3. Create association_admin user
        admin_email = "admin@siroq.local"
        admin_stmt = select(Users).filter(Users.email == admin_email)
        admin = db.execute(admin_stmt).scalar_one_or_none()
        if not admin:
            admin = Users(
                email=admin_email,
                hashed_password=hash_password("ChangeMe123!"),
                full_name="Demo Admin",
                role="association_admin",
                association_id=assoc.id,
                pharmacy_id=None,
            )
            db.add(admin)
            db.flush()
            print(f"Created association_admin: {admin.id}")

        # 4. Create pharmacy_manager user scoped to Demo Branch 1
        manager_email = "manager@siroq.local"
        manager_stmt = select(Users).filter(
            Users.email == manager_email,
            Users.pharmacy_id == pharmacy.id,
        )
        manager = db.execute(manager_stmt).scalar_one_or_none()
        if not manager:
            manager = Users(
                email=manager_email,
                hashed_password=hash_password("ChangeMe123!"),
                full_name="Demo Manager",
                role="pharmacy_manager",
                association_id=assoc.id,
                pharmacy_id=pharmacy.id,
            )
            db.add(manager)
            db.flush()
            print(f"Created pharmacy_manager: {manager.id}")

        db.commit()
        print("Seed data committed successfully.")

    except Exception as e:
        db.rollback()
        print(f"Error seeding data: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    # Ensure tables exist
    Base.metadata.create_all(bind=engine)
    idempotent_create()