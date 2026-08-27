# Reverse-Engineering LinkedIn's Profile Delivery

How this project recovers a structured profile (name, headline, location, about,
experience, education, …) from a `linkedin.com/in/<slug>/` URL — and how we
figured out the moving parts.

> **Scope & ethics.** This is an educational teardown of traffic captured from
> our own authenticated browser session. Automating access to LinkedIn — even
> your own account — violates their Terms of Service and can get the account
> restricted. Keep any live use tiny, serial, and cookie-refreshed by hand.
> There is no official API that turns an arbitrary profile URL into this data;
> the sanctioned routes are consent-based OAuth (Sign In with LinkedIn) or the
> gated Marketing/Talent partner programs.

---

## 1. The three systems behind a profile page

Capturing the network traffic for a single profile view showed requests to
three distinct backends, not one:

| System | URL shape | Role |
|---|---|---|
| **Media CDN** | `media.licdn.com/dms/image/v2/…` | Pre-signed image URLs (photos, logos). No auth; expiring HMAC signature in the URL (`e=`, `t=`, `v=beta`). Not queryable. |
| **Voyager** | `linkedin.com/voyager/api/graphql?queryId=…&variables=…` | Legacy internal GraphQL-over-Rest.li. Still powers messaging, nav, and an *identity resolve*, but the rich profile projection is gone. |
| **SDUI / RSC** | `linkedin.com/flagship-web/rsc-action/actions/component?componentId=…` | The current profile stack: Server-Driven-UI delivered as React Server Component (Flight) streams. **This is where profile data now lives.** |

Two client versions ship on the same page — Voyager `voyager-web 1.13.x` and
SDUI `web 0.2.x` — evidence of a live migration from the former to the latter.

---

## 2. Dead ends (worth knowing)

- **Logged-out fetch → authwall.** A bare `curl` of `/in/<slug>/` returns a JS
  stub that redirects to `/authwall`. No profile data reaches an unauthenticated
  client. `curl` doesn't run the JS, so you just see the redirect script.
- **Legacy REST is retired.** `GET /voyager/api/identity/profiles/<slug>/profileView`
  now returns **`410 Gone`**. The clean, one-call profile blob that older
  unofficial libraries relied on no longer exists.
- **The surviving Voyager GraphQL profile query is nearly empty.**
  `voyagerIdentityDashProfiles.<hash>` with `variables=(memberIdentity:<urn>)`
  returns a single `Profile` entity carrying only `entityUrn` + `versionTag`.
  The `$recipeTypes` projection is minimal — it's an identity resolve, not data.

Conclusion: the real data path is the **authenticated SDUI/RSC calls**.

---

## 3. Authentication model

Every authenticated call needs, at minimum:

