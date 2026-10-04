from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker, declarative_base
from sqlalchemy.pool import NullPool

from app.config import settings


def _engine_kwargs(url: str) -> dict:
    """Pick a pool strategy that suits the target endpoint.

    Managed Postgres providers front the database with PgBouncer in transaction
    mode. In that mode a SQLAlchemy QueuePool is a second, redundant pool in
    front of the first: it holds a checked-out backend per concurrent request
    long after the transaction has been returned to PgBouncer, which is exactly
    what erodes the provider's connection headroom.

    The endpoint hostname is the only reliable signal available in a URL, and
    Neon names its pooled endpoints ``<id>-pooler.<region>…``. Direct and
    self-hosted connections keep the default pool.
    """
    host = make_url(url).host or ""
    if "-pooler" in host:
        return {"poolclass": NullPool, "pool_pre_ping": True}
    return {"pool_pre_ping": True}


engine = create_engine(settings.DATABASE_URL, **_engine_kwargs(settings.DATABASE_URL))
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()