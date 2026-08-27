# Reverse-Engineering LinkedIn's Profile Delivery

How this project turns a `linkedin.com/in/<slug>/` URL into structured JSON
(name, headline, experience, education, …), and what we learned getting there.


## 1. Three backends, one profile page

| System | URL shape | Role |
|---|---|---|
| **Media CDN** | `media.licdn.com/dms/image/...` | Pre-signed, expiring image URLs. No auth needed, not queryable. |
| **Voyager** | `linkedin.com/voyager/api/graphql?...` | Legacy GraphQL. Still used for nav/messaging, but the rich profile projection is gone. |
| **SDUI / RSC** | `linkedin.com/flagship-web/rsc-action/actions/component?componentId=...` | Where profile data actually lives today — a React Server Component (Flight) stream. |

Dead ends ruled out along the way: logged-out `curl` just gets an authwall
redirect stub; the old `voyager/.../profileView` REST endpoint is `410 Gone`;
the surviving Voyager profile GraphQL query returns only an entity URN, no
data. **The real data path is the authenticated SDUI/RSC calls.**

## 2. Auth requirements

- Cookies **`li_at`** (session) and **`JSESSIONID`**.
- Header **`csrf-token`** must byte-match the `JSESSIONID` value (double-submit
  CSRF) — mismatch is a `403`.
- SDUI calls add tracing headers (`x-li-rsc-stream`, `x-li-track`,
  `x-li-application-version`, etc.) copied from real browser traffic.
- Bot defenses observed: Cloudflare `__cf_bm`, device fingerprinting, and
  `999` responses when traffic looks scripted.

## 3. Slug → URN

The backend keys on `urn:li:fsd_profile:<id>`, not the slug. It's recovered
either from the logged-in page HTML (`urn:li:fsd_profile:...` is embedded
there) or from the SDUI card's request body (`vieweeProfileId`). Neither is
available logged out.

## 4. The SDUI/RSC wire format (the core problem)

Each `profileCards*` component call returns a **React Flight stream**, not
JSON — rows of `<hexid>:<payload>`, where payloads are either client-module
imports (`I[...]`) or element trees serialized as `["$", type, key, props]`.
Inside those trees, `$`-prefixed strings are references into other rows
(`$L<hex>` lazy, `$<hex>` direct, `$Q<hex>` Map, `$<hex>:<path>` navigate,
`$undefined`, etc.). `flight_parse.py` is a full resolver for this grammar.

Once resolved, the actual data sits as leaves in two shapes:
- **plain `children:["..."]`** → primary fields (job title, company, school).
- **`textProps.children:["..."]`** → secondary lines (dates, locations,
  descriptions).

Individual entries are bounded by `initialItems[].item` containers, tagged by
`data-testid` like `profile_ExperienceTopLevelSection_...` — grouping on these
(rather than guessing from date patterns) gives one clean object per role or
school.

**Laziness:** SDUI is recursively lazy. Skills, certifications, languages, and
recommendations often need a *second* per-`componentKey` resolve call, so a
single-pass fetch recovers experience/education/about but leaves those
sections thin. That's architectural, not a bug.

## 5. The top card is plain HTML

Name, headline, location, connections, and photo URL come straight from the
server-rendered page HTML (regex, no extra API call) — not from SDUI.

## 6. Pipeline

1. GET the profile page (authenticated) → parse the top card, extract
   `vieweeProfileId`.
2. POST each `profileCards*` component call.
3. Resolve each Flight stream (`flight_parse.py`).
4. Walk the resolved tree collecting `children`/`textProps.children` leaves,
   images, and detail links; group experience/education by their
   `initialItems[].item` containers.
5. Merge with the HTML top card into one JSON document.

## 7. Why it's brittle

- `componentId`s, CSS classes, and Voyager `queryId` hashes rotate with
  LinkedIn deploys — expect occasional re-capture from DevTools.
- Cookies expire: `JSESSIONID` rotates fastest, `li_at` lasts longer but dies
  on logout/password change. Authwall / `401`/`403`/`999` = refresh cookies.
- Bot management escalates with volume — low, serial, browser-like traffic
  from one IP survives longest.

## In one line

Capture real authenticated traffic → identify SDUI/RSC as the live data path
→ decode the Flight wire grammar → recognize data lives in `children` /
`textProps.children` leaves → group by structural containers → merge with the
HTML top card.