- Cookie **`li_at`** — the session bearer.
- Cookie **`JSESSIONID`** (value like `ajax:8399…`).
- Header **`csrf-token`** that must **byte-match** the `JSESSIONID` value
  (LinkedIn's double-submit CSRF). Mismatch → `403`.
- Header **`x-restli-protocol-version: 2.0.0`** for Voyager (or the Rest.li tuple
  encoding is rejected with `400`).

SDUI calls add tracing headers: `x-li-rsc-stream: true`, `x-li-pageforestid`,
`x-li-traceparent` / `x-li-tracestate` (W3C-style spans), `x-li-track` (a JSON
blob of screen dimensions/timezone), and `x-li-application-version`.

Bot defenses observed: `__cf_bm` (Cloudflare Bot Management, TLS/JA3
fingerprint), `dfpfpt` (device fingerprint), rotating `queryId` hashes, and
`999` responses when a client looks scripted.

---

## 4. Resolving the identity: slug → URN

The slug isn't the key the backend uses; a `urn:li:fsd_profile:<id>` is. Two
ways to get it:

1. **From the logged-in page HTML** — the rendered profile page embeds
   `urn:li:fsd_profile:…` (and `vieweeProfileId`). Grep it out.
2. **From an SDUI card's arguments** — the `profileCards*` request body carries
   `replaceableSectionArgs.vieweeProfileId`.

(Logged out, neither is available — you only get the authwall.)

---

## 5. The SDUI / RSC wire format (the core of the work)

Profile content is returned by lazy `profileCards*` component calls, e.g.
`componentId=com.linkedin.sdui.generated.profile.dsl.impl.profileCardsBelowActivityPart1`.
Each response is a **React Flight stream**, not JSON.

### 5.1 Stream structure

Rows are `\<hexid\>:\<payload\>`, one per logical value:

```
1:I["…hash…",[],"default"]          ← client module import  [id, deps, exportName]
3:I["…","","TracedComponent"]        ← wrapper component
0:["$","div",null,{ … element tree … }]   ← the model (element tree)
2:null                               ← a resolved scalar row
```

- `I[…]` rows are **client module imports** (referenced later as components).
- Model rows are React elements serialized as `["$", type, key, props]`.
- Row ids are a **hexadecimal counter** (`…, 9, a, b, …, 10, 11, 1a, 1b`).

### 5.2 Reference grammar

Inside models, `$`-prefixed strings are references, resolved against other rows:

| Token | Meaning |
|---|---|
| `$` | React element marker (slot 0 of an element array) |
| `$L<hex>` | lazy reference → load row `<hex>` |
| `$<hex>` | direct reference → value of row `<hex>` |
| `$Q<hex>` | reference to row `<hex>` as a Map |
| `$S<name>` | Symbol (e.g. `$Sreact.fragment`) |
| `$n<digits>` | BigInt |
| `$undefined` | `undefined` / null |
| `$<hex>:<path>` | navigate into row `<hex>` by a colon-separated key/index path |
| `$type`, `$case` | **NOT** Flight tokens — these are LinkedIn's protobuf discriminator keys |

`flight_parse.py` implements a complete resolver for this grammar (with a cycle
guard and path navigation), materializing the tree with all references inlined.

### 5.3 Where the data hides (the key insight)

Once resolved, human data sits as **leaves inside the element tree**, in two
distinct forms:

- **plain `children:["…"]`** → the *primary* entity fields: job title, company
  (`"Company · Full-time"`), school, degree.
- **`textProps.children:["…"]`** → secondary lines: dates
  (`"Nov 2023 - Present · 2 yrs 10 mos"`), locations, section titles,
  descriptions.

Other useful leaves: `aria-label` (company/school "… logo"), `buttonProps.text`
(`"Show all"`), `renderPayload.rootUrl` + `imageRenditions[].suffixUrl` (image
URLs), and `screen.url` (`/in/<slug>/details/experience/` — the pagination
entry points).

### 5.4 Entry boundaries

Individual experience/education entries are cleanly bounded by
`initialItems[].item` containers, and each section is identified by a
`data-testid` like `profile_ExperienceTopLevelSection_<slug>`. Grouping by these
containers (rather than guessing on date lines) yields one clean object per role
or school. Dates/locations render twice in the tree, so consecutive duplicate
strings are de-duplicated.

### 5.5 Laziness

SDUI is recursively lazy. A card often returns a *skeleton*: section slots with
`initialContent:$undefined` and a `componentKey`. The actual items (skills,
certifications, languages, recommendations) require a **second resolve call per
`componentKey`**. A single-pass fetch therefore recovers the eager sections
(about, experience, education) but leaves the lazy ones empty — that is
architectural, not a bug. Their section *markers* still appear, and their
`/details/*` links are the entry points if you later resolve them.

---

## 6. The top card comes from HTML, not an API

The logged-in profile page is **server-rendered HTML**. The top card — name,
headline, location, connections, current company/school, and the profile photo
URL — is plain DOM text there (name in an `<h2>`/`<p>`, headline and location in
following nodes, "N connections" split across sibling nodes). So the top card is
parsed with regex over the HTML, needing no extra component call. (The
`profileCardsTopComponents` call returns `500` and is intentionally skipped.)

---

## 7. Putting it together — the extraction pipeline

For a URL `linkedin.com/in/<slug>/`:

1. **GET the page** (authenticated). Detect authwall. Parse the **top card** from
   the HTML; scrape `vieweeProfileId`.
2. **POST the `profileCards*` component calls** (About/Activity, and
   BelowActivity Part1–4) with the captured `clientArguments` body shape.
3. **Resolve each Flight stream** with `flight_parse.py`.
4. **Extract**: walk each resolved tree collecting `children` / `textProps.children`
   leaves, `aria-label`, images, and detail links; group experience/education by
   the `initialItems[].item` containers into typed objects.
5. **Assemble** into one JSON document.

### Tools produced

| File | Purpose |
|---|---|
| `flight_parse.py` | Complete React Flight resolver — the wire syntax, executable. |
| `rsc_decode.py` | Lower-level chunk splitter + `componentKey` extractor (CLI, `--b64file`, `--out`). |
| `linkedin_scrape.py` | URL → structured JSON (HTML top card + Flight cards). |
| `app/` | FastAPI service wrapping the same idea over `httpx`, with typed errors, caching, and rate limiting. |

### Example result (abridged)

```json
{
  "name": "…",
  "headline": "…",
  "location": "…",
  "connections": "78",
  "about": "…",
  "experience": [
    { "title": "Painting planner",
      "company": "ADCL-FAFECO Engineering pvt ltd",
      "employment_type": "Full-time",
      "dates": "Nov 2023 - Present · 2 yrs 10 mos",
      "location": "Pune District, Maharashtra, India" }
  ],
  "education": [
    { "school": "Savitribai Phule Pune University",
      "degree": "Bachelor of Engineering - BE, Mechanical Engineering",
      "years": "2011 – 2014" }
  ]
}
```

---

## 8. Why this is brittle (operational notes)

- **`queryId` hashes rotate** per LinkedIn deploy → `400`/`410`. Re-capture from
  DevTools when a call stops returning data.
- **`componentId`s and CSS class names change** — extraction that keys on markup
  will need occasional re-capture.
- **Cookies expire/rotate.** `JSESSIONID` (CSRF) rotates first; `li_at` lasts
  longer (weeks–months) but is revoked by logout, password change, or security
  checks — faster from a server IP different from where you logged in. There is
  no fixed TTL; the only way to get new values is to copy them from a fresh
  browser login. Detection: treat authwall / `401` / `403` / `999` as
  "credentials dead" and refresh by hand.
- **Bot management** (`__cf_bm`, device fingerprints, rate limits) escalates with
  volume. Low, serial, browser-like traffic from one IP survives longest.

---

## 9. Method, in one line

Capture real authenticated traffic → separate the three backends → discard the
dead ends (authwall, `410`, empty Voyager query) → decode the RSC/Flight wire
grammar so the element tree materializes → recognize that data lives in
`children` / `textProps.children` leaves → group by structural containers → merge
with the HTML top card.
