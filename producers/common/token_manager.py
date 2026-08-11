"""OpenSky OAuth2 client-credentials token lifecycle (PRD §5.1, Manual 01 §A).

Tokens live 30 minutes; we refresh proactively at 25. On a 401 the caller
invalidates and retries once. The token never appears in logs.
"""

from __future__ import annotations

import logging
import os
import time

import requests

log = logging.getLogger(__name__)

TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
REFRESH_MARGIN_S = 300  # refresh 5 min before expiry: 30-min TTL -> refresh at 25
TIMEOUT_S = 15


class TokenManager:
    def __init__(self) -> None:
        self._client_id = os.environ["OPENSKY_CLIENT_ID"]
        self._client_secret = os.environ["OPENSKY_CLIENT_SECRET"]
        self._token: str | None = None
        self._refresh_at: float = 0.0

    def get(self) -> str:
        """Return a bearer token, refreshing when within the margin of expiry."""
        if self._token is None or time.monotonic() >= self._refresh_at:
            self._refresh()
        assert self._token is not None
        return self._token

    def invalidate(self) -> None:
        """Force a refresh on the next get() — call after a 401."""
        self._token = None

    def _refresh(self) -> None:
        response = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            timeout=TIMEOUT_S,
        )
        response.raise_for_status()
        payload = response.json()
        self._token = payload["access_token"]
        expires_in = int(payload.get("expires_in", 1800))
        self._refresh_at = time.monotonic() + max(expires_in - REFRESH_MARGIN_S, 60)
        log.info("token refreshed: expires_in=%ss (never logged)", expires_in)
