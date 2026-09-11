from alembic import context
import sys
import os

# add the project root so 'app' package can be imported
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from app.config import settings

target_metadata = None

def run_migrations_offline():
    """Run migrations in 'offline' mode."""
    context = context.get_context()
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
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()