"""Companion power authority: server-owned, single-owner, fail-closed.

The power switch is a product promise — off means nothing listens, nothing
wakes, nothing streams, nothing answers — and a promise the page alone
cannot keep: a second or stale tab kept its own wake/realtime sockets
after another tab turned Parker off, and a swallowed settings write left
the switch visibly off while the database stayed on (independent review
of PR #40, 2026-09-01). So the ENGINE owns power:

- ``claim`` turns Parker on for exactly one page (the *owner*): the page
  receives a secret token and a generation number that every companion
  audio socket must present. A claim is refused while another owner still
  holds a live socket — a screen that is actually listening cannot be
  silently displaced. Persistence happens BEFORE the in-memory flip: a
  failed write leaves Parker off (fail closed) and the page told.
- ``release`` turns Parker off for everyone: in-memory off first (no new
  socket can be authorized from this instant), every registered socket
  revoked, THEN persisted. A failed write still leaves every line dead —
  the page shows the failure and retries the write.
- ``authorize`` is what ``/converse/wake`` and ``/converse/realtime``
  check before a single audio frame flows; a mismatch is answered with a
  ``revoked`` frame naming the reason, never a silent hang.

One household, one process: module state, like the bridge slot counter.
"""

from __future__ import annotations

import logging
import secrets
import threading
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger("parker.companion_power")

Closer = Callable[[str], Awaitable[None]]


class PowerRefused(Exception):
    """A claim was refused: another owner is live, or persistence failed."""

    def __init__(self, status_code: int, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.reason = reason
        self.detail = detail


@dataclass
class _Registration:
    token: str
    kind: str  # "wake" | "realtime"
    close: Closer


@dataclass
class CompanionPower:
    on: bool = False
    generation: int = 0
    owner_token: Optional[str] = None
    owner_client: str = ""
    # True from a release() until the next claim(): "off in this process".
    # The route persists the durable flag AFTER revoking the lines, so a
    # revoked page that reads the settings inside that write window must
    # see OFF — not the still-ON durable flag plus "no owner", which is
    # exactly the engine-restart shape it re-claims on (negative-space
    # review, 2026-09-02). A fresh process starts False, so the restart
    # re-claim keeps working.
    released: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _next_id: int = 1
    _sockets: dict[int, _Registration] = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------
    # Transitions
    # ------------------------------------------------------------------

    def claim(
        self, persist: Callable[[bool], Any], *, client_id: str
    ) -> dict[str, Any]:
        """Turn Parker on for *client_id*; returns the owner credentials.

        ``persist`` writes the durable ``power_on`` flag (raising on
        failure). It runs under the lock so two tabs claiming at once are
        serialized and only one becomes the owner.
        """

        client_id = (client_id or "")[:64]
        with self._lock:
            # Every registered socket belongs to the current owner (claim
            # and release both clear the registry), so "any live socket"
            # means "the current owner is actually listening".
            if (
                self.on
                and self.owner_token is not None
                and self.owner_client != client_id
                and self._sockets
            ):
                raise PowerRefused(
                    409,
                    "elsewhere",
                    "Parker is already on and listening on another screen.",
                )
            try:
                persist(True)
            except Exception:  # noqa: BLE001 — the reason is logged; the page hears "not saved"
                logger.warning("companion power-on write failed", exc_info=True)
                raise PowerRefused(
                    503, "not_saved", "Parker could not save the switch — nothing is on."
                )
            # A stale owner (a page that claimed but never connected, or a
            # previous generation of this same page) is displaced here.
            previous = list(self._sockets.values())
            self._sockets.clear()
            self.generation += 1
            self.owner_token = secrets.token_urlsafe(24)
            self.owner_client = client_id
            self.on = True
            self.released = False
            gen = self.generation
            token = self.owner_token
        return {
            "power_on": True,
            "owner": token,
            "gen": gen,
            "displaced": [reg.close for reg in previous],
        }

    def release(self, persist: Optional[Callable[[bool], Any]] = None) -> dict[str, Any]:
        """Turn Parker off for everyone. Never raises before revoking.

        Returns the closers the (async) route must await, plus whether
        the durable write landed — the page shows a failed write and
        retries; the lines are dead either way. ``persist`` is optional:
        the route passes none and writes the flag itself AFTER revoking
        every line (``saved`` is then None here), so neither the revoke
        nor the ack ever waits behind SQLite (F1 probe 3b: his mic audio
        reached OpenAI a second after the switch while the write blocked).
        """

        with self._lock:
            self.on = False
            self.released = True
            self.generation += 1
            self.owner_token = None
            self.owner_client = ""
            revoked = list(self._sockets.values())
            self._sockets.clear()
            saved: Optional[bool] = None
            if persist is not None:
                saved = True
                try:
                    persist(False)
                except Exception:  # noqa: BLE001 — off in memory regardless
                    logger.warning("companion power-off write failed", exc_info=True)
                    saved = False
        return {
            "power_on": False,
            "saved": saved,
            "revoked": [reg.close for reg in revoked],
        }

    # ------------------------------------------------------------------
    # Socket authority
    # ------------------------------------------------------------------

    def authorize(self, token: str, gen: Any) -> Optional[str]:
        """None when *token*/*gen* may open a companion audio socket, else
        the refusal reason: ``power_off`` or ``not_owner``."""

        with self._lock:
            if not self.on or self.owner_token is None:
                return "power_off"
            try:
                generation = int(gen)
            except (TypeError, ValueError):
                return "not_owner"
            if token != self.owner_token or generation != self.generation:
                return "not_owner"
            return None

    def register(
        self, *, token: str, kind: str, close: Closer
    ) -> tuple[Optional[int], list[Closer]]:
        """Track one authorized socket so power-off can revoke it.

        Re-validates the owner under the lock: a power-off that landed
        between the route's authorize() and this call must not leave a
        socket serving under a dead token — ``(None, [])`` tells the route
        to refuse. A second realtime line for the same owner supersedes the
        first — one owner, one line; the page's own reconnect replaces its
        dead socket, and the fenced page ignores frames from the old one.
        """

        with self._lock:
            if not self.on or token != self.owner_token:
                return None, []
            superseded: list[Closer] = []
            if kind == "realtime":
                for sid, reg in list(self._sockets.items()):
                    if reg.kind == "realtime":
                        superseded.append(reg.close)
                        del self._sockets[sid]
            sid = self._next_id
            self._next_id += 1
            self._sockets[sid] = _Registration(token=token, kind=kind, close=close)
            return sid, superseded

    def unregister(self, sid: int) -> None:
        with self._lock:
            self._sockets.pop(sid, None)

    def live_sockets(self) -> dict[str, int]:
        with self._lock:
            counts: dict[str, int] = {}
            for reg in self._sockets.values():
                counts[reg.kind] = counts.get(reg.kind, 0) + 1
            return counts

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "power_on": self.on,
                "released": self.released,
                "gen": self.generation,
                "owner_client": self.owner_client,
                "live": {
                    kind: sum(1 for r in self._sockets.values() if r.kind == kind)
                    for kind in ("wake", "realtime")
                },
            }


authority = CompanionPower()
