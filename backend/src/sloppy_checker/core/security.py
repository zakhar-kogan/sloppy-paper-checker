from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import socket
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import AppSettings, get_settings

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AccessContext:
    is_admin: bool
    owner_hash: str | None


def _guest_signature(session_id: str, expires: int, settings: AppSettings) -> str:
    payload = f"{session_id}.{expires}".encode()
    return hmac.new(settings.api_token.encode(), payload, hashlib.sha256).hexdigest()


def issue_guest_session(
    settings: AppSettings, current_value: str | None = None
) -> tuple[str, str, datetime]:
    session_id = secrets.token_urlsafe(24)
    if current_value and parse_guest_session(current_value, settings):
        try:
            session_id = current_value.rsplit(".", 2)[0]
        except (ValueError, TypeError):
            pass
    expires_at = datetime.now(UTC) + timedelta(hours=settings.report_retention_hours)
    expires = int(expires_at.timestamp())
    value = f"{session_id}.{expires}.{_guest_signature(session_id, expires, settings)}"
    owner_hash = hashlib.sha256(session_id.encode()).hexdigest()
    return value, owner_hash, expires_at


def parse_guest_session(value: str | None, settings: AppSettings) -> str | None:
    if not value:
        return None
    try:
        session_id, expires_raw, signature = value.rsplit(".", 2)
        expires = int(expires_raw)
    except (ValueError, TypeError):
        return None
    if expires <= int(datetime.now(UTC).timestamp()):
        return None
    if not hmac.compare_digest(signature, _guest_signature(session_id, expires, settings)):
        return None
    return hashlib.sha256(session_id.encode()).hexdigest()


def require_client_access(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    settings: AppSettings = Depends(get_settings),
) -> AccessContext:
    if request.url.path == "/v1/session":
        return AccessContext(False, None)
    if credentials is not None and hmac.compare_digest(
        credentials.credentials.encode(), settings.api_token.encode()
    ):
        return AccessContext(True, None)
    owner_hash = parse_guest_session(request.cookies.get(settings.guest_cookie_name), settings)
    if owner_hash:
        return AccessContext(False, owner_hash)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Start an anonymous session or provide the backend bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return bool(ip.is_global)


def validate_public_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only absolute HTTP(S) URLs are accepted")
    if parsed.username or parsed.password:
        raise ValueError("Credentials in URLs are not accepted")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ValueError("Private-network destinations are not accepted")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or 443)}
    except socket.gaierror as exc:
        raise ValueError("URL host could not be resolved") from exc
    if not addresses or not all(_is_public(address) for address in addresses):
        raise ValueError("Private, reserved, or non-global destinations are not accepted")
    return url


def validate_redirect_chain(urls: Iterable[str]) -> None:
    for url in urls:
        validate_public_url(url)


async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    return response
