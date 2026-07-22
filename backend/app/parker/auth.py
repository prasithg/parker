"""HTTP Basic auth seam for the caregiver decision surface.

Enforcement is opt-in: with ``DASHBOARD_PASSWORD`` unset (the default),
every route stays open so localhost demos and the runbook curl flows
need no credentials. Once a password is configured, the caregiver
decision surface — review feed/page, outbox, and action confirm/
execute/cancel — requires HTTP Basic sign-in.

Deliberately *not* gated: ``/parker/tick`` and ``/parker/resurface``,
which are the assistant-loop surface, not the caregiver's. Giving the
voice agent its own machine credential is a future slice.
"""

from __future__ import annotations

import secrets
from typing import Optional

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import settings

_basic = HTTPBasic(auto_error=False)


def require_dashboard_auth(
    credentials: Optional[HTTPBasicCredentials] = Depends(_basic),
) -> None:
    """FastAPI dependency: 401 unless auth is disabled or credentials match."""

    if not settings.dashboard_password:
        return
    if credentials is not None:
        username_ok = secrets.compare_digest(
            credentials.username.encode(), settings.dashboard_username.encode()
        )
        password_ok = secrets.compare_digest(
            credentials.password.encode(), settings.dashboard_password.encode()
        )
        if username_ok and password_ok:
            return
    raise HTTPException(
        status_code=401,
        detail="Caregiver review requires sign-in.",
        headers={"WWW-Authenticate": "Basic"},
    )


def require_configured_dashboard_auth(
    credentials: Optional[HTTPBasicCredentials] = Depends(_basic),
) -> str:
    """Require configured credentials for irreversible caregiver operations.

    Most localhost demo review routes intentionally stay open when no password
    is configured. Irreversible query redaction does not inherit that shortcut:
    families must configure dashboard auth, and the authenticated username is
    the audit actor rather than caller-supplied request text.
    """

    if not settings.dashboard_password:
        raise HTTPException(
            status_code=503,
            detail="Configure DASHBOARD_PASSWORD before irreversible query redaction.",
        )
    if credentials is not None:
        username_ok = secrets.compare_digest(
            credentials.username.encode(), settings.dashboard_username.encode()
        )
        password_ok = secrets.compare_digest(
            credentials.password.encode(), settings.dashboard_password.encode()
        )
        if username_ok and password_ok:
            return settings.dashboard_username
    raise HTTPException(
        status_code=401,
        detail="Caregiver review requires sign-in.",
        headers={"WWW-Authenticate": "Basic"},
    )
