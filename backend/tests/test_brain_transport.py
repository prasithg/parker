"""The provider boundary: a cancel token reaches the socket (P0.1 F1).

Power off means no provider work continues. Measured on macOS (scratchpad
probes, 2026-09-02): ``httpx.Client.close()`` from another thread does NOT
wake a socket read blocked in ``poll()``; only ``shutdown(SHUT_RDWR)`` does.
So ``CancellableTransport`` registers the socket shutdown on the token and
refuses to dial once cancelled. These tests pin the mechanism itself, once,
on a real loopback socket — the one place a MockTransport cannot stand in.
"""

from __future__ import annotations

import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import anthropic
import httpcore
import httpx
import pytest

from app.brain.transport import (
    PROVIDER_TIMEOUT_SECONDS,
    CancellableTransport,
    CancelToken,
    _CancellableBackend,
    _CancellableStream,
    provider_http_client,
)


def _slow_server(started: threading.Event, release: threading.Event) -> ThreadingHTTPServer:
    """A loopback provider that holds every response until *release*.
    ``server.requests`` counts the POSTs that reached it."""

    class Slow(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 — http.server naming
            self.rfile.read(int(self.headers.get("content-length", 0)))
            self.server.requests += 1
            started.set()
            release.wait(5.0)
            body = b"{}"
            try:
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except OSError:
                pass  # the client hung up — that is the point

        def log_message(self, *args) -> None:  # noqa: D102 — silence the test log
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    server.requests = 0
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_the_installed_sdk_takes_an_httpx_client():
    """``provider_http_client`` is an ``httpx.Client``; the SDK must accept
    one as ``http_client=``. anthropic 1.x moved to httpx2 and raises
    TypeError here — which claude.py swallows into ``brain is None``, so
    every bridged lookup silently became 'no brain' on a fresh install
    (review F1). Pin the contract loudly instead."""

    pin = "anthropic 1.x moved to httpx2; keep anthropic<1 until app/brain/transport.py is ported"
    assert issubclass(anthropic.DefaultHttpxClient, httpx.Client), pin
    client = httpx.Client()
    try:
        try:
            sdk = anthropic.Anthropic(api_key="x", http_client=client)
        except TypeError as exc:
            pytest.fail(f"{pin} (constructor raised {exc!r})")
        assert sdk._client is client, pin
    finally:
        client.close()


def test_cancel_token_unwinds_the_anthropic_sdk_on_a_real_socket():
    """Off means off at the provider boundary through the REAL SDK, not a
    fake that unblocks itself: ``messages.create`` blocked in a loopback
    read unwinds within 3 s of ``token.cancel()`` with the SDK's own
    ``APIConnectionError``, and its retries (max_retries=2, backoff sleeps
    only) never redial — exactly one request reaches the server. Control:
    over a stock ``httpx.HTTPTransport`` the thread is still blocked at 3 s."""

    started, release = threading.Event(), threading.Event()
    server = _slow_server(started, release)
    token = CancelToken()
    client = provider_http_client(token)
    sdk = anthropic.Anthropic(
        api_key="x",
        base_url=f"http://127.0.0.1:{server.server_address[1]}",
        max_retries=2,
        http_client=client,
    )
    outcome: dict = {}

    def worker() -> None:
        try:
            sdk.messages.create(
                model="m", max_tokens=8, messages=[{"role": "user", "content": "hi"}]
            )
            outcome["result"] = "returned"
        except Exception as exc:  # noqa: BLE001 — the exception IS the observation
            outcome["result"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        assert started.wait(3.0), "the SDK request never reached the loopback provider"
        time.sleep(0.2)
        assert thread.is_alive()  # blocked in the provider read
        cancelled_at = time.monotonic()
        token.cancel()
        thread.join(3.0)
        unwound_after = time.monotonic() - cancelled_at
        assert not thread.is_alive(), "the SDK call did not unwind within 3 s of cancel"
        assert unwound_after < 3.0
        assert isinstance(outcome["result"], anthropic.APIConnectionError), outcome["result"]
        assert server.requests == 1, "a post-cancel retry redialled the provider"
    finally:
        release.set()
        client.close()
        server.shutdown()
        server.server_close()


def test_cancel_token_aborts_an_inflight_provider_read():
    started, release = threading.Event(), threading.Event()
    server = _slow_server(started, release)
    url = f"http://127.0.0.1:{server.server_address[1]}/v1/messages"
    token = CancelToken()
    client = provider_http_client(token)
    outcome: dict = {}

    def worker() -> None:
        t0 = time.monotonic()
        try:
            client.post(url, json={})
            outcome["result"] = "returned"
        except Exception as exc:  # noqa: BLE001 — the exception IS the observation
            outcome["result"] = exc
        outcome["unwound_after"] = time.monotonic() - t0

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        assert started.wait(3.0), "the request never reached the loopback provider"
        assert thread.is_alive()  # blocked in the provider read
        cancelled_at = time.monotonic()
        token.cancel()
        thread.join(1.0)
        assert not thread.is_alive(), "the provider read did not unwind within 1 s of cancel"
        assert time.monotonic() - cancelled_at < 1.0
        assert isinstance(outcome["result"], httpx.TransportError), outcome["result"]
        # Once cancelled there is no redial: connect is refused at once.
        t0 = time.monotonic()
        with pytest.raises(httpx.ConnectError):
            client.post(url, json={})
        assert time.monotonic() - t0 < 0.5
    finally:
        release.set()
        client.close()
        server.shutdown()
        server.server_close()


def test_provider_http_client_carries_the_lookup_timeout():
    """Realtime lookups get a bounded read (the bridge drops anything after
    30 s anyway) instead of the SDK's 10-minute default."""

    assert PROVIDER_TIMEOUT_SECONDS == 35.0
    client = provider_http_client(CancelToken())
    try:
        assert client.timeout == httpx.Timeout(35.0, connect=5.0)
        assert provider_http_client(CancelToken(), timeout=3.0).timeout == httpx.Timeout(
            3.0, connect=5.0
        )
    finally:
        client.close()


def test_the_httpx_pool_seam_still_exists():
    """CancellableTransport swaps ``httpx.HTTPTransport._pool`` — the ONE
    private attribute it relies on (httpx 0.28 / httpcore 1.0). If a
    dependency bump renames it, this fails loudly instead of the transport
    silently dialling through the stock, uncancellable backend."""

    assert isinstance(httpx.HTTPTransport()._pool, httpcore.ConnectionPool)
    transport = CancellableTransport(CancelToken())
    assert isinstance(transport._pool, httpcore.ConnectionPool)
    assert isinstance(transport._pool._network_backend, _CancellableBackend)


def test_start_tls_rewraps_so_the_tls_socket_is_the_one_shut_down():
    """``wrap_socket`` detaches the fd from the plain socket: after start_tls
    only the TLS socket can be shut down, so the wrapper must re-wrap."""

    class FakeSock:
        def __init__(self) -> None:
            self.shutdowns: list[int] = []

        def shutdown(self, how: int) -> None:
            self.shutdowns.append(how)

    class FakeStream(httpcore.NetworkStream):
        def __init__(self, sock, upgraded=None) -> None:
            self.sock, self.upgraded = sock, upgraded

        def get_extra_info(self, info: str):
            return self.sock if info == "socket" else None

        def start_tls(self, ssl_context, server_hostname=None, timeout=None):
            return FakeStream(self.upgraded)

    plain, tls = FakeSock(), FakeSock()
    token = CancelToken()
    stream = _CancellableStream(FakeStream(plain, tls), token)
    upgraded = stream.start_tls(None, server_hostname="api.anthropic.com")
    assert isinstance(upgraded, _CancellableStream)
    assert upgraded.get_extra_info("socket") is tls
    token.cancel()
    assert tls.shutdowns == [socket.SHUT_RDWR]


def test_build_brain_adapter_hands_the_client_to_both_providers(monkeypatch):
    """``build_brain_adapter(http_client=...)`` is how a worker's cancellable
    client reaches the Claude SDK and the OpenClaw gateway; defaults stay
    None so the text lane and talk loops are untouched."""

    from app.brain.build import build_brain_adapter
    from app.brain.claude import build_brain_context
    from app.brain.openclaw import build_openclaw_gateway
    from app.config import settings

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host + request.url.path)
        if request.url.host == "gateway.test":
            return httpx.Response(200, json={"lines": ["He is watching the tennis."]})
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "m",
                "content": [{"type": "text", "text": "Friday night."}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(settings, "anthropic_api_key", "test-anthropic-key")
    brain = build_brain_adapter(http_client=client)
    assert brain is not None
    reply = brain.respond([], "when is the final?", build_brain_context())
    assert reply.speech == "Friday night."
    assert seen == ["api.anthropic.com/v1/messages"]

    monkeypatch.setattr(settings, "parker_openclaw_gateway_url", "http://gateway.test")
    gateway = build_openclaw_gateway(client=client)
    assert gateway is not None
    assert gateway.current_context() == ["He is watching the tennis."]
    assert seen[-1] == "gateway.test/parker/v1/context"
    assert build_openclaw_gateway() is not None  # the default path still builds
