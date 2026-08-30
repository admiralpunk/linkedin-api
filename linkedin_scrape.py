#!/usr/bin/env python3
"""
linkedin_scrape.py — best-effort profile extractor for LinkedIn's SDUI stack.

Accepts a profile URL and returns: name, headline, location, connections, about,
experience, education, skills, certifications, languages, and profile/entity
image URLs — as far as a single-pass fetch can recover them.

ARCHITECTURE (learned by reverse-engineering the live responses)
---------------------------------------------------------------
1. The logged-in profile PAGE is server-rendered HTML. The TOP CARD
   (name, headline, location, connections, photo, current company/school) is
   plain DOM text there — parsed with regex, no extra API call.
2. The rest of the profile is delivered by lazy `profileCards*` component calls
   that return an RSC (React Flight) stream. Data lives as leaves in the element
   tree in TWO forms:
       * plain   children:["<value>"]      -> entity primary fields
                                              (job title, company, school, degree)
       * textProps.children:["<value>"]    -> dates, locations, section titles,
                                              descriptions
   flight_parse.py resolves the Flight reference grammar so these materialize.
3. Some sub-sections (skills, certifications, languages, recommendations) are
   lazy behind a second per-componentKey resolve and may come back empty in a
   single pass — that is architectural. Their section MARKERS still appear.

FOR EDUCATIONAL / PERSONAL USE ONLY. Automating access — even to your own
account — violates LinkedIn's Terms of Service. Keep volume tiny and serial.
componentIds / markup classes rotate; re-capture from DevTools if a field stops.

Credentials via environment variables OR a local .env (KEY=VALUE lines):
    LI_AT          li_at cookie value
    LI_JSESSIONID  (or JSESSIONID)  e.g.  ajax:8399673192947890215

Usage:
    python linkedin_scrape.py "https://www.linkedin.com/in/some-slug/"
    python linkedin_scrape.py "https://www.linkedin.com/in/some-slug/" --debug
"""

import html as htmlmod
import json
import os
import random
import re
import sys
import time
from urllib.parse import urlparse

from curl_cffi import requests

import flight_parse as fp  # local module (same folder)

BASE = "https://www.linkedin.com"
APP_VERSION = "0.2.6951"
CARD_PREFIX = "com.linkedin.sdui.generated.profile.dsl.impl."
# TopComponents intentionally omitted — top card comes from the page HTML.
CARDS = [
    "profileCardsAboveActivity",       # about, featured, services
    "profileCardsActivity",            # followers, recent activity
    "profileCardsBelowActivityPart1",  # experience, education (+ cert/lang markers)
    "profileCardsBelowActivityPart2",  # recommendations
    "profileCardsBelowActivityPart3",  # courses, honors, publications, tests
    "profileCardsBelowActivityPart4",  # languages, organizations
]


