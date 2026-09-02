"""Provider-boundary cancellation for background workers.

Power off means no provider work continues (docs/plans/2026-09-02-parker-
hermes-current-information-sprint.md, P0.1). A worker runs a synchronous
provider call on the threadpool; cancelling its asyncio task only abandons
the thread, and — measured on macOS — ``httpx.Client.close()`` from another
thread does not wake a socket read blocked in ``poll()``. Only shutting the
socket down does. So the cancel signal is carried by a token the worker
registers abort callbacks on (``sock.shutdown``), and the bridge fires it
first thing in its shutdown, before any persistence.

``CancelToken`` is the seam; ``CancellableTransport``/``provider_http_client``
(below, once landed) wire it into httpx/httpcore so a cancel reaches the
provider socket.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

logger = logging.getLogger("parker.brain.transport")


class CancelToken:
    """A one-shot cancel flag with abort callbacks.

    ``on_cancel(abort)`` registers a callable to run when the token is
    cancelled; if the token is already cancelled it runs immediately.
    ``cancel()`` sets the flag then runs every abort (each at most once;
    exceptions are logged, never raised — the caller is shutting down).
    Thread-safe: workers register from their thread, the bridge cancels
    from the event loop.
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._aborts: list[Callable[[], None]] = []

    def cancelled(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._event.wait(timeout)

    def on_cancel(self, abort: Callable[[], None]) -> None:
        with self._lock:
            if not self._event.is_set():
                self._aborts.append(abort)
                return
        self._run(abort)

    def cancel(self) -> None:
        with self._lock:
            if self._event.is_set():
                return
            self._event.set()
            aborts, self._aborts = self._aborts, []
        for abort in aborts:
            self._run(abort)

    @staticmethod
    def _run(abort: Callable[[], None]) -> None:
        try:
            abort()
        except Exception:  # noqa: BLE001 — an abort must never break shutdown
            logger.debug("cancel abort callback failed", exc_info=True)
