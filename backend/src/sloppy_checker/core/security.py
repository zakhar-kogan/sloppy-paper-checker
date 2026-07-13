from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import socket
from collections.abc import Iterable
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import AppSettings, get_settings

bearer = HTTPBearer(auto_error=False)


def require_api_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    settings: AppSettings = Depends(get_settings),
) -> None:
    valid = credentials is not None and hmac.compare_digest(
        credentials.credentials.encode(), settings.api_token.encode()
    )
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API token",
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


def _fernet(settings: AppSettings) -> Fernet:
    if settings.encryption_key:
        key = settings.encryption_key.encode()
    else:
        digest = hashlib.sha256(settings.api_token.encode()).digest()
        key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt_secret(value: str, settings: AppSettings) -> str:
    return _fernet(settings).encrypt(value.encode()).decode()


def decrypt_secret(value: str, settings: AppSettings) -> str:
    return _fernet(settings).decrypt(value.encode()).decode()


async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    return response

