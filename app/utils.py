"""URL / URN helpers for LinkedIn identifiers."""

from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

# Accepts: /in/<slug>, /pub/<slug>, with or without www/locale sub-hosts,
# trailing slashes, query strings, and mobile (m.linkedin.com) hosts.
_PROFILE_PATH_RE = re.compile(r"/(?:in|pub)/([^/?#]+)", re.IGNORECASE)


class InvalidProfileURL(ValueError):
    """Raised when the input is not a recognizable LinkedIn profile URL."""


def extract_public_id(url: str) -> str:
    """Extract the vanity slug (public_id) from any LinkedIn profile URL form.

    >>> extract_public_id("https://www.linkedin.com/in/williamhgates/")
    'williamhgates'
    """
    if not url or not isinstance(url, str):
        raise InvalidProfileURL("URL is empty.")

    candidate = url.strip()
    # Allow bare slugs and scheme-less URLs.
    if "//" not in candidate and "linkedin.com" not in candidate.lower():
        if "/" not in candidate and candidate:
            return _clean_slug(candidate)
        candidate = "https://" + candidate

    parsed = urlparse(candidate if "//" in candidate else "https://" + candidate)
    host = (parsed.netloc or "").lower()
    if "linkedin.com" not in host:
        raise InvalidProfileURL("Not a linkedin.com URL.")

    match = _PROFILE_PATH_RE.search(parsed.path)
    if not match:
        raise InvalidProfileURL(
            "URL is not a profile URL (expected /in/<slug> or /pub/<slug>)."
        )
    return _clean_slug(match.group(1))


def _clean_slug(slug: str) -> str:
    slug = unquote(slug).strip().strip("/")
    if not slug:
        raise InvalidProfileURL("Empty profile slug.")
    return slug


def canonical_profile_url(public_id: str) -> str:
    return f"https://www.linkedin.com/in/{public_id}/"


def urn_id(urn: str | None) -> str | None:
    """Return the trailing id of a LinkedIn URN, e.g. urn:li:fs_position:(x,y) -> y-ish.

    For simple URNs like ``urn:li:fs_miniProfile:ABC`` returns ``ABC``.
    """
    if not urn or not isinstance(urn, str):
        return None
    return urn.rsplit(":", 1)[-1]
