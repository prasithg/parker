"""Database setup and connection."""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def create_tables():
    """Create all tables (v0 — no Alembic migration yet)."""
    from app.db.models import Base  # noqa: F811 — ensure models imported
    Base.metadata.create_all(bind=engine)


def get_db():
    """Dependency for FastAPI route injection."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
