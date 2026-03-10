"""OAuth setup CLI — one-time token provisioning for OAuth-based tools.

Usage:
    python -m astridr.tools.oauth_setup youtube
    python -m astridr.tools.oauth_setup linkedin
    python -m astridr.tools.oauth_setup zoho
"""

from __future__ import annotations

import json
import os
import sys
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse, parse_qs

_TOKEN_DIR = Path.home() / ".astridr" / "oauth"

# Provider configurations
PROVIDERS: dict[str, dict[str, Any]] = {
    "youtube": {
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scopes": ["https://www.googleapis.com/auth/youtube.readonly",
                    "https://www.googleapis.com/auth/youtube.upload",
                    "https://www.googleapis.com/auth/youtube.force-ssl"],
        "client_id_env": "YOUTUBE_CLIENT_ID",
        "client_secret_env": "YOUTUBE_CLIENT_SECRET",
    },
    "linkedin": {
        "auth_url": "https://www.linkedin.com/oauth/v2/authorization",
        "token_url": "https://www.linkedin.com/oauth/v2/accessToken",
        "scopes": ["r_liteprofile", "r_organization_social", "w_member_social", "r_basicprofile"],
        "client_id_env": "LINKEDIN_CLIENT_ID",
        "client_secret_env": "LINKEDIN_CLIENT_SECRET",
    },
    "zoho": {
        "auth_url": "https://accounts.zoho.{domain}/oauth/v2/auth",
        "token_url": "https://accounts.zoho.{domain}/oauth/v2/token",
        "scopes": ["ZohoCRM.modules.ALL", "ZohoCRM.settings.ALL"],
        "client_id_env": "ZOHO_CLIENT_ID",
        "client_secret_env": "ZOHO_CLIENT_SECRET",
        "domain_env": "ZOHO_DOMAIN",
        "default_domain": "com",
    },
}

REDIRECT_PORT = 8919
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"


class _CallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler that captures the OAuth callback code."""

    auth_code: str | None = None

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        if code:
            _CallbackHandler.auth_code = code
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>Authorization successful!</h1><p>You can close this tab.</p>")
        else:
            error = params.get("error", ["unknown"])[0]
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(f"<h1>Error: {error}</h1>".encode())

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Suppress HTTP server logs


def _resolve_url(url_template: str, domain: str) -> str:
    """Replace {domain} placeholder in URL."""
    return url_template.replace("{domain}", domain)


def setup_provider(provider_name: str) -> None:
    """Run the OAuth setup flow for a provider."""
    if provider_name not in PROVIDERS:
        print(f"Unknown provider: {provider_name}")
        print(f"Available: {', '.join(PROVIDERS)}")
        sys.exit(1)

    config = PROVIDERS[provider_name]
    client_id = os.environ.get(config["client_id_env"], "")
    client_secret = os.environ.get(config["client_secret_env"], "")

    if not client_id or not client_secret:
        print(f"Error: Set {config['client_id_env']} and {config['client_secret_env']} env vars first.")
        sys.exit(1)

    domain = os.environ.get(config.get("domain_env", ""), config.get("default_domain", "com"))
    auth_url = _resolve_url(config["auth_url"], domain)
    token_url = _resolve_url(config["token_url"], domain)

    # Build authorization URL
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(config["scopes"]),
        "access_type": "offline",
        "prompt": "consent",
    }
    full_auth_url = f"{auth_url}?{urlencode(params)}"

    print(f"\nOpening browser for {provider_name} authorization...")
    print(f"If browser doesn't open, visit:\n{full_auth_url}\n")
    webbrowser.open(full_auth_url)

    # Start local server to capture callback
    _CallbackHandler.auth_code = None
    server = HTTPServer(("localhost", REDIRECT_PORT), _CallbackHandler)
    server.timeout = 120
    print(f"Waiting for callback on port {REDIRECT_PORT}...")
    server.handle_request()
    server.server_close()

    code = _CallbackHandler.auth_code
    if not code:
        print("Error: No authorization code received.")
        sys.exit(1)

    # Exchange code for tokens
    import httpx
    resp = httpx.post(
        token_url,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    if resp.status_code != 200:
        print(f"Token exchange failed: {resp.status_code} {resp.text}")
        sys.exit(1)

    token_data = resp.json()
    save_data = {
        "access_token": token_data["access_token"],
        "refresh_token": token_data.get("refresh_token", ""),
        "expires_at": time.time() + token_data.get("expires_in", 3600),
        "token_type": token_data.get("token_type", "Bearer"),
        "scope": token_data.get("scope", " ".join(config["scopes"])),
    }

    token_file = _TOKEN_DIR / f"{provider_name}_token.json"
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(json.dumps(save_data, indent=2), encoding="utf-8")
    print(f"\nTokens saved to {token_file}")
    print("Setup complete!")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m astridr.tools.oauth_setup <provider>")
        print(f"Providers: {', '.join(PROVIDERS)}")
        sys.exit(1)
    setup_provider(sys.argv[1])


if __name__ == "__main__":
    main()
