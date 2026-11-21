from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import JSON, create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def configure(url: str | None = None) -> Engine:
    """(Re)configure the global engine. Tests call this with a test database URL."""
    global _engine, _SessionLocal
    url = url or get_settings().database_url
    kwargs: dict = {"pool_pre_ping": True, "future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs.update(pool_size=10, max_overflow=20)
    _engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(_engine, "connect")
        def _pragmas(dbapi_conn, _):  # sqlite (embedded Android mode and tests)
            dbapi_conn.isolation_level = None   # transactions are begun explicitly below
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
            dbapi_conn.execute("PRAGMA busy_timeout=15000")      # engine, API and scheduler threads share the file
            if ":memory:" not in url:
                dbapi_conn.execute("PRAGMA journal_mode=WAL")    # readers never block the writer
                dbapi_conn.execute("PRAGMA synchronous=NORMAL")

        @event.listens_for(_engine, "begin")
        def _begin(conn):  # one writer at a time: read-then-write transactions cannot hit BUSY_SNAPSHOT
            conn.exec_driver_sql("BEGIN IMMEDIATE")
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def engine() -> Engine:
    if _engine is None:
        configure()
    assert _engine is not None
    return _engine


def init_db() -> None:
    from . import models  # noqa: F401  (register tables)

    Base.metadata.create_all(engine())


def drop_all() -> None:
    from . import models  # noqa: F401

    Base.metadata.drop_all(engine())


_local = threading.local()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, rollback on any exception.

    A scope opened while the same thread already has one joins the outer transaction instead of
    opening a second connection (on SQLite a second writer in the same thread would block itself).
    """
    outer = getattr(_local, "session", None)
    if outer is not None:
        yield outer
        return
    if _SessionLocal is None:
        configure()
    assert _SessionLocal is not None
    s = _SessionLocal()
    _local.session = s
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        _local.session = None
        s.close()


def after_commit(fn) -> None:
    """Run `fn` after the current thread's transaction commits (immediately if there is none).
    Used to publish bus messages only once the rows they refer to are visible to other threads."""
    s = getattr(_local, "session", None)
    if s is None:
        fn()
        return
    event.listen(s, "after_commit", lambda _s: fn(), once=True)


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with session_scope() as s:
        yield s
