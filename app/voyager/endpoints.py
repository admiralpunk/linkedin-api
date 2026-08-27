"""Voyager endpoint URL builders.

These paths mirror what the linkedin.com web client calls. They are undocumented
and may change without notice (see README known limitations).
"""

from __future__ import annotations

from urllib.parse import quote

BASE = "https://www.linkedin.com/voyager/api"


def graphql(query_id: str, variables: str) -> str:
    """Build a Voyager GraphQL URL.

    ``variables`` uses LinkedIn's Rest.li tuple syntax, e.g. ``(vanityName:williamhgates)``.
    It is passed through verbatim except for URL-encoding of reserved characters; the
    parentheses/colons that Rest.li relies on are preserved.
    """
    encoded = quote(variables, safe="(),:.@-_")
    return f"{BASE}/graphql?includeWebMetadata=true&variables={encoded}&queryId={query_id}"


def profile_variables(public_id: str) -> str:
    """Default variables tuple for the identity profile query (keyed by vanity name)."""
    return f"(vanityName:{public_id})"


def profile_view(public_id: str) -> str:
    """The big one: profile + positions + education + skills + certs + languages."""
    return f"{BASE}/identity/profiles/{public_id}/profileView"


def profile_basic(public_id: str) -> str:
    return f"{BASE}/identity/profiles/{public_id}"


def profile_skills(public_id: str, count: int = 100) -> str:
    return f"{BASE}/identity/profiles/{public_id}/skills?count={count}&start=0"


def profile_contact_info(public_id: str) -> str:
    return f"{BASE}/identity/profiles/{public_id}/profileContactInfo"
