from alembic import context
import sys
import os

# add the project root so 'app' package can be imported
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from app.config import settings
from app.database import Base
from app.models import service_models  # noqa: F401  (register tables on Base)

target_metadata = Base.metadata


def run_migrations_offline():
    """Run migrations in 'offline' mode."""
    context.configure(
        url=settings.MIGRATIONS_DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin():
        context.run_migrations()


def run_migrations_online():
    """Run migrations in 'online' mode."""
    from sqlalchemy import create_engine

    engine = create_engine(settings.MIGRATIONS_DATABASE_URL)
    try:
        # begin(), not connect(): when a connection is handed to
        # context.configure(connection=...), Alembic does not own the
        # transaction and will not commit it, and a bare connect() context
        # rolls back on exit. With connect() every migration below ran and was
        # then discarded -- "alembic upgrade head" exited 0 against an empty
        # database. begin() commits on success and rolls back on error.
        with engine.begin() as connection:
            # Postgres defaults both timeouts to 0 (wait forever), and Neon
            # keeps that default. A migration that contends for a lock then
            # hangs with no diagnostic at all. Bound them so the failure names
            # itself, and so a stuck Render predeploy step fails, not hangs.
            connection.exec_driver_sql("SET lock_timeout = '15s'")
            connection.exec_driver_sql("SET statement_timeout = '300s'")
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()