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
wire it into httpx/httpcore so a cancel reaches the provider socket:
every stream the pool opens registers ``sock.shutdown(SHUT_RDWR)`` on the
token, and ``connect_tcp`` refuses to dial once it is cancelled (the SDK's
retries then die instantly instead of redialling). Measured: cancel to
thread-exit 0.3 s bare (plain and TLS), under ~1.8 s through the anthropic
SDK with its default retries (backoff sleeps only).
"""

from __future__ import annotations

import logging
import socket
import threading
from typing import Any, Callable, Iterable, Optional

import httpcore
import httpx

logger = logging.getLogger("parker.brain.transport")

# Read timeout for a realtime lookup's provider call. The bridge drops any
# worker result after its own 30 s budget (realtime.WORKER_TIMEOUT_SECONDS —
# not imported here: transport must stay below realtime), so the SDK's
# 10-minute default only kept abandoned threads alive.
PROVIDER_TIMEOUT_SECONDS = 35.0


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


class _CancellableStream(httpcore.NetworkStream):
    """A network stream whose socket is shut down when the token fires.

    Composition over ``httpcore.NetworkStream`` (public), delegating every
    operation. ``start_tls`` re-wraps: ``wrap_socket`` detaches the fd from
    the plain socket, so the TLS socket is the one that must be shut down.
    """

    def __init__(self, inner: httpcore.NetworkStream, token: CancelToken) -> None:
        self._inner = inner
        self._token = token
        sock = inner.get_extra_info("socket")
        if sock is not None:
            token.on_cancel(lambda: sock.shutdown(socket.SHUT_RDWR))

    def read(self, max_bytes: int, timeout: Optional[float] = None) -> bytes:
        return self._inner.read(max_bytes, timeout)

    def write(self, buffer: bytes, timeout: Optional[float] = None) -> None:
        self._inner.write(buffer, timeout)

    def close(self) -> None:
        self._inner.close()

    def start_tls(
        self,
        ssl_context: Any,
        server_hostname: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> httpcore.NetworkStream:
        upgraded = self._inner.start_tls(ssl_context, server_hostname, timeout)
        return _CancellableStream(upgraded, self._token)

    def get_extra_info(self, info: str) -> Any:
        return self._inner.get_extra_info(info)


class _CancellableBackend(httpcore.NetworkBackend):
    """``httpcore.SyncBackend`` whose streams honour the token and whose
    dials are refused once it is cancelled (no redial on retry)."""

    def __init__(self, token: CancelToken) -> None:
        self._token = token
        self._inner = httpcore.SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: Optional[float] = None,
        local_address: Optional[str] = None,
        socket_options: Optional[Iterable[Any]] = None,
    ) -> httpcore.NetworkStream:
        if self._token.cancelled():
            raise httpcore.ConnectError("cancelled")
        stream = self._inner.connect_tcp(
            host, port, timeout=timeout, local_address=local_address, socket_options=socket_options
        )
        return _CancellableStream(stream, self._token)

    def connect_unix_socket(
        self,
        path: str,
        timeout: Optional[float] = None,
        socket_options: Optional[Iterable[Any]] = None,
    ) -> httpcore.NetworkStream:
        if self._token.cancelled():
            raise httpcore.ConnectError("cancelled")
        stream = self._inner.connect_unix_socket(path, timeout=timeout, socket_options=socket_options)
        return _CancellableStream(stream, self._token)

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)


class CancellableTransport(httpx.HTTPTransport):
    """``httpx.HTTPTransport`` dialling through ``_CancellableBackend``.

    httpx has no public hook for the network backend; the ONE private
    attribute used is ``HTTPTransport._pool`` (httpx 0.28.1 / httpcore
    1.0.9), replaced after the stock init with a pool built from public
    httpcore parts. tests/test_brain_transport.py pins the attribute so a
    dependency bump fails loudly instead of dialling uncancellably.
    """

    def __init__(
        self,
        token: CancelToken,
        *,
        verify: Any = True,
        limits: Optional[httpx.Limits] = None,
    ) -> None:
        super().__init__(verify=verify)
        if not isinstance(getattr(self, "_pool", None), httpcore.ConnectionPool):
            raise RuntimeError(
                "httpx.HTTPTransport no longer keeps its pool in _pool; "
                "CancellableTransport needs updating"
            )
        limits = limits or httpx.Limits(
            max_connections=100, max_keepalive_connections=20, keepalive_expiry=5.0
        )
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(verify=verify),
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            network_backend=_CancellableBackend(token),
        )


def provider_http_client(
    token: CancelToken, *, timeout: float = PROVIDER_TIMEOUT_SECONDS
) -> httpx.Client:
    """The HTTP client a background worker hands its providers.

    Injected into the anthropic SDK (which adopts its timeout) and the
    OpenClaw gateway; the worker closes it when the lookup ends. This is
    the one seam tests replace with a ``MockTransport`` client.
    """

    return httpx.Client(
        timeout=httpx.Timeout(timeout, connect=5.0),
        transport=CancellableTransport(token),
    )
