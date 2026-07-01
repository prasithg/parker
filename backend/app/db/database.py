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
    from app.db.models import Base  # noqa: F811 — ensure core models imported
    import app.conversation.repair_events  # noqa: F401
    import app.escalation.models  # noqa: F401
    import app.evening.session  # noqa: F401
    import app.exercises.session  # noqa: F401
    import app.memory.models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_db():
    """Dependency for FastAPI route injection."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
