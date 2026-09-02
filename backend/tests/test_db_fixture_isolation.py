"""The test database gives every Session its own connection.

Regression pin for the harness itself (independent review, 2026-09-01):
on the old single shared in-memory connection, another thread's
``Session.close()`` — a ROLLBACK on the same connection — silently
discarded a writer's in-flight transaction with no exception. Realtime and
converse tests then flaked "unreproducibly", and a thread crash left the
concurrent-session test green. This test reproduces exactly that
interleaving and requires the write to survive.
"""

from __future__ import annotations

import threading

from sqlalchemy.orm import sessionmaker

from app.db.models import CallLog


def test_a_reader_closing_on_another_thread_cannot_roll_back_a_writer(db):
    factory = sessionmaker(bind=db.get_bind())
    flushed = threading.Event()
    reader_closed = threading.Event()
    failures: list[str] = []

    def writer() -> None:
        session = factory()
        try:
            session.add(CallLog(call_sid="ISOLATION-WRITER", call_type="converse"))
            session.flush()  # the INSERT is on the wire, not yet committed
            flushed.set()
            assert reader_closed.wait(timeout=5), "reader never ran"
            session.commit()
        except BaseException as exc:  # noqa: BLE001 — surfaced by the assertion below
            failures.append(repr(exc))
        finally:
            session.close()

    def reader() -> None:
        assert flushed.wait(timeout=5), "writer never flushed"
        session = factory()
        try:
            session.query(CallLog).count()
        finally:
            session.close()  # ROLLBACK — must only touch the reader's own transaction
        reader_closed.set()

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert failures == [], failures
    db.expire_all()
    assert db.query(CallLog).filter(CallLog.call_sid == "ISOLATION-WRITER").count() == 1


def test_sessions_on_different_threads_do_not_share_a_connection(db):
    factory = sessionmaker(bind=db.get_bind())
    seen: list[int] = []
    lock = threading.Lock()
    barrier = threading.Barrier(3, timeout=5)

    def hold() -> None:
        session = factory()
        try:
            session.execute(__import__("sqlalchemy").text("SELECT 1"))
            raw = session.connection().connection.dbapi_connection
            with lock:
                seen.append(id(raw))
            barrier.wait()  # every thread holds its connection at once
        finally:
            session.close()

    threads = [threading.Thread(target=hold) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert len(seen) == 3 and len(set(seen)) == 3, seen