class AuthWallError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# credentials
# --------------------------------------------------------------------------- #
def load_env_file():
    """Load KEY=VALUE lines from a local .env into os.environ (without clobbering
    already-set env vars). Lets any launcher — PM2, bare uvicorn, systemd — pick
    up LI_AT / JSESSIONID / API_KEY / PROXY_URL the same way Docker --env-file does.
    """
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    for line in open(env_path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"'))


def load_creds():
    load_env_file()
    li_at = os.environ.get("LI_AT", "").strip()
    jsid = os.environ.get("LI_JSESSIONID", os.environ.get("JSESSIONID", "")).strip()
    if not (li_at and jsid):
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        if os.path.exists(env_path):
            for line in open(env_path, encoding="utf-8"):
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    v = v.strip().strip('"')
                    if k.strip() == "LI_AT" and not li_at:
                        li_at = v
                    if k.strip() in ("LI_JSESSIONID", "JSESSIONID") and not jsid:
                        jsid = v
    if jsid and not jsid.startswith("ajax:"):
        jsid = "ajax:" + jsid
    return li_at, jsid


def persist_jsessionid(new_jsid):
    """Write a freshly-rotated JSESSIONID back to .env and os.environ so the
    next process/API call picks it up — no manual DevTools copy needed.
    (LI_AT still requires a manual refresh; it can only be renewed by
    actually logging in again.)
    """
    fresh = new_jsid if new_jsid.startswith("ajax:") else f"ajax:{new_jsid}"
    os.environ["JSESSIONID"] = fresh
    os.environ.pop("LI_JSESSIONID", None)  # keep one source of truth

    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    lines = open(env_path, encoding="utf-8").read().splitlines()
    out, replaced = [], False
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in ("JSESSIONID", "LI_JSESSIONID"):
                out.append(f'JSESSIONID="{fresh}"')
                replaced = True
                continue
        out.append(line)
    if not replaced:
        out.append(f'JSESSIONID="{fresh}"')
    with open(env_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


# --------------------------------------------------------------------------- #
# HTML top-card parsing
# --------------------------------------------------------------------------- #
def clean(s):
    if not s:
        return s
    return htmlmod.unescape(re.sub(r"\s+", " ", s)).strip().lstrip(">").strip()


def parse_top_card(page_html):
    """Pull name/headline/location/connections/photo from the rendered top card."""
    out = {"name": None, "headline": None, "location": None,
           "connections": None, "photo": None, "current": []}

    m = re.search(r"<title>([^<|]+)\s*\|\s*LinkedIn</title>", page_html)
    if m:
        out["name"] = clean(m.group(1))

    # visible text between the name node and the About section
    nm = out["name"] or "LinkedIn"
    start = page_html.find(">" + nm + "</p>")
    if start < 0:
        start = page_html.find(nm)
    if start < 0:
        start = 0
    end = page_html.find(">About<", start)
    if end < 0 or end - start > 12000:
        end = start + 12000
    seg = page_html[start:end]
    parts, seen = [], set()
    for chunk in re.split(r"<[^>]+>", seg):
        c = clean(chunk)
        if c and len(c) > 1 and c not in seen:
            seen.add(c)
            parts.append(c)

    # heuristic ordering: [name, headline, ...buttons..., fullname, current, location]
    # headline = first line after name that isn't a UI verb
    verbs = {"More", "Message", "Connect", "Follow", "Contact info", "·"}
    name = out["name"] or ""
    after = [p for p in parts if name not in p]
    for p in after:
        if p not in verbs and not p.isdigit():
            out["headline"] = p
            break

    m = re.search(r"([\w/ .\-]+ Area|[A-Z][\w .\-]+,\s?[\w .\-]+(?:,\s?[\w .\-]+)?)", " | ".join(after))
    # location: prefer an explicit "... Area" or "City, State, Country" line
    for p in after:
        if re.search(r"Area$", p) or re.match(r"^[A-Z][\w .\-]+,\s?[\w .\-]+", p):
            if p not in verbs and "ltd" not in p.lower() and "univ" not in p.lower():
                out["location"] = p
                break

    # connections: number and word are in separate DOM nodes -> match across tags
    m = re.search(r">([\d,]+)<[^>]*>(?:[^<]*<[^>]*>)?\s*connections?", page_html)
    if not m:
        m = re.search(r"([\d,]+)\s*connections?", clean(re.sub(r"<[^>]+>", " ", page_html)))
    if m:
        out["connections"] = m.group(1)

    m = re.search(r'(https://media\.licdn\.com/dms/image/[^"\\ ]*?profile-displayphoto[^"\\ ]+)', page_html)
    if m:
        out["photo"] = htmlmod.unescape(m.group(1))
    return out


# --------------------------------------------------------------------------- #
# Flight-tree extraction
# --------------------------------------------------------------------------- #
def resolve_stream(text):
    rows = {h: fp.parse_row(p) for h, p in fp.split_rows(text).items()}
    if not rows:
        return None
    R = fp.Resolver(rows)
    root = "0" if "0" in rows else sorted(rows)[0]
    return R.row(root)


def collect(node, out):
    """
    Walk a resolved Flight tree, appending (kind, text) in document order:
      kind 'field' = plain children[0] string (title/company/school/degree)
      kind 'meta'  = textProps.children[0] string (dates/location/description)
    Also records section markers, image urls, and detail-page links.
    """
    if isinstance(node, dict):
        # plain children:["string"]  -> primary entity field
        ch = node.get("children")
        if isinstance(ch, list) and len(ch) == 1 and isinstance(ch[0], str) \
           and ch[0].strip() and not ch[0].startswith("$"):
            out["lines"].append(("field", ch[0].strip()))
        elif isinstance(ch, str) and ch.strip() and not ch.startswith("$"):
            out["lines"].append(("field", ch.strip()))
        # textProps.children:["string"] -> meta line
        tp = node.get("textProps")
        if isinstance(tp, dict):
            tch = tp.get("children")
            for c in (tch if isinstance(tch, list) else [tch]):
                if isinstance(c, str) and c.strip() and not c.startswith("$"):
                    out["lines"].append(("meta", c.strip()))
        # button text
        bp = node.get("buttonProps")
        if isinstance(bp, dict):
            for t in bp.get("text", []) if isinstance(bp.get("text"), list) else []:
                if isinstance(t, str) and t.strip():
                    out["lines"].append(("button", t.strip()))
        # section markers
        for k in ("observabilityIdentifier", "data-sdui-component"):
            if isinstance(node.get(k), str):
                out["sections"].add(node[k])
        # images: rootUrl + first rendition suffix
        rp = node.get("renderPayload")
        if isinstance(rp, dict) and isinstance(rp.get("rootUrl"), str):
            rends = node.get("imageRenditions") or rp.get("imageRenditions") or []
            suffix = ""
            if isinstance(rends, list) and rends and isinstance(rends[-1], dict):
                suffix = rends[-1].get("suffixUrl", "")
            out["images"].add(rp["rootUrl"] + suffix)
        # detail (see-all) links = pagination entry points
        sc = node.get("screen")
        if isinstance(sc, dict) and isinstance(sc.get("url"), str):
            out["detail_links"].add(sc["url"])
        for v in node.values():
            collect(v, out)
    elif isinstance(node, list):
        for v in node:
            collect(v, out)


def new_bag():
    return {"lines": [], "sections": set(), "images": set(), "detail_links": set()}


# --- structural entry grouping (by entity-collection-item containers) ------- #
TESTID_RE = re.compile(r"profile_(\w+?)TopLevelSection")


def item_texts(node):
    """Ordered, de-duplicated (kind,text) leaves under one entry container."""
    acc = []
    def rec(n):
        if isinstance(n, dict):
            ch = n.get("children")
            if isinstance(ch, list) and len(ch) == 1 and isinstance(ch[0], str) \
               and ch[0].strip() and not ch[0].startswith("$"):
                acc.append(("field", ch[0].strip()))
            elif isinstance(ch, str) and ch.strip() and not ch.startswith("$"):
                acc.append(("field", ch.strip()))
            tp = n.get("textProps")
            if isinstance(tp, dict):
                tch = tp.get("children")
                for c in (tch if isinstance(tch, list) else [tch]):
                    if isinstance(c, str) and c.strip() and not c.startswith("$"):
                        acc.append(("meta", c.strip()))
            for v in n.values():
                rec(v)
        elif isinstance(n, list):
            for v in n:
                rec(v)
    rec(node)
    # drop consecutive duplicate texts (dates/location render twice)
    out, prev = [], None
    for k, t in acc:
        if t != prev:
            out.append((k, t))
        prev = t
    return out


def find_sections(tree):
    """Map sectionName -> list of entry containers (initialItems[i].item)."""
    sections = {}
    def rec(n):
        if isinstance(n, dict):
            tid = n.get("data-testid")
            if isinstance(tid, str):
                m = TESTID_RE.search(tid)
                if m:
                    items = find_initial_items(n)
                    if items:
                        sections.setdefault(m.group(1).lower(), []).extend(items)
            for v in n.values():
                rec(v)
        elif isinstance(n, list):
            for v in n:
                rec(v)
    rec(tree)
    return sections


def find_initial_items(node):
    """Return the list of entry element-subtrees from the nearest initialItems."""
    found = []
    def rec(n):
        if isinstance(n, dict):
            ii = n.get("initialItems")
            if isinstance(ii, list):
                for entry in ii:
                    if isinstance(entry, dict) and "item" in entry:
                        found.append(entry["item"])
            for v in n.values():
                rec(v)
        elif isinstance(n, list):
            for v in n:
                rec(v)
    rec(node)
    return found


def structure_experience(items):
    out = []
    for it in items:
        seq = item_texts(it)
        texts = [t for _k, t in seq]
        texts = [t for t in texts if t not in ("more", "Show all") and not t.startswith("Show all")]
        if not texts:
            continue
        entry = {"title": texts[0]}
        rest = texts[1:]
        # company · employment-type
        if rest and "·" in rest[0]:
            comp, _, etype = rest[0].partition("·")
            entry["company"] = comp.strip()
            entry["employment_type"] = etype.strip() or None
            rest = rest[1:]
        dates = next((t for t in rest if DATE_RE.search(t)), None)
        if dates:
            entry["dates"] = dates
            rest = [t for t in rest if t != dates]
        loc = next((t for t in rest if "," in t or "Area" in t or "site" in t.lower()), None)
        if loc:
            entry["location"] = loc
            rest = [t for t in rest if t != loc]
        if rest:
            entry["description"] = " ".join(rest)
        out.append(entry)
    return out


def structure_education(items):
    out = []
    for it in items:
        texts = [t for _k, t in item_texts(it)]
        texts = [t for t in texts if t not in ("more", "Show all") and not t.startswith("Show all")]
        if not texts:
            continue
        entry = {"school": texts[0]}
        rest = texts[1:]
        if rest and DATE_RE.search(rest[-1]):
            pass
        degree = next((t for t in rest if not DATE_RE.search(t) and not t.startswith("Activities")), None)
        if degree:
            entry["degree"] = degree
            rest = [t for t in rest if t != degree]
        years = next((t for t in rest if DATE_RE.search(t)), None)
        if years:
            entry["years"] = years
            rest = [t for t in rest if t != years]
        act = next((t for t in rest if t.startswith("Activities")), None)
        if act:
            entry["activities"] = act
        out.append(entry)
    return out


# --------------------------------------------------------------------------- #
# networking
# --------------------------------------------------------------------------- #
class Scraper:
    def __init__(self, li_at, jsid, debug=False):
        self.csrf, self.debug = jsid, debug
        # impersonate="chrome" replays a real Chrome's TLS/JA3 + HTTP2
        # fingerprint (curl-impersonate under the hood) instead of Python's
        # own OpenSSL handshake, which bot-detection fingerprints trivially.
        # It also sets a matching user-agent/sec-ch-ua bundle on its own —
        # don't override user-agent manually, that'd create a UA-vs-JA3
        # mismatch, itself a stronger tell than sending no override at all.
        self.s = requests.Session(impersonate="chrome")
        self.s.cookies.set("li_at", li_at, domain=".linkedin.com")
        self.s.cookies.set("JSESSIONID", f'"{jsid}"', domain=".linkedin.com")
        self.s.headers.update({"accept-language": "en-US,en;q=0.9"})
        # Route all LinkedIn traffic through a proxy when PROXY_URL is set,
        # e.g. http://user:pass@residential-proxy-host:port
        # (Required on a datacenter host like EC2 — LinkedIn flags AWS IPs.)
        proxy = os.environ.get("PROXY_URL", "").strip()
        if proxy:
            self.s.proxies = {"http": proxy, "https": proxy}

    def _sync_jsessionid(self):
        """Pick up any JSESSIONID rotation from the live cookie jar (requests
        applies Set-Cookie responses automatically) so later csrf-token
        headers match the CURRENT cookie, and persist it for next time.
        """
        raw = self.s.cookies.get("JSESSIONID", domain=".linkedin.com")
        if not raw:
            return
        fresh = raw.strip('"')
        if fresh and fresh != self.csrf:
            self.csrf = fresh
            persist_jsessionid(fresh)

    def get_page(self, slug):
        try:
            r = self.s.get(f"{BASE}/in/{slug}/", timeout=30)
        except requests.exceptions.TooManyRedirects:
            raise AuthWallError(
                "Too many redirects — li_at/JSESSIONID expired or invalid."
            )
        if r.status_code in (401, 403):
            raise AuthWallError(f"HTTP {r.status_code}")
        if r.status_code == 999:
            raise RuntimeError("HTTP 999 — bot-detection. Stop and back off.")
        r.encoding = "utf-8"
        if "authwall" in r.text and "window.location.href" in r.text:
            raise AuthWallError("Authwall — li_at missing/expired.")
        self._sync_jsessionid()
        return r.text

    def component(self, short, slug, viewee):
        cid = CARD_PREFIX + short
        url = (f"{BASE}/flagship-web/rsc-action/actions/component"
               f"?componentId={cid}&sduiid={cid}")
        body = {"clientArguments": {"payload": {
                    "isSelfView": False, "vanityName": slug,
                    "replaceableSectionArgs": {"vanityName": slug,
                        "hideCardsForGoldenGate": False,
                        "shouldSetupReplaceableComponent": True,
                        "vieweeProfileId": viewee, "isSelfView": False,
                        "isSelfViewResolved": False}},
                "states": [], "requestMetadata": {"$type": "proto.sdui.common.RequestMetadata"},
                "screenId": "com.linkedin.sdui.flagshipnav.profile.Profile",
                "knownTemplateIds": []}}
        track = json.dumps({"clientVersion": APP_VERSION, "mpVersion": APP_VERSION,
            "osName": "web", "timezoneOffset": 5.5, "timezone": "Asia/Calcutta",
            "mpName": "web", "deviceFormFactor": "DESKTOP", "displayDensity": 1.5,
            "displayWidth": 1920, "displayHeight": 1080})
        h = {"accept": "*/*", "content-type": "application/json",
             "csrf-token": self.csrf, "origin": BASE, "referer": f"{BASE}/in/{slug}/",
             "x-li-application-version": APP_VERSION, "x-li-rsc-stream": "true",
             "x-li-track": track}
        r = self.s.post(url, headers=h, data=json.dumps(body), timeout=30)
        if self.debug:
            print(f"    {short:32} HTTP {r.status_code} {len(r.text)}B", file=sys.stderr)
        self._sync_jsessionid()
        return r.text if r.status_code < 400 else ""


# --------------------------------------------------------------------------- #
# assembly
# --------------------------------------------------------------------------- #
def route(section_bags):
    """Turn per-card ordered lines into structured sections by their markers."""
    result = {"about": None, "experience": [], "education": [],
              "skills": [], "certifications": [], "languages": [],
              "activity": [], "images": [], "detail_links": [], "_sections": set()}
    all_imgs, all_links = set(), set()

    for short, bag in section_bags.items():
        result["_sections"] |= bag["sections"]
        all_imgs |= bag["images"]
        all_links |= bag["detail_links"]
        markers = " ".join(bag["sections"]).lower()
        lines = bag["lines"]
        field = [t for k, t in lines if k in ("field", "meta")]

        if "aboutsection" in markers and short == "profileCardsAboveActivity":
            # About is the longest field/meta blob on the AboveActivity card
            longest = max([t for _k, t in lines], key=len, default=None)
            if longest and len(longest) > 40:
                result["about"] = longest
        if short == "profileCardsBelowActivityPart1" and bag.get("tree") is not None:
            secs = find_sections(bag["tree"])
            result["experience"] = structure_experience(secs.get("experience", []))
            result["education"] = structure_education(secs.get("education", []))
        if short == "profileCardsActivity":
            result["activity"] = [t for k, t in lines if k in ("field", "meta")][:12]
        if "language" in markers:
            result["languages"] += field
        if "certification" in markers:
            result["certifications"] += field

    result["images"] = sorted(all_imgs)
    result["detail_links"] = sorted(all_links)
    result["_sections"] = sorted(s.split(".")[-1] for s in result["_sections"])
    return result


DATE_RE = re.compile(r"(Present|\b\d{4}\b|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b)")


def group_entries(lines, kind):
    """
    Split the ordered (kind,text) lines into entries. An entry starts at a
    'field' line (title/school) and runs until the next field that follows a
    date/meta line. Pragmatic — exact grouping depends on card layout.
    """
    # take only text after the section header; keep both field & meta in order
    seq = [(k, t) for k, t in lines if k in ("field", "meta")]
    # drop everything before the section title word
    header = "Experience" if kind == "experience" else "Education"
    if header in [t for _k, t in seq]:
        i = [t for _k, t in seq].index(header)
        seq = seq[i + 1:]
    # cut at the other section's header if present
    other = "Education" if kind == "experience" else "Experience"
    cut = [t for _k, t in seq]
    if other in cut:
        seq = seq[:cut.index(other)]

    entries, cur = [], []
    for k, t in seq:
        if t in ("Show all", "more") or t.startswith("Show all"):
            continue
        if k == "field" and cur and any(DATE_RE.search(x) for x in cur):
            entries.append(cur); cur = []
        cur.append(t)
    if cur:
        entries.append(cur)
    return [" | ".join(e) for e in entries if e]


def slug_of(url):
    m = re.search(r"/in/([^/?#]+)", urlparse(url).path)
    return m.group(1) if m else None


def scrape_profile(url, li_at, jsid, debug=False):
    """Fetch and structure a LinkedIn profile. Returns the profile dict.

    Raises ValueError on a bad URL, AuthWallError on dead cookies, RuntimeError
    on bot-detection (HTTP 999). This is the entry point the API and CLI share.
    """
    if not (li_at and jsid):
        raise AuthWallError("No LinkedIn cookies configured.")
    slug = slug_of(url)
    if not slug:
        raise ValueError("Not a /in/ profile URL.")

    sc = Scraper(li_at, jsid, debug=debug)
    page = sc.get_page(slug)
    top = parse_top_card(page)
    viewee_m = re.search(r"urn:li:fsd_profile:([A-Za-z0-9_-]{20,})", page)
    viewee = viewee_m.group(1) if viewee_m else ""
    if debug:
        print(f"    slug={slug} vieweeId={viewee}", file=sys.stderr)

    bags = {}
    for short in CARDS:
        time.sleep(random.uniform(0.6, 1.2))  # jitter — avoid a robotic fixed cadence
        stream = sc.component(short, slug, viewee)
        if stream.strip():
            bag = new_bag()
            tree = resolve_stream(stream)
            if tree is not None:
                collect(tree, bag)
                bag["tree"] = tree
            bags[short] = bag

    routed = route(bags)
    return {
        "input_url": url, "slug": slug, "vieweeProfileId": viewee,
        "name": top["name"], "headline": top["headline"],
        "location": top["location"], "connections": top["connections"],
        "profile_photo": top["photo"],
        "about": routed["about"],
        "experience": routed["experience"],
        "education": routed["education"],
        "skills": routed["skills"],
        "certifications": routed["certifications"],
        "languages": routed["languages"],
        "activity": routed["activity"],
        "entity_images": routed["images"],
        "detail_links": routed["detail_links"],
        "_sections_seen": routed["_sections"],
    }


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    debug = "--debug" in sys.argv
    url = [a for a in sys.argv[1:] if not a.startswith("--")][0]

    li_at, jsid = load_creds()
    if not (li_at and jsid):
        print("ERROR: set LI_AT + LI_JSESSIONID (env or .env).")
        return 2

    profile = scrape_profile(url, li_at, jsid, debug=debug)

    here = os.path.dirname(os.path.abspath(__file__))
    out_path = os.path.join(here, "profile_extracted.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)

    summary = {k: profile[k] for k in ("name", "headline", "location",
               "connections", "about", "experience", "education")}
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nfull output -> {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AuthWallError as e:
        print(f"AUTHWALL: {e}")
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {e}")
        sys.exit(1)
