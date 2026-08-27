#!/usr/bin/env python3
"""
linkedin_profile.py — educational study of LinkedIn's internal (Voyager) API.

Takes YOUR OWN session cookie (li_at + JSESSIONID) plus a profile URL, then:
  1. resolves the vanity slug -> profile URN (via the logged-in profile page)
  2. calls the Voyager GraphQL endpoint to fetch the profile
  3. flattens the "normalized" JSON response into a readable dict

FOR EDUCATIONAL / PERSONAL USE ONLY.
Automating access — even to your own account — violates LinkedIn's Terms of
Service and can get the account restricted or banned. Keep volume tiny, run
serially, and stop if you see HTTP 999 (bot-detection / rate limit).

Usage:
    python linkedin_profile.py "https://www.linkedin.com/in/some-slug/"

Credentials are read from environment variables (never hard-code them):
    LI_AT          value of the li_at cookie
    LI_JSESSIONID  value of the JSESSIONID cookie, e.g.  ajax:8399673192947890215

PowerShell (this session):
    $env:LI_AT = "AQEDAS..."
    $env:LI_JSESSIONID = "ajax:8399673192947890215"
    python linkedin_profile.py "https://www.linkedin.com/in/some-slug/"
"""

import json
import os
import re
import sys
import time
from urllib.parse import quote, urlparse

import requests

# The persisted-query hash observed in captured browser traffic. LinkedIn
# rotates these; if a request starts returning HTTP 400, open DevTools ->
# Network on a real profile view and copy the fresh queryId here.
PROFILE_QUERY_ID = "voyagerIdentityDashProfiles.b5c27c04968c409fc0ed3546575b9b7a"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)

BASE = "https://www.linkedin.com"


class AuthWallError(RuntimeError):
    """Raised when LinkedIn returns the logged-out authwall stub."""


