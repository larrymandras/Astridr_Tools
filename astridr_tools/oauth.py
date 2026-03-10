"""OAuth 2.0 token manager — reusable async token management with automatic refresh.

Supports Authorization Code + PKCE flow. Tokens persisted to disk.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import httpx
import structlog

log = structlog.get_logger()

_TOKEN_DIR = Path.home() / ".astridr" / "oauth"


def _atomic_json_write(path: Path, data: dict[str, Any]) -> None:
    """Write JSON atomically via temp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def _json_read(path: Path) -> dict[str, Any] | None:
    """Read JSON from path, return None on failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


class OAuthTokenManager:
    """Manages OAuth 2.0 tokens with automatic refresh."""

    def __init__(
        self,
        provider: str,
        client_id_env: str,
        client_secret_env: str,
        token_url: str,
        scopes: list[str],
        token_file: Path | None = None,
    ) -> None:
        self.provider = provider
        self.client_id = os.environ.get(client_id_env, "")
        self.client_secret = os.environ.get(client_secret_env, "")
        self.token_url = token_url
        self.scopes = scopes
        self._token_file = token_file or (_TOKEN_DIR / f"{provider}_token.json")
        self._token_data: dict[str, Any] | None = None
        self._client: httpx.AsyncClient | None = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    def token_file_path(self) -> Path:
        """Return the path to the token file."""
        return self._token_file

    def _load_token_data(self) -> dict[str, Any] | None:
        """Load token data from disk (cached in memory)."""
        if self._token_data is None:
            self._token_data = _json_read(self._token_file)
        return self._token_data

    def _save_token_data(self, data: dict[str, Any]) -> None:
        """Save token data to disk and update in-memory cache."""
        self._token_data = data
        _atomic_json_write(self._token_file, data)
        log.debug("oauth.token_saved", provider=self.provider)

    def _is_expired(self, data: dict[str, Any]) -> bool:
        """Check if the access token is expired (with 60s buffer)."""
        expires_at = data.get("expires_at", 0)
        return time.time() >= (expires_at - 60)

    async def get_access_token(self) -> str | None:
        """Get a valid access token, refreshing if expired.

        Returns None if no tokens are configured or refresh fails.
        """
        data = self._load_token_data()
        if data is None:
            return None

        if not self._is_expired(data):
            return data.get("access_token")

        # Try to refresh
        refreshed = await self.refresh_token()
        return refreshed

    async def refresh_token(self) -> str | None:
        """Refresh the access token using the refresh_token grant.

        Returns the new access token or None on failure.
        """
        data = self._load_token_data()
        if data is None or not data.get("refresh_token"):
            log.warning("oauth.no_refresh_token", provider=self.provider)
            return None

        if not self.client_id or not self.client_secret:
            log.warning("oauth.missing_credentials", provider=self.provider)
            return None

        client = self._ensure_client()
        try:
            resp = await client.post(
                self.token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": data["refresh_token"],
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
            )
            resp.raise_for_status()
            new_data = resp.json()

            # Merge — some providers don't return a new refresh_token
            token_data = {
                "access_token": new_data["access_token"],
                "refresh_token": new_data.get("refresh_token", data["refresh_token"]),
                "expires_at": time.time() + new_data.get("expires_in", 3600),
                "token_type": new_data.get("token_type", "Bearer"),
                "scope": new_data.get("scope", " ".join(self.scopes)),
            }
            self._save_token_data(token_data)
            log.info("oauth.token_refreshed", provider=self.provider)
            return token_data["access_token"]
        except httpx.HTTPStatusError as exc:
            log.error(
                "oauth.refresh_failed",
                provider=self.provider,
                status=exc.response.status_code,
                body=exc.response.text[:200],
            )
            return None
        except httpx.HTTPError as exc:
            log.error("oauth.refresh_error", provider=self.provider, error=str(exc))
            return None

    async def is_authenticated(self) -> bool:
        """Check if valid tokens exist (even if expired, refresh might work)."""
        data = self._load_token_data()
        if data is None:
            return False
        if not self._is_expired(data):
            return True
        # Try refresh
        token = await self.refresh_token()
        return token is not None
