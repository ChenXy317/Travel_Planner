from datetime import datetime, timezone

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Base, SessionState

_engine: Engine | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_engine_for(path) -> Engine:
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return engine


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine_for(get_settings().db_path)
    return _engine


def reset_engine() -> None:
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


def init_db(bind: Engine | None = None) -> None:
    settings = get_settings()
    settings.plans_dir.mkdir(parents=True, exist_ok=True)
    engine = bind or get_engine()
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        if session.get(SessionState, 1) is None:
            session.add(
                SessionState(
                    id=1,
                    seen_urls_json="[]",
                    points_json="{}",
                    routes_json="{}",
                    weather_json="{}",
                    updated_at=now_iso(),
                )
            )
            session.commit()
