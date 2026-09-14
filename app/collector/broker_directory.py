"""Independent broker directory lookup; never sends trading credentials.

Results are discovery candidates, not proof of terminal provisioning, endpoint
ownership, availability or successful investor authorization. This module does
not connect to MT5 or upload a terminal's servers.dat to another service.
"""

from dataclasses import dataclass
import json
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen


SEARCH_URL = "https://mt5.mtapi.io/Search"
MAX_RESPONSE_BYTES = 2_000_000
MAX_SERVERS = 5_000


class DirectoryError(ValueError):
    """Contains only a local error code, never provider response bodies."""


@dataclass(frozen=True)
class DirectoryServer:
    broker: str
    server: str
    access_points: tuple[str, ...]
    source: str = "mtapi.io/Search"
    verification: str = "directory_only"


def _text(value, maximum, error):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise DirectoryError(error)
    if any(unicodedata.category(c).startswith("C") for c in value):
        raise DirectoryError(error)
    return value.strip()


def parse_directory(payload) -> tuple[DirectoryServer, ...]:
    """Parse documented Company.results{name,access[]} without fuzzy matching."""
    if not isinstance(payload, list) or len(payload) > MAX_SERVERS:
        raise DirectoryError("INVALID_PROVIDER_RESPONSE")
    merged: dict[tuple[str, str], set[str]] = {}
    record_count = 0
    for company in payload:
        if not isinstance(company, dict):
            raise DirectoryError("INVALID_PROVIDER_RESPONSE")
        broker = _text(company.get("companyName"), 256, "INVALID_PROVIDER_RESPONSE")
        results = company.get("results")
        if not isinstance(results, list):
            raise DirectoryError("INVALID_PROVIDER_RESPONSE")
        for result in results:
            record_count += 1
            if record_count > MAX_SERVERS:
                raise DirectoryError("PROVIDER_RESPONSE_TOO_LARGE")
            if not isinstance(result, dict):
                raise DirectoryError("INVALID_PROVIDER_RESPONSE")
            server = _text(result.get("name"), 256, "INVALID_PROVIDER_RESPONSE")
            addresses = result.get("access")
            if not isinstance(addresses, list) or len(addresses) > 64:
                raise DirectoryError("INVALID_PROVIDER_RESPONSE")
            # Retain opaque endpoint strings for subsequent broker validation.
            # Nothing here makes them safe destinations for account credentials.
            points = {_text(a, 512, "INVALID_PROVIDER_RESPONSE") for a in addresses}
            merged.setdefault((broker, server), set()).update(points)
    return tuple(DirectoryServer(broker, server, tuple(sorted(points)))
                 for (broker, server), points in sorted(merged.items()))


def search_brokers(query: str) -> tuple[DirectoryServer, ...]:
    """Name-only HTTPS lookup, bounded size and socket timeout; no implicit retry.

    Production callers still need overall request deadlines, caching, rate limits
    and a provider agreement. The public demonstration host is not an SLA.
    """
    query = _text(query, 80, "INVALID_QUERY")
    if len(query) < 2:
        raise DirectoryError("QUERY_TOO_SHORT")
    url = SEARCH_URL + "?" + urlencode({"company": query})
    try:
        with urlopen(url, timeout=10) as response:
            if response.status != 200:
                raise DirectoryError("PROVIDER_ERROR")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        code = "PROVIDER_RATE_LIMITED" if error.code == 429 else "PROVIDER_HTTP_ERROR"
        error.close()
        raise DirectoryError(code) from None
    except (URLError, OSError):
        raise DirectoryError("PROVIDER_UNAVAILABLE") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise DirectoryError("PROVIDER_RESPONSE_TOO_LARGE")
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise DirectoryError("INVALID_PROVIDER_RESPONSE") from None
    return parse_directory(payload)
