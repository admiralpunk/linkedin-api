"""HTTP client that talks to LinkedIn's Voyager API using session cookies."""

from __future__ import annotations

import asyncio
import random

import httpx

from ..config import Settings

# A realistic desktop Chrome UA reduces the chance of being flagged.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


class VoyagerError(Exception):
    """Generic upstream failure talking to Voyager."""


class CookieExpiredError(VoyagerError):
    """Session cookie is invalid/expired — LinkedIn redirected us to login."""


class ProfileNotFoundError(VoyagerError):
    """Profile does not exist or is not visible to the logged-in account."""


class RateLimitedError(VoyagerError):
    """LinkedIn is rate limiting us (HTTP 429 / 999)."""


class EndpointGoneError(VoyagerError):
    """HTTP 410 — the endpoint/queryId is retired. The captured queryId is stale."""


class VoyagerClient:
    """Thin async wrapper around the Voyager REST endpoints.

    One client is shared for the app lifetime (connection reuse). All requests
    carry the li_at + JSESSIONID cookies and the matching csrf-token header.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0),
            follow_redirects=False,  # a redirect to /login means the cookie died
            headers=self._base_headers(),
            cookies=self._cookies(),
        )

    def _cookies(self) -> dict[str, str]:
        return {
            "li_at": self._settings.li_at,
            "JSESSIONID": self._settings.jsessionid.strip('"'),
        }

    def _base_headers(self) -> dict[str, str]:
        # Header set mirrors what voyager-web sends; several of these (x-li-track,
        # x-li-page-instance) are required by the GraphQL endpoints or requests 4xx.
        return {
            "User-Agent": _USER_AGENT,
            "Accept": "application/vnd.linkedin.normalized+json+2.1",
            "Accept-Language": "en-US,en;q=0.9",
            "x-restli-protocol-version": "2.0.0",
            "x-li-lang": "en_US",
            "x-li-track": self._settings.effective_li_track,
            "x-li-page-instance": self._settings.effective_li_page_instance,
            "csrf-token": self._settings.csrf_token,
            "Referer": "https://www.linkedin.com/",
        }

    async def get_graphql(self, url: str) -> dict:
        """GET a Voyager GraphQL URL and return the normalized JSON body.

        The endpoint accepts a prebuilt URL (see ``endpoints.graphql``) carrying the
        ``queryId`` and Rest.li ``variables`` tuple. A per-request Referer pointing at a
        profile page keeps the request consistent with browser behavior.
        """
        return await self.get_json(url)

    async def get_json(self, url: str) -> dict:
        """GET a Voyager URL and return parsed JSON, mapping failures to typed errors."""
        if not self._settings.cookies_present:
            raise CookieExpiredError("No LinkedIn cookies configured.")

        # Small randomized delay to look less like a scripted burst.
        await asyncio.sleep(random.uniform(0.2, 0.8))

        try:
            resp = await self._client.get(url)
        except httpx.RequestError as exc:  # network-level failure
            raise VoyagerError(f"Network error contacting LinkedIn: {exc}") from exc

        return self._handle(resp)

    def _handle(self, resp: httpx.Response) -> dict:
        status = resp.status_code

        if status in (301, 302, 303, 307, 308):
            location = resp.headers.get("location", "")
            if "login" in location or "authwall" in location or "uas/login" in location:
                raise CookieExpiredError("Redirected to login — cookie expired.")
            raise VoyagerError(f"Unexpected redirect to {location!r}.")

        if status == 200:
            try:
                return resp.json()
            except ValueError as exc:
                raise VoyagerError("Voyager returned non-JSON body.") from exc

        if status in (401, 403):
            raise CookieExpiredError(
                f"LinkedIn rejected the session ({status}). Cookie likely expired."
            )
        if status == 404:
            raise ProfileNotFoundError("Profile not found or not accessible.")
        if status == 410:
            raise EndpointGoneError(
                "LinkedIn returned 410 Gone — the captured queryId/endpoint is stale. "
                "Re-capture the current queryId from a browser session (see README)."
            )
        if status in (429, 999):
            raise RateLimitedError("LinkedIn is rate limiting requests.")

        raise VoyagerError(f"Voyager returned HTTP {status}.")

    async def aclose(self) -> None:
        await self._client.aclose()
