"""Headless-browser fetch layer.

LinkedIn's profile page is Server-Driven UI (SDUI): the top card renders in the
initial HTML, but Experience / Education / Skills / About / Certifications /
Languages load lazily as protobuf-style components on scroll. Rather than
replicate that, we drive a real Chromium via Playwright: inject the session
cookies, open the profile, scroll to trigger the lazy sections, optionally open
the ``/details/*`` pages for full lists, and read the rendered DOM.

Extraction is deliberately keyed on **human-readable section headings**
("Experience", "Education", ...) rather than LinkedIn's hashed CSS class names,
so it survives their frequent class churn. Section bodies are returned as raw
innerText; ``browser_parser`` turns that into the typed schema.
"""

from __future__ import annotations

import asyncio

from playwright.async_api import (
    Browser,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from .config import Settings
from .voyager.client import CookieExpiredError, ProfileNotFoundError, VoyagerError

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# JS run in-page. Returns the page title, the top-card text, and each known
# section's innerText keyed by heading. Keys on visible headings, not classes.
_EXTRACT_JS = r"""
() => {
  const main = document.querySelector('main') || document.body;
  const secs = Array.from(main.querySelectorAll('section'));
  const out = { title: document.title, topcard: '', sections: {} };
  if (secs.length) out.topcard = (secs[0].innerText || '').trim();

  // Headings LinkedIn uses for profile sections (prefix match, case-insensitive).
  const wanted = ['About', 'Experience', 'Education', 'Licenses',
                  'Certifications', 'Skills', 'Languages', 'Projects',
                  'Honors', 'Volunteer'];
  for (const sec of secs) {
    const h = sec.querySelector('h1, h2, h3');
    if (!h) continue;
    const t = (h.innerText || '').trim();
    for (const key of wanted) {
      if (t.toLowerCase().startsWith(key.toLowerCase())) {
        // Keep the longest capture if the heading appears more than once.
        const body = (sec.innerText || '').trim();
        if (!out.sections[key] || body.length > out.sections[key].length) {
          out.sections[key] = body;
        }
      }
    }
  }
  return out;
}
"""

# A details page (e.g. /details/experience/) is one big list; grab the main text.
_DETAILS_JS = r"""
() => {
  const main = document.querySelector('main') || document.body;
  return (main.innerText || '').trim();
}
"""

_DETAILS_SECTIONS = {
    "Experience": "experience",
    "Education": "education",
    "Skills": "skills",
    "Certifications": "certifications",
    "Languages": "languages",
}


class BrowserFetcher:
    """Owns a single Chromium instance; one fresh context per request."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pw: Playwright | None = None
        self._browser: Browser | None = None

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=self._settings.headless,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )

    async def close(self) -> None:
        if self._browser:
            await self._browser.aclose()
        if self._pw:
            await self._pw.stop()

    def _cookies(self) -> list[dict]:
        return [
            {"name": "li_at", "value": self._settings.li_at, "domain": ".linkedin.com", "path": "/"},
            {
                "name": "JSESSIONID",
                "value": self._settings.jsessionid,  # keep the "ajax:..." quotes
                "domain": ".linkedin.com",
                "path": "/",
            },
        ]

    async def fetch(self, public_id: str) -> dict:
        """Render a profile and return raw extraction data for the parser.

        Returns ``{"public_id", "html", "extract": {...}, "details": {...}}``.
        Raises typed errors (CookieExpiredError / ProfileNotFoundError / VoyagerError).
        """
        if not self._settings.cookies_present:
            raise CookieExpiredError("No LinkedIn cookies configured.")
        if self._browser is None:
            raise VoyagerError("Browser not started.")

        context = await self._browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": 1440, "height": 900},
            locale="en-US",
        )
        try:
            await context.add_cookies(self._cookies())
            page = await context.new_page()
            url = f"https://www.linkedin.com/in/{public_id}/"
            try:
                await page.goto(
                    url, wait_until="domcontentloaded", timeout=self._settings.nav_timeout_ms
                )
            except PlaywrightTimeoutError as exc:
                raise VoyagerError(f"Navigation timed out for {public_id}.") from exc

            await self._guard_auth(page)

            # 404 / unavailable profile.
            if "/in/" not in page.url and "/pub/" not in page.url:
                # LinkedIn redirects unknown vanity names away from /in/.
                if "unavailable" in page.url or page.url.rstrip("/").endswith("linkedin.com"):
                    raise ProfileNotFoundError("Profile not found or not accessible.")

            await self._scroll(page)

            extract = await page.evaluate(_EXTRACT_JS)
            html = await page.content()

            details: dict[str, str] = {}
            if self._settings.fetch_details_pages:
                details = await self._fetch_details(context, public_id)

            return {"public_id": public_id, "html": html, "extract": extract, "details": details}
        finally:
            await context.close()

    async def _guard_auth(self, page) -> None:
        u = page.url.lower()
        if any(s in u for s in ("/login", "/authwall", "/checkpoint", "/uas/login")):
            raise CookieExpiredError("Redirected to login/checkpoint — cookie expired or challenged.")
        # Login/join wall sometimes renders at the profile URL.
        if await page.locator("text=/Join to view|Sign in to view|Join now/i").count():
            raise CookieExpiredError("Auth wall shown — cookie expired or insufficient access.")

    async def _scroll(self, page) -> None:
        """Scroll down in steps so SDUI lazy sections fetch and render."""
        for _ in range(self._settings.scroll_passes):
            await page.mouse.wheel(0, 2200)
            await page.wait_for_timeout(self._settings.scroll_pause_ms)
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(300)

    async def _fetch_details(self, context, public_id: str) -> dict[str, str]:
        """Visit /details/<section>/ pages for full (non-truncated) lists."""
        out: dict[str, str] = {}
        for heading, slug in _DETAILS_SECTIONS.items():
            page = await context.new_page()
            try:
                url = f"https://www.linkedin.com/in/{public_id}/details/{slug}/"
                await page.goto(
                    url, wait_until="domcontentloaded", timeout=self._settings.nav_timeout_ms
                )
                low = page.url.lower()
                if any(s in low for s in ("/login", "/authwall", "/checkpoint")):
                    continue  # section unavailable; skip quietly
                for _ in range(3):
                    await page.mouse.wheel(0, 2500)
                    await page.wait_for_timeout(self._settings.scroll_pause_ms)
                out[heading] = await page.evaluate(_DETAILS_JS)
            except PlaywrightTimeoutError:
                continue
            finally:
                await page.close()
            await asyncio.sleep(0.3)  # be polite between section pages
        return out
