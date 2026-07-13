import socket

import pytest

from sloppy_checker.core.ingest import normalize_doi
from sloppy_checker.core.security import validate_public_url


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("doi:10.1000/XYZ.123", "10.1000/xyz.123"),
        ("https://doi.org/10.1038/s41586-024-00001-2", "10.1038/s41586-024-00001-2"),
        ("See 10.5555/ABC(2024).7.", "10.5555/abc(2024).7"),
    ],
)
def test_doi_normalization(raw, expected):
    assert normalize_doi(raw) == expected


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://localhost/admin",
        "http://127.0.0.1/private",
        "http://169.254.169.254/latest/meta-data",
        "https://user:pass@example.org/paper",
    ],
)
def test_ssrf_targets_are_rejected(url):
    with pytest.raises(ValueError):
        validate_public_url(url)


def test_dns_rebinding_private_resolution_is_rejected(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("10.0.0.4", 443))])
    with pytest.raises(ValueError, match="Private"):
        validate_public_url("https://apparently-public.example/paper")

