"""Transform the nested Voyager ``profileView`` JSON into our flat schema.

The Voyager response is undocumented and deeply nested; every access here is
defensive (``.get`` with fallbacks) so a missing/renamed field degrades to
``None``/empty rather than crashing the request.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..schemas import (
    Certification,
    Education,
    Experience,
    Images,
    Language,
    Name,
    Profile,
)
from ..utils import canonical_profile_url


def parse_graphql(public_id: str, *responses: dict) -> Profile:
    """Parse one or more Voyager GraphQL responses (normalized ``{data, included}``).

    LinkedIn's GraphQL responses are *normalized*: entities live in a flat ``included``
    list, each tagged with a ``$type`` (or ``entityUrn``) and cross-referenced by URN.
    We bucket ``included`` by entity type and dereference geo/company entities by URN.

    Accepts multiple responses because the profile page splits data across several
    queries (top-card, experiences, education, skills, ...). All are merged.
    """
    included: list[dict] = []
    for resp in responses:
        if isinstance(resp, dict) and isinstance(resp.get("included"), list):
            included.extend(resp["included"])

    by_urn = {e.get("entityUrn"): e for e in included if isinstance(e, dict)}

    def of_type(*suffixes: str) -> list[dict]:
        out = []
        for e in included:
            t = e.get("$type") or e.get("$recipeType") or ""
            if any(t.endswith(s) for s in suffixes):
                out.append(e)
        return out

    profile_entity = (of_type(".profile.Profile", ".Profile") or [{}])[0]

    name = Name(
        first=profile_entity.get("firstName"),
        last=profile_entity.get("lastName"),
        full=_join_name(profile_entity.get("firstName"), profile_entity.get("lastName")),
    )

    return Profile(
        public_id=public_id,
        profile_url=canonical_profile_url(public_id),
        name=name,
        headline=profile_entity.get("headline"),
        location=_gql_location(profile_entity, by_urn),
        industry=_gql_ref_name(profile_entity.get("industry"), by_urn),
        about=profile_entity.get("summary"),
        experience=[_gql_experience(e, by_urn) for e in of_type(".profile.Position", ".Position")],
        education=[_gql_education(e) for e in of_type(".profile.Education", ".Education")],
        skills=[s.get("name") for s in of_type(".profile.Skill", ".Skill") if s.get("name")],
        certifications=[_gql_cert(e) for e in of_type(".profile.Certification", ".Certification")],
        languages=[
            Language(name=e.get("name"), proficiency=e.get("proficiency"))
            for e in of_type(".profile.Language", ".Language")
        ],
        images=Images(
            profile_picture_url=_vector_url(profile_entity.get("profilePicture")),
            background_url=_vector_url(profile_entity.get("backgroundPicture")),
        ),
        fetched_at=datetime.now(timezone.utc).isoformat(),
        cached=False,
    )


def _gql_date_range_end(el: dict) -> str | None:
    dr = el.get("dateRange") or {}
    return _date(dr.get("end")) if isinstance(dr, dict) else None


def _gql_date_range_start(el: dict) -> str | None:
    dr = el.get("dateRange") or {}
    return _date(dr.get("start")) if isinstance(dr, dict) else None


def _gql_experience(el: dict, by_urn: dict) -> Experience:
    return Experience(
        title=el.get("title"),
        company=el.get("companyName") or _gql_ref_name(el.get("company"), by_urn),
        company_url=None,
        location=el.get("locationName"),
        start=_gql_date_range_start(el),
        end=_gql_date_range_end(el),
        description=el.get("description"),
    )


def _gql_education(el: dict) -> Education:
    return Education(
        school=el.get("schoolName"),
        degree=el.get("degreeName"),
        field_of_study=el.get("fieldOfStudy"),
        start=_gql_date_range_start(el),
        end=_gql_date_range_end(el),
    )


def _gql_cert(el: dict) -> Certification:
    return Certification(
        name=el.get("name"),
        authority=el.get("authority") or el.get("authorityName"),
        license_number=el.get("licenseNumber"),
        url=el.get("url"),
        start=_gql_date_range_start(el),
        end=_gql_date_range_end(el),
    )


def _gql_location(profile_entity: dict, by_urn: dict) -> str | None:
    # Newer profiles carry a plain string; older reference a geo entity by URN.
    direct = profile_entity.get("geoLocationName") or profile_entity.get("locationName")
    if direct:
        return direct
    geo = profile_entity.get("geoLocation") or profile_entity.get("location")
    return _gql_ref_name(geo, by_urn)


def _gql_ref_name(ref, by_urn: dict) -> str | None:
    """Resolve a value that may be a name string, an inline dict, or a URN reference."""
    if not ref:
        return None
    if isinstance(ref, str):
        target = by_urn.get(ref, {}) if ref.startswith("urn:") else {"name": ref}
        return _entity_name(target)
    if isinstance(ref, dict):
        # Sometimes a nested {"*geo": "urn:..."} or an inline entity.
        urn = ref.get("*geo") or ref.get("entityUrn")
        if isinstance(urn, str) and urn in by_urn:
            return _entity_name(by_urn[urn])
        return _entity_name(ref)
    return None


def _entity_name(entity: dict) -> str | None:
    return (
        entity.get("defaultLocalizedName")
        or entity.get("name")
        or (entity.get("defaultLocalizedNameWithoutCountryName"))
    )


def parse_profile_view(public_id: str, data: dict) -> Profile:
    profile = data.get("profile") or {}

    name = Name(
        first=profile.get("firstName"),
        last=profile.get("lastName"),
        full=_join_name(profile.get("firstName"), profile.get("lastName")),
    )

    return Profile(
        public_id=public_id,
        profile_url=canonical_profile_url(public_id),
        name=name,
        headline=profile.get("headline"),
        location=_location(profile),
        industry=profile.get("industryName"),
        about=profile.get("summary"),
        experience=_experience(data),
        education=_education(data),
        skills=_skills(data),
        certifications=_certifications(data),
        languages=_languages(data),
        images=_images(profile),
        fetched_at=datetime.now(timezone.utc).isoformat(),
        cached=False,
    )


# --------------------------------------------------------------------------- #
# Section parsers
# --------------------------------------------------------------------------- #
def _elements(data: dict, view_key: str) -> list[dict]:
    view = data.get(view_key) or {}
    elements = view.get("elements")
    return elements if isinstance(elements, list) else []


def _experience(data: dict) -> list[Experience]:
    out: list[Experience] = []
    for el in _elements(data, "positionView"):
        company = el.get("company") or {}
        out.append(
            Experience(
                title=el.get("title"),
                company=el.get("companyName") or company.get("name"),
                company_url=_company_url(company),
                location=el.get("locationName"),
                start=_date(_time_period(el).get("startDate")),
                end=_date(_time_period(el).get("endDate")),
                description=el.get("description"),
            )
        )
    return out


def _education(data: dict) -> list[Education]:
    out: list[Education] = []
    for el in _elements(data, "educationView"):
        out.append(
            Education(
                school=el.get("schoolName"),
                degree=el.get("degreeName"),
                field_of_study=el.get("fieldOfStudy"),
                start=_date(_time_period(el).get("startDate")),
                end=_date(_time_period(el).get("endDate")),
            )
        )
    return out


def _skills(data: dict) -> list[str]:
    names = [el.get("name") for el in _elements(data, "skillView")]
    return [n for n in names if n]


def _certifications(data: dict) -> list[Certification]:
    out: list[Certification] = []
    for el in _elements(data, "certificationView"):
        out.append(
            Certification(
                name=el.get("name"),
                authority=el.get("authority"),
                license_number=el.get("licenseNumber"),
                url=el.get("url"),
                start=_date(_time_period(el).get("startDate")),
                end=_date(_time_period(el).get("endDate")),
            )
        )
    return out


def _languages(data: dict) -> list[Language]:
    out: list[Language] = []
    for el in _elements(data, "languageView"):
        out.append(Language(name=el.get("name"), proficiency=el.get("proficiency")))
    return out


# --------------------------------------------------------------------------- #
# Field helpers
# --------------------------------------------------------------------------- #
def _join_name(first: str | None, last: str | None) -> str | None:
    full = " ".join(p for p in (first, last) if p).strip()
    return full or None


def _location(profile: dict) -> str | None:
    return (
        profile.get("locationName")
        or profile.get("geoLocationName")
        or profile.get("geoCountryName")
        or (profile.get("location") or {}).get("basicLocation", {}).get("countryCode")
    )


def _time_period(el: dict) -> dict:
    tp = el.get("timePeriod")
    return tp if isinstance(tp, dict) else {}


def _date(d: dict | None) -> str | None:
    """Format a Voyager {month, year} date to 'YYYY-MM' or 'YYYY'. None => present/unknown."""
    if not isinstance(d, dict):
        return None
    year = d.get("year")
    month = d.get("month")
    if year is None:
        return None
    if month:
        return f"{int(year):04d}-{int(month):02d}"
    return f"{int(year):04d}"


def _company_url(company: dict) -> str | None:
    urn = company.get("miniCompany", {}).get("entityUrn") or company.get("entityUrn")
    if isinstance(urn, str) and "company:" in urn:
        return f"https://www.linkedin.com/company/{urn.rsplit(':', 1)[-1]}/"
    return None


def _images(profile: dict) -> Images:
    return Images(
        profile_picture_url=_vector_url(
            _dig(profile, "miniProfile", "picture")
            or profile.get("profilePicture")
            or profile.get("picture")
        ),
        background_url=_vector_url(
            _dig(profile, "miniProfile", "backgroundImage")
            or profile.get("backgroundImage")
        ),
    )


def _dig(node: dict, *keys: str):
    cur: object = node
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _vector_url(node) -> str | None:
    """Reconstruct the largest image URL from a Voyager VectorImage node.

    A VectorImage is ``{rootUrl, artifacts: [{width, fileIdentifyingUrlPathSegment}]}``,
    sometimes wrapped under the ``com.linkedin.common.VectorImage`` key.
    """
    if not isinstance(node, dict):
        return None
    vector = node.get("com.linkedin.common.VectorImage") or node
    root = vector.get("rootUrl")
    artifacts = vector.get("artifacts")
    if not root or not isinstance(artifacts, list) or not artifacts:
        return None
    largest = max(artifacts, key=lambda a: a.get("width", 0) if isinstance(a, dict) else 0)
    segment = largest.get("fileIdentifyingUrlPathSegment")
    return f"{root}{segment}" if segment else None
