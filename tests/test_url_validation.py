"""Tests for the SSRF guard in app.extract._validate_article_url."""
from __future__ import annotations

import pytest

from app.extract import _validate_article_url


# ── Allowed ────────────────────────────────────────────────────────────────

def test_public_https_url_passes():
    _validate_article_url("https://www.example.com/article")


def test_public_http_url_passes():
    _validate_article_url("http://example.com/article")


def test_url_with_port_passes():
    _validate_article_url("https://example.com:8443/article")


# ── Scheme checks ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("scheme", ["file", "ftp", "gopher", "data", "javascript", "about"])
def test_non_http_schemes_rejected(scheme):
    with pytest.raises(ValueError, match="http/https"):
        _validate_article_url(f"{scheme}://example.com/x")


def test_missing_scheme_rejected():
    with pytest.raises(ValueError):
        _validate_article_url("example.com/article")


def test_missing_hostname_rejected():
    with pytest.raises(ValueError, match="hostname"):
        _validate_article_url("http:///article")


# ── SSRF private network / loopback / link-local ───────────────────────────

@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1/",           # loopback
        "http://10.0.0.5/",             # 10/8
        "http://172.16.0.1/",           # 172.16/12
        "http://192.168.1.1/",          # 192.168/16
        "http://169.254.169.254/",      # AWS/GCP metadata
        "http://[::1]/",                # IPv6 loopback
    ],
)
def test_private_and_loopback_rejected(bad_url):
    with pytest.raises(ValueError, match="Private|loopback"):
        _validate_article_url(bad_url)


# ── Internal short-name Docker hostnames ───────────────────────────────────

@pytest.mark.parametrize("name", ["redis", "db", "worker", "localhost"])
def test_bare_hostnames_rejected(name):
    with pytest.raises(ValueError, match="Bare hostname"):
        _validate_article_url(f"http://{name}/article")
