from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_session():
    """Dependency to get a SQLAlchemy session per request.

    Yield-style so the session is always closed when the request ends, even on
    an exception. Previously the bare session was returned and never closed,
    leaking a connection from the pool on every request.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()