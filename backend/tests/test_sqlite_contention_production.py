"""Production-shaped SQLite contention for the realtime persistence helpers.

Every other DB-touching test runs on conftest's fixture engine (WAL,
synchronous=OFF, busy timeout 30 s), and every existing "database is
locked" test injects a fake exception at the session factory. Nothing
drove ``_with_local_write_retries`` / ``record_event_sync`` /
``_record_exchange_sync`` / ``_finalize_session_sync`` against the engine
Parker actually ships — ``app/db/database.py`` sets no pragmas, so the
file runs a DELETE journal with pysqlite's default 5 s busy timeout — while
another connection holds a real file lock (discovery P0.4a, 2026-09-02).

The contender here models "another Parker process" (server, talk loop and
digest share one file): its own connection, never the in-process
``_db_write_lock``, holding SQLite's RESERVED lock inside each
transaction. Loss is observed only the way production would see it — row
counts and the ``write lost`` WARNING — because the wrapper never raises.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.db.database import create_tables
from app.db.models import CallLog
from app.memory.models import ConversationMemory
from app.parker import realtime, session_review
from app.parker.screen import get_screen_state
from app.parker.session_review import RealtimeSessionEvent

# Generous: a loaded CI box must turn a slow run into a slow pass, not a
# hung worker. No latency is asserted anywhere in this file.
JOIN_TIMEOUT_S = 120.0


@dataclass
class ProductionDb:
    engine: object
    Session: Callable[[], object]
    busy_timeout_s: float


@dataclass
class ContenderStats:
    commits: int = 0
    errors: list[str] = field(default_factory=list)


@pytest.fixture
def production_db(tmp_path_factory, monkeypatch):
    """A file engine built exactly the way app/db/database.py builds Parker's.

    Points ``realtime._db_session_factory`` at it (the seam realtime_db and
    the voice_world harness already use), mirroring ``_make_db``'s
    create_tables()-per-session — which is itself a write transaction
    (the research-handoff retention backfill UPDATE runs unconditionally),
    so every bridge write below contends for the lock twice, exactly as in
    production. The PRAGMA guard keeps a future "helpful" WAL/timeout
    pragma from silently turning this back into the fixture shape every
    other test already covers.
    """

    db_dir = tmp_path_factory.mktemp("parker-db-production")
    engine = create_engine(
        f"sqlite:///{db_dir / 'parker.db'}", connect_args={"check_same_thread": False}
    )
    create_tables(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    with engine.connect() as conn:
        journal_mode = conn.execute(text("PRAGMA journal_mode")).scalar()
        busy_timeout_ms = conn.execute(text("PRAGMA busy_timeout")).scalar()
    assert journal_mode == "delete", (
        f"expected production defaults (DELETE journal), got journal_mode={journal_mode!r}"
    )

    def factory():
        create_tables(bind=engine)
        return Session()

    monkeypatch.setattr(realtime, "_db_session_factory", factory)
    try:
        yield ProductionDb(
            engine=engine, Session=Session, busy_timeout_s=float(busy_timeout_ms) / 1000.0
        )
    finally:
        engine.dispose()


def _foreign_writer(
    Session: Callable[[], object],
    *,
    stop: threading.Event,
    held: threading.Event,
    hold_s: float,
    gap_s: float,
    once: bool,
    stats: ContenderStats,
) -> None:
    """Another process's writer: own connection, no ``_db_write_lock``.

    ``flush()`` runs BEGIN + INSERT, so the connection holds RESERVED from
    ``held.set()`` until ``commit()``. Every exception is recorded, never
    raised — pytest.ini turns a thread exception into a test failure.
    """

    while not stop.is_set():
        try:
            session = Session()
            try:
                session.add(
                    CallLog(call_sid=f"foreign-{stats.commits}", call_type="foreign")
                )
                session.flush()
                held.set()
                time.sleep(hold_s)
                session.commit()
                stats.commits += 1
            except Exception as exc:  # noqa: BLE001 — recorded for the assertions
                stats.errors.append(repr(exc))
                session.rollback()
            finally:
                session.close()
        except Exception as exc:  # noqa: BLE001
            stats.errors.append(repr(exc))
        if once:
            return
        time.sleep(gap_s)


def _join(thread: threading.Thread, what: str) -> None:
    thread.join(timeout=JOIN_TIMEOUT_S)
    assert not thread.is_alive(), f"{what} still running after {JOIN_TIMEOUT_S:.0f} s"


@dataclass
class StormOutcome:
    events: int
    memories: int
    ended: bool
    screen_heard: str | None
    locked_errors: list[str]
    lost: list[str]
    contender: ContenderStats


def _run_storm(
    prod: ProductionDb,
    caplog,
    *,
    hold_s: float,
    gap_s: float,
    once: bool,
    n_journal: int,
    n_mirror: int,
) -> StormOutcome:
    """Foreign contender first, then the bridge's write storm, then finalize.

    The workers only start once the contender reports RESERVED held, so
    the first bridge INSERT is provably against a live foreign lock
    whatever the scheduler does.
    """

    call_sid = "REALTIME-contention"
    stop = threading.Event()
    held = threading.Event()
    stats = ContenderStats()
    locked: list[str] = []
    locked_guard = threading.Lock()

    def observing(write: Callable[[], None]) -> Callable[[], None]:
        def inner() -> None:
            try:
                write()
            except OperationalError as exc:
                with locked_guard:
                    locked.append(str(exc.orig))
                raise

        return inner

    def journal(seq: int) -> None:
        realtime._with_local_write_retries(
            "session event",
            observing(
                lambda: session_review.record_event_sync(
                    realtime._make_db,
                    call_sid,
                    seq,
                    "turn",
                    heard=f"heard {seq}",
                    said=f"said {seq}",
                )
            ),
        )

    def mirror(i: int) -> None:
        realtime._record_exchange_sync(f"heard {i}", f"speech {i}")

    contender = threading.Thread(
        target=_foreign_writer,
        args=(prod.Session,),
        kwargs={
            "stop": stop,
            "held": held,
            "hold_s": hold_s,
            "gap_s": gap_s,
            "once": once,
            "stats": stats,
        },
        name="foreign-parker-process",
    )
    workers = [threading.Thread(target=journal, args=(i,)) for i in range(n_journal)]
    workers += [threading.Thread(target=mirror, args=(i,)) for i in range(n_mirror)]

    caplog.set_level(logging.WARNING)
    contender.start()
    try:
        assert held.wait(timeout=JOIN_TIMEOUT_S), "foreign contender never took the lock"
        for worker in workers:
            worker.start()
        for worker in workers:
            _join(worker, "bridge writer")
        realtime._finalize_session_sync(
            call_sid, [("when does Alcaraz play next", "Friday")]
        )
    finally:
        stop.set()
        _join(contender, "foreign contender")

    lost = [
        record.getMessage() for record in caplog.records if "write lost" in record.getMessage()
    ]
    db = prod.Session()
    try:
        call = db.query(CallLog).filter(CallLog.call_sid == call_sid).one()
        events = (
            db.query(RealtimeSessionEvent)
            .filter(RealtimeSessionEvent.call_log_id == call.id)
            .count()
        )
        memories = (
            db.query(ConversationMemory)
            .filter(
                ConversationMemory.call_log_id == call.id,
                ConversationMemory.source == "realtime",
            )
            .count()
        )
        ended = call.ended_at is not None
        screen = get_screen_state(db)
        screen_heard = screen.heard if screen is not None else None
    finally:
        db.close()
    return StormOutcome(
        events=events,
        memories=memories,
        ended=ended,
        screen_heard=screen_heard,
        locked_errors=list(locked),
        lost=lost,
        contender=stats,
    )


def test_production_defaults_lose_no_bridge_write_under_foreign_lock_contention(
    production_db, caplog
):
    """A foreign writer cycling 20 ms holds costs the bridge nothing but waits.

    Below the busy timeout SQLite's busy handler absorbs the contention
    (the wrapper never even sees an error), and every journal row, the
    screen mirror, the topic memory and the session end still land.
    """

    out = _run_storm(
        production_db, caplog, hold_s=0.02, gap_s=0.03, once=False, n_journal=12, n_mirror=3
    )

    assert out.events == 12
    assert out.memories == 1
    assert out.ended is True
    assert out.screen_heard is not None and out.screen_heard.startswith("heard ")
    assert out.contender.commits >= 1
    assert out.contender.errors == []
    assert out.lost == []


def test_a_lock_held_past_the_default_busy_timeout_costs_a_retry_never_the_write(
    production_db, caplog
):
    """One foreign transaction outliving the 5 s busy timeout: a retry, not a loss.

    Slow by construction (~6 s wall): the contender must hold RESERVED
    longer than pysqlite's default busy timeout so that a wrapped write
    really raises ``database is locked``. It is the only test proving
    ``_with_local_write_retries`` copes with a genuine busy-timeout
    expiry rather than an injected exception — the discovery probe showed
    the same storm losing a journal row once the wrapper was cut to a
    single attempt. Rows and the ``write lost`` WARNING are the only
    observables; no latency is asserted.
    """

    hold_s = 6.0
    assert hold_s > production_db.busy_timeout_s, (
        "premise: the hold must outlive the connection's busy timeout "
        f"({production_db.busy_timeout_s} s)"
    )

    out = _run_storm(
        production_db, caplog, hold_s=hold_s, gap_s=0.0, once=True, n_journal=4, n_mirror=1
    )

    assert out.locked_errors, "expected at least one real busy-timeout expiry"
    assert all("database is locked" in message for message in out.locked_errors)
    assert out.events == 4
    assert out.memories == 1
    assert out.ended is True
    assert out.screen_heard == "heard 0"
    assert out.contender.commits == 1
    assert out.contender.errors == []
    assert out.lost == []