class LinkedInStudy:
    def __init__(self, li_at: str, jsessionid: str):
        if not li_at or not jsessionid:
            raise ValueError("li_at and JSESSIONID are both required")

        # csrf-token header MUST byte-match the JSESSIONID cookie value,
        # including the leading "ajax:". This is LinkedIn's double-submit CSRF.
        self.csrf = jsessionid

        self.session = requests.Session()
        self.session.cookies.set("li_at", li_at, domain=".linkedin.com")
        self.session.cookies.set("JSESSIONID", f'"{jsessionid}"', domain=".linkedin.com")
        self.session.headers.update(
            {
                "user-agent": USER_AGENT,
                "accept-language": "en-US,en;q=0.9",
            }
        )

    # ---- Step 1: slug -> URN ------------------------------------------------
    def slug_from_url(self, url: str) -> str:
        """Extract the /in/<slug>/ portion from a profile URL."""
        path = urlparse(url).path
        m = re.search(r"/in/([^/?#]+)", path)
        if not m:
            raise ValueError(f"Not a /in/ profile URL: {url}")
        return m.group(1)

    def resolve_urn(self, slug: str) -> str:
        """
        Fetch the logged-in profile page and scrape the fsd_profile URN out of
        the embedded bootstrap JSON. Logged-out this page is just the authwall
        redirect stub, so a valid li_at cookie is mandatory.
        """
        url = f"{BASE}/in/{slug}/"
        r = self.session.get(url, timeout=30)
        self._guard(r)

        html = r.text
        if "window.location.href" in html and "authwall" in html:
            raise AuthWallError(
                "Got the authwall stub — li_at cookie missing/expired."
            )

        # The URN appears in the embedded state as urn:li:fsd_profile:ACoAA...
        m = re.search(r"urn:li:fsd_profile:([A-Za-z0-9_-]+)", html)
        if not m:
            raise RuntimeError(
                "Could not find fsd_profile URN in page HTML. LinkedIn may have "
                "changed the bootstrap format, or the profile is not accessible."
            )
        return m.group(1)

    # ---- Step 2: URN -> profile data ---------------------------------------
    def fetch_profile(self, profile_id: str) -> dict:
        """
        Call the Voyager GraphQL persisted query. `variables` uses LinkedIn's
        RESTli tuple encoding — (key:value) — NOT JSON. URN colons must be
        percent-encoded.
        """
        variables = f"(memberIdentity:{profile_id})"
        url = (
            f"{BASE}/voyager/api/graphql"
            f"?includeWebMetadata=true"
            f"&variables={quote(variables, safe='():,')}"
            f"&queryId={PROFILE_QUERY_ID}"
        )
        headers = {
            "csrf-token": self.csrf,
            "accept": "application/vnd.linkedin.normalized+json+2.1",
            "x-restli-protocol-version": "2.0.0",
            "x-li-lang": "en_US",
        }
        r = self.session.get(url, headers=headers, timeout=30)
        self._guard(r)
        return r.json()

    # ---- Generic GraphQL replay (the durable method) ------------------------
    def graphql(self, query_id: str, variables: str) -> dict:
        """
        Replay ANY Voyager GraphQL persisted query you captured from DevTools.

        `query_id`  : the full  namespace.sha256  string from the Network tab
        `variables` : RESTli-tuple string, e.g.  (vanityName:some-slug)
                      or  (profileUrn:urn:li:fsd_profile:ACoAA...)

        This is the workflow that survives LinkedIn's changes: the endpoint is
        stable, only the hash + variable shape rotate, and you re-capture those.
        """
        url = (
            f"{BASE}/voyager/api/graphql"
            f"?includeWebMetadata=true"
            f"&variables={quote(variables, safe='():,')}"
            f"&queryId={query_id}"
        )
        headers = {
            "csrf-token": self.csrf,
            "accept": "application/vnd.linkedin.normalized+json+2.1",
            "x-restli-protocol-version": "2.0.0",
            "x-li-lang": "en_US",
        }
        r = self.session.get(url, headers=headers, timeout=30)
        self._guard(r)
        return r.json()

    # ---- Richer fetch: legacy profileView (RETIRED — returns 410) -----------
    def fetch_profile_view(self, slug: str) -> dict:
        """
        The legacy `profileView` endpoint. Unlike the thin dash persisted query,
        this returns a fully DECORATED object (data inline, not normalized):
        positions, educations, skills, languages, etc. all in one response.
        Keyed by the public slug, not the URN.
        """
        url = f"{BASE}/voyager/api/identity/profiles/{quote(slug)}/profileView"
        headers = {
            "csrf-token": self.csrf,
            "accept": "application/json",
            "x-restli-protocol-version": "2.0.0",
            "x-li-lang": "en_US",
        }
        r = self.session.get(url, headers=headers, timeout=30)
        self._guard(r)
        return r.json()

    @staticmethod
    def summarize_profile_view(data: dict) -> dict:
        """
        Walk the decorated profileView graph and pull the common sections into
        clean nested JSON. Each section lives under a *View key with an
        `elements` list — this is the stitch pattern worth learning.
        """
        def date(tp: dict) -> str:
            def one(d):
                if not d:
                    return ""
                return f"{d.get('month', '')}/{d.get('year', '')}".strip("/")
            start = one(tp.get("startDate"))
            end = one(tp.get("endDate")) or "Present"
            return f"{start} - {end}".strip(" -") if start else end

        prof = data.get("profile", {}) or {}
        out = {
            "name": " ".join(x for x in (prof.get("firstName"), prof.get("lastName")) if x),
            "headline": prof.get("headline"),
            "location": prof.get("locationName") or prof.get("geoCountryName"),
            "industry": prof.get("industryName"),
            "summary": prof.get("summary"),
            "positions": [],
            "educations": [],
            "skills": [],
            "languages": [],
        }

        for e in (data.get("positionView", {}) or {}).get("elements", []):
            tp = e.get("timePeriod", {}) or {}
            out["positions"].append(
                {
                    "title": e.get("title"),
                    "company": e.get("companyName"),
                    "location": e.get("locationName"),
                    "dates": date(tp),
                    "description": e.get("description"),
                }
            )

        for e in (data.get("educationView", {}) or {}).get("elements", []):
            tp = e.get("timePeriod", {}) or {}
            out["educations"].append(
                {
                    "school": e.get("schoolName"),
                    "degree": e.get("degreeName"),
                    "field": e.get("fieldOfStudy"),
                    "dates": date(tp),
                }
            )

        out["skills"] = [
            e.get("name") for e in (data.get("skillView", {}) or {}).get("elements", [])
        ]
        out["languages"] = [
            {"name": e.get("name"), "proficiency": e.get("proficiency")}
            for e in (data.get("languageView", {}) or {}).get("elements", [])
        ]
        return out

    # ---- Step 3: flatten normalized JSON ------------------------------------
    @staticmethod
    def index_included(payload: dict) -> dict:
        """
        LinkedIn returns a "normalized" graph: a flat `included` array of
        entities each keyed by its own entityUrn. Build an urn -> entity map so
        references can be rejoined.
        """
        included = payload.get("included", [])
        return {e["entityUrn"]: e for e in included if "entityUrn" in e}

    @staticmethod
    def summarize(payload: dict) -> dict:
        """
        Pull a few common fields out of the normalized graph. This is
        deliberately shallow — the point is to show HOW the graph is stitched,
        not to fully model LinkedIn's schema (which changes constantly).
        """
        out = {"name": None, "headline": None, "location": None, "raw_entities": 0}
        for e in payload.get("included", []):
            t = e.get("$type", "")
            if t.endswith("identity.profile.Profile") or "firstName" in e:
                first = e.get("firstName")
                last = e.get("lastName")
                if first or last:
                    out["name"] = " ".join(x for x in (first, last) if x)
                out["headline"] = e.get("headline") or out["headline"]
                loc = e.get("geoLocation") or e.get("locationName")
                if isinstance(loc, str):
                    out["location"] = loc
        out["raw_entities"] = len(payload.get("included", []))
        return out

    # ---- shared response guard ---------------------------------------------
    @staticmethod
    def _guard(r: requests.Response) -> None:
        if r.status_code == 999:
            raise RuntimeError(
                "HTTP 999 — LinkedIn bot-detection / rate limit. Stop and back off."
            )
        if r.status_code == 403:
            raise RuntimeError(
                "HTTP 403 — csrf-token header likely does not match JSESSIONID."
            )
        if r.status_code == 401:
            raise AuthWallError("HTTP 401 — session cookie invalid or expired.")
        if r.status_code == 400:
            raise RuntimeError(
                "HTTP 400 — queryId hash or variables shape stale. Re-capture from DevTools."
            )
        if r.status_code == 410:
            raise RuntimeError(
                "HTTP 410 Gone — this endpoint has been retired by LinkedIn. "
                "Use --graphql with a queryId captured from your browser instead."
            )
        r.raise_for_status()


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    li_at = os.environ.get("LI_AT", "").strip()
    jsessionid = os.environ.get("LI_JSESSIONID", "").strip()
    if not li_at or not jsessionid:
        print("ERROR: set LI_AT and LI_JSESSIONID environment variables first.")
        print("See the docstring at the top of this file for how.")
        return 2

    # crude flag parse: bare tokens are positional, --k=v tokens are options
    positional, opts = [], {}
    for a in sys.argv[1:]:
        if a.startswith("--"):
            k, _, v = a[2:].partition("=")
            opts[k] = v if v else True
        else:
            positional.append(a)
    url = positional[0]
    full = opts.get("full") is True

    client = LinkedInStudy(li_at, jsessionid)
    here = os.path.dirname(os.path.abspath(__file__))

    slug = client.slug_from_url(url)
    print(f"[1/3] slug         = {slug}")

    # --- generic replay mode: python ... --graphql=NAMESPACE.HASH [--var=(...)]
    if "graphql" in opts:
        query_id = opts["graphql"]
        variables = opts.get("var") or f"(vanityName:{slug})"
        print(f"[2/3] queryId      = {query_id}")
        print(f"      variables    = {variables}")
        time.sleep(1.0)
        data = client.graphql(query_id, variables)
        inc = data.get("included", [])
        from collections import Counter

        types = Counter(e.get("$type", "?") for e in inc)
        print(f"[3/3] {len(inc)} entities:")
        for t, n in types.most_common():
            print(f"        {n:3}  {t}")
        raw = os.path.join(here, "graphql_raw.json")
        with open(raw, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"\nraw -> {raw}")
        return 0

    if full:
        # profileView is keyed by slug directly — no URN hop needed.
        print("[2/3] mode         = --full (legacy profileView, decorated)")
        time.sleep(1.0)
        data = client.fetch_profile_view(slug)
        summary = client.summarize_profile_view(data)
        raw_name, out_name = "profileview_raw.json", "profile_full.json"
        print(
            f"[3/3] positions={len(summary['positions'])} "
            f"educations={len(summary['educations'])} "
            f"skills={len(summary['skills'])}"
        )
    else:
        urn = client.resolve_urn(slug)
        print(f"[2/3] profile URN  = {urn}")
        time.sleep(1.0)  # human pace between requests
        data = client.fetch_profile(urn)
        summary = client.summarize(data)
        raw_name, out_name = "profile_raw.json", "profile_summary.json"
        print(f"[3/3] fetched {summary['raw_entities']} entities")

    print()
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    with open(os.path.join(here, raw_name), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    with open(os.path.join(here, out_name), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"\nraw   -> {os.path.join(here, raw_name)}")
    print(f"clean -> {os.path.join(here, out_name)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AuthWallError as e:
        print(f"AUTHWALL: {e}")
        sys.exit(1)
    except Exception as e:  # noqa: BLE001 - study tool, surface the message plainly
        print(f"ERROR: {e}")
        sys.exit(1)
