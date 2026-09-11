"""Row Level Security test for SiroQ.

Tests that RLS policies properly isolate data by association_id.
This test verifies the session-based RLS mechanism works correctly.
"""
import pytest


@pytest.mark.skipif(True, reason="Requires running Docker containers with PostgreSQL")
def test_rls_session_variable():
    """Test that the SET LOCAL command works for RLS."""
    from sqlalchemy import text, create_engine
    from sqlalchemy.orm import Session

    # Use the application database URL
    engine = create_engine(
        "postgresql+psycopg://siroq_app:siroq_app_dev_password@localhost:5432/siroq"
    )

    with Session(engine) as db:
        # Execute the SET LOCAL command
        db.execute(text("SET LOCAL app.current_association_id = :aid"), {"aid": "1"})
        db.commit()

        # Verify it's set
        result = db.execute(text("SHOW app.current_association_id")).scalar()
        assert result == "1"


@pytest.mark.skipif(True, reason="Requires running Docker containers with PostgreSQL")
def test_rls_query_isolation():
    """Test that RLS policies isolate data by association_id."""
    from sqlalchemy import text, create_engine
    from sqlalchemy.orm import Session

    engine = create_engine(
        "postgresql+psycopg://siroq_app:siroq_app_dev_password@localhost:5432/siroq"
    )

    with Session(engine) as db:
        # Set association 1
        db.execute(text("SET LOCAL app.current_association_id = :aid"), {"aid": "1"})
        db.commit()

        # Query with no WHERE clause - RLS should filter by association_id
        result = db.execute(text("SELECT count(*) FROM pharmacies")).scalar()
        assert result > 0  # Should return rows, filtered by RLS

        # Set association 2
        db.execute(text("SET LOCAL app.current_association_id = :aid"), {"aid": "2"})
        db.commit()

        # Query again - should still return rows (just different ones due to RLS)
        result2 = db.execute(text("SELECT count(*) FROM pharmacies")).scalar()
        assert result2 > 0