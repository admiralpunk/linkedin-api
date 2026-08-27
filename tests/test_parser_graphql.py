import json
from pathlib import Path

import pytest

from app.voyager.parser import parse_graphql

FIXTURE = Path(__file__).parent / "fixtures" / "graphql_profile.json"


@pytest.fixture
def raw():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_graphql_basic(raw):
    p = parse_graphql("gracehopper", raw)
    assert p.public_id == "gracehopper"
    assert p.name.full == "Grace Hopper"
    assert p.headline.startswith("Rear Admiral")
    assert p.location == "Arlington, Virginia, United States"
    assert p.industry == "Computer Hardware"  # resolved from URN reference
    assert "compiler" in p.about


def test_graphql_sections(raw):
    p = parse_graphql("gh", raw)
    assert [e.title for e in p.experience] == ["Senior Programmer", "Consultant"]
    assert p.experience[0].start == "1959-01"
    assert p.experience[0].end == "1971-12"
    assert p.experience[1].end is None  # ongoing
    assert p.education[0].school == "Yale University"
    assert p.education[0].degree == "PhD"
    assert set(p.skills) == {"Compilers", "COBOL"}
    assert p.certifications[0].authority == "US Navy"
    assert p.certifications[0].start == "1943-12"
    assert p.languages[0].name == "English"


def test_graphql_images_largest(raw):
    p = parse_graphql("gh", raw)
    assert p.images.profile_picture_url == "https://media.example.com/pic/800/p.jpg"
    assert p.images.background_url == "https://media.example.com/bg/1584/b.jpg"


def test_graphql_merges_multiple_responses(raw):
    # Splitting sections across responses (as the real profile page does) still merges.
    top = {"included": [e for e in raw["included"] if "Position" not in e["$type"]]}
    positions = {"included": [e for e in raw["included"] if "Position" in e["$type"]]}
    p = parse_graphql("gh", top, positions)
    assert p.name.full == "Grace Hopper"
    assert len(p.experience) == 2


def test_graphql_empty_safe():
    p = parse_graphql("nobody", {})
    assert p.name.full is None
    assert p.experience == []
    assert p.skills == []
