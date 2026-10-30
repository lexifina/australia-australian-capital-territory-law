"""Reads the retrieved responses of the ACT Legislation Register for the ACT builder; nothing here touches the network.

Each retrieved response is kept as `<cache>/<h>` (h = the first 40 hex digits of the sha256 of its URL); a 404 is kept
as the NOT_FOUND marker, so a rebuild reads the same bytes.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / 'cache' / 'http'
HOST = 'https://www.legislation.act.gov.au'
NOT_FOUND = b'<!-- lexifina-cache: HTTP 404 -->'


def cache_path(url: str) -> Path:
    return CACHE / hashlib.sha256(url.encode()).hexdigest()[:40]


def cached(url: str) -> bytes | None:
    p = cache_path(url)
    return p.read_bytes() if p.exists() else None
