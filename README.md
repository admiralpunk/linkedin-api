# LinkedIn Profile API

A hosted HTTPS API that accepts a **LinkedIn profile URL** and returns most of the information on the
profile page as **structured JSON** — name, headline, location, about, experience, education, skills,
certifications, languages, and profile images.

It works by talking to LinkedIn's own internal **Voyager API** (the private REST backend that powers
`linkedin.com`) using an authenticated session cookie, rather than scraping rendered HTML. Authenticated
Voyager requests return structured JSON, which is far more robust than parsing the DOM.

> ⚠️ **Educational / take-home project.** Automated access to LinkedIn violates their Terms of Service and
> can get an account restricted. Use a throwaway account, keep volume low, and do not use this in production
> against LinkedIn without permission. See [Known limitations](#known-limitations).

---

## Table of contents
- [Architecture](#architecture)
- [Quick start (local)](#quick-start-local)
- [Getting your LinkedIn cookies](#getting-your-linkedin-cookies)
- [API documentation](#api-documentation)
- [Deployment (AWS EC2 + HTTPS)](#deployment-aws-ec2--https)
- [Approach](#approach)
- [Known limitations](#known-limitations)
- [Project layout](#project-layout)
- [Legal & ethics](#legal--ethics)

---

## Architecture

```
client ──HTTPS──> Caddy (TLS) ──> FastAPI app ──> Voyager client ──> linkedin.com/voyager/api
                                        │
                                        ├── URL → public_id (utils)
                                        ├── TTL cache (per public_id)
                                        └── parser: nested Voyager JSON → flat schema
```

- **FastAPI** — async web layer, automatic OpenAPI/Swagger docs at `/docs`.
- **Voyager client** (`app/voyager/client.py`) — sends the `li_at` + `JSESSIONID` cookies and the matching
  `csrf-token` header, maps upstream failures to typed errors.
- **Parser** (`app/voyager/parser.py`) — defensively flattens the nested `profileView` response.
- **Cache** (`app/cache.py`) — in-memory TTL cache keyed by profile slug to limit calls to LinkedIn.
- **Rate limiting** (`slowapi`) + randomized request jitter to reduce the chance of being flagged.

---

## Quick start (local)

Requires Python 3.12+ (tested on 3.12 and 3.14).

```bash
# 1. install deps
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. configure secrets
cp .env.example .env               # then edit .env (see next section)

# 3. run
uvicorn app.main:app --reload
```

- API: `http://localhost:8000/api/v1/profile?url=...`
- Interactive docs: `http://localhost:8000/docs`
- Health: `http://localhost:8000/health`

Run the tests (no network / cookies needed — they run against a fixture):

```bash
pytest -q
```

---

## Getting your LinkedIn cookies

The backend authenticates as **you** using two cookies from a logged-in browser session:

1. Log in to `https://www.linkedin.com` in Chrome/Firefox.
2. Open **DevTools → Application (Storage) → Cookies → https://www.linkedin.com**.
3. Copy these two values into `.env`:
   - **`li_at`** → `LI_AT`
   - **`JSESSIONID`** → `JSESSIONID` (keep the quotes and `ajax:` prefix, e.g. `"ajax:1234..."`)

The service automatically derives the required `csrf-token` header from `JSESSIONID` (they must match).

`.env` example:

```dotenv
LI_AT=AQEDA...verylongtoken...
JSESSIONID="ajax:1234567890123456789"
API_KEY=choose-a-long-random-string
CACHE_TTL=43200
RATE_LIMIT_PER_MINUTE=10
```

> Cookies expire (typically weeks to a few months) or when you log out. When the API starts returning
> `503 LinkedIn session expired`, refresh these two values.

### Capturing the GraphQL `queryId` (required)

LinkedIn retired the old REST profile endpoint (it now returns **HTTP 410**), so the service calls the
**Voyager GraphQL** API. Each GraphQL query is identified by a version-pinned `queryId` hash that changes on
LinkedIn deploys, so it can't be hard-coded — you capture the current one from your browser:

1. Logged in, open any profile (e.g. `https://www.linkedin.com/in/williamhgates/`).
2. DevTools → **Network** → filter box: `graphql`.
3. Find the profile request(s) — the `queryId` contains `Profile`, e.g.
   `voyagerIdentityDashProfiles.<hash>` (top card) and often
   `voyagerIdentityDashProfileComponents.<hash>` (the experience/education/skills cards).
4. Put the hashes in `.env`:
   ```dotenv
   GQL_PROFILE_QUERY_ID=voyagerIdentityDashProfiles.abcdef...
   GQL_CARDS_QUERY_ID=voyagerIdentityDashProfileComponents.123456...   # optional
   ```
5. (Recommended) also copy the exact `x-li-track` and `x-li-page-instance` **request headers** from that same
   request into `LI_TRACK` / `LI_PAGE_INSTANCE` — some queries reject requests without a matching track header.

When the API returns `502 ... queryId is stale`, re-capture (LinkedIn shipped a new build).

---

## API documentation

Base URL (local): `http://localhost:8000`

Authentication: if `API_KEY` is set, every `/api/v1/*` request must send header **`X-API-Key: <key>`**.
If `API_KEY` is blank, auth is disabled (fine for local dev, **not** for a public deployment).

### `GET /health`
Liveness + whether cookies are configured.
```json
{ "status": "ok", "cookies_present": true, "version": "1.0.0" }
```

### `GET /api/v1/profile?url=<linkedin_profile_url>`
### `POST /api/v1/profile`  body: `{ "url": "<linkedin_profile_url>" }`

Accepts any LinkedIn profile URL form: `/in/<slug>`, legacy `/pub/<slug>`, locale/mobile subdomains,
trailing slashes, and query strings.

**Example**
```bash
curl "https://<your-domain>/api/v1/profile?url=https://www.linkedin.com/in/williamhgates/" \
     -H "X-API-Key: your-key"
```

**Success `200`**
```json
{
  "public_id": "williamhgates",
  "profile_url": "https://www.linkedin.com/in/williamhgates/",
  "name": { "first": "Bill", "last": "Gates", "full": "Bill Gates" },
  "headline": "Co-chair, Bill & Melinda Gates Foundation",
  "location": "Seattle, Washington, United States",
  "industry": "Philanthropy",
  "about": "Co-founder of Microsoft...",
  "experience": [
    {
      "title": "Co-chair",
      "company": "Bill & Melinda Gates Foundation",
      "company_url": "https://www.linkedin.com/company/1link/",
      "location": "Seattle, WA",
      "start": "2000-01",
      "end": null,
      "description": "..."
    }
  ],
  "education": [
    { "school": "Harvard University", "degree": null, "field_of_study": null, "start": "1973", "end": "1975" }
  ],
  "skills": ["Philanthropy", "Software"],
  "certifications": [
    { "name": "...", "authority": "...", "license_number": null, "url": null, "start": null, "end": null }
  ],
  "languages": [ { "name": "English", "proficiency": "NATIVE_OR_BILINGUAL" } ],
  "images": {
    "profile_picture_url": "https://media.licdn.com/.../400_400/pic.jpg",
    "background_url": "https://media.licdn.com/.../1584/bg.jpg"
  },
  "fetched_at": "2026-08-27T10:00:00+00:00",
  "cached": false
}
```
Any field not visible to your account (privacy, connection degree) comes back `null` or as an empty list.
`cached` is `true` when the result was served from the TTL cache.

**Error responses** (body: `{ "detail": "..." }`)

| Status | Meaning |
|--------|---------|
| `400`  | URL is missing/not a LinkedIn profile URL |
| `401`  | Missing/invalid `X-API-Key` |
| `404`  | Profile not found or not visible to the backend account |
| `429`  | Client rate limit exceeded, or LinkedIn is rate-limiting us |
| `502`  | Upstream (Voyager) failure, or `410` = the captured `queryId` is stale (re-capture) |
| `503`  | Backend LinkedIn cookie expired — needs refreshing |

Full interactive schema is always available at **`/docs`** (Swagger) and **`/redoc`**.

---

## Deployment (AWS EC2 + HTTPS)

The simplest path uses Docker Compose with **Caddy**, which provisions and renews a Let's Encrypt
certificate automatically.

1. **Launch** an Ubuntu EC2 instance, attach an **Elastic IP**, and in the security group open
   **80** and **443** to the world and **22** to your IP only.
2. **DNS:** point a domain's `A` record at the Elastic IP (a valid TLS cert requires a domain).
3. **Install** Docker + Docker Compose plugin on the instance.
4. **Clone** the repo and create secrets — the real `.env` never goes in git:
   ```bash
   git clone https://github.com/<you>/linkedin-profile-api.git
   cd linkedin-profile-api
   cp .env.example .env        # fill in LI_AT, JSESSIONID, API_KEY
   export DOMAIN=api.example.com
   ```
5. **Run:**
   ```bash
   docker compose -f deploy/docker-compose.yml up -d --build
   ```
6. **Verify:** `curl https://api.example.com/health`

**Alternative (no Docker):** use `deploy/linkedin-api.service` (systemd + gunicorn/uvicorn on
`127.0.0.1:8000`) and front it with Caddy or nginx for TLS. Store secrets in a root-only
`EnvironmentFile` (`/etc/linkedin-api.env`) or **AWS SSM Parameter Store** — never in the repo.

---

## Approach

1. **Reverse engineering.** LinkedIn's web app fetches profile data from the **Voyager GraphQL** API,
   `https://www.linkedin.com/voyager/api/graphql?queryId=<hash>&variables=(vanityName:<slug>)`. Authenticated
   with a session cookie, it returns a *normalized* document (`{data, included:[...]}`) where every entity
   (`Profile`, `Position`, `Education`, `Skill`, `Certification`, `Language`) is a typed item in the
   `included` array, cross-referenced by URN. (The older single-call REST endpoint
   `identity/profiles/{id}/profileView` now returns HTTP 410 and is retained only as a fallback/reference.)
2. **Authentication.** Requests carry the `li_at` and `JSESSIONID` cookies and a `csrf-token` header equal
   to the `JSESSIONID` value, plus `x-restli-protocol-version: 2.0.0` and a realistic browser `User-Agent`.
3. **URL handling.** `extract_public_id()` pulls the vanity slug from any profile URL variant.
4. **Parsing.** `parse_profile_view()` flattens the nested response into a stable, documented schema.
   Every field access is defensive, so a renamed/missing field degrades to `null` instead of erroring.
5. **Resilience & politeness.** Per-profile TTL cache, per-IP rate limiting, randomized jitter between
   upstream calls, and detection of login redirects (`→ 503`) so an expired cookie is obvious.
6. **Security.** Optional `X-API-Key` gate protects the public endpoint (and thus your cookie/quota); all
   secrets come from the environment and are excluded from git.

### Why this stack
- **Python + FastAPI** — the richest ecosystem for the Voyager approach and free OpenAPI docs.
- **Own Voyager client** rather than a third-party library: the popular `tomquirk/linkedin-api` went
  private, so a small, self-contained client we control is more maintainable and dependency-light.

---

## Known limitations

- **Undocumented, unstable API.** Voyager is private and is migrating toward GraphQL; the `profileView`
  endpoint or its field names can change without notice. The parser fails soft, but a major change may
  require updating `endpoints.py` / `parser.py`.
- **Cookie lifetime.** Access depends on a personal `li_at` cookie that expires and must be refreshed
  manually. There is no automated login (which would hit captcha/2FA).
- **Visibility-limited data.** You only get what the backend account can see. Connection degree and the
  target's privacy settings determine which fields are populated.
- **Rate limits & bans.** LinkedIn detects and throttles automation (HTTP `429`/`999`) and may restrict the
  account. Caching, rate limiting, and jitter reduce but do not eliminate this risk.
- **Datacenter IP.** EC2 IPs are more likely to be flagged than residential ones; heavy use may need a
  proxy. This project does not bundle one.
- **No captcha/2FA/challenge handling**, and **profile-only** (no company/job/search endpoints).
- **Terms of Service.** This violates LinkedIn's ToS and is provided for educational purposes only.

---

## Project layout

```
app/
  main.py            FastAPI app, routes, error mapping, rate limiting
  config.py          env-based settings (cookies, api key, TTL)
  schemas.py         Pydantic response models (the JSON contract)
  security.py        X-API-Key dependency
  cache.py           TTL cache
  services.py        URL → fetch → parse → cache orchestration
  utils.py           URL/URN parsing helpers
  voyager/
    client.py        authenticated Voyager HTTP client + typed errors
    endpoints.py     Voyager URL builders
    parser.py        nested Voyager JSON → flat schema
tests/               URL parsing + parser tests (offline, fixture-based)
deploy/              Dockerfile, docker-compose (+ Caddy), systemd unit
```

---

## Legal & ethics

This project accesses LinkedIn in a way that is **not authorized by LinkedIn** and violates their User
Agreement. It exists to demonstrate reverse-engineering and API design for a take-home challenge. Only
scrape data you are permitted to access, respect people's privacy, use a disposable account, keep request
volume minimal, and do not redistribute scraped personal data. You are responsible for how you use it.
