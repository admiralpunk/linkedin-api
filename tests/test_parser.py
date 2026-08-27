import json
from pathlib import Path

import pytest

from app.voyager.parser import parse_profile_view

FIXTURE = Path(__file__).parent / "fixtures" / "profileView.json"


@pytest.fixture
def raw():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_basic_fields(raw):
    p = parse_profile_view("ada-lovelace", raw)
    assert p.public_id == "ada-lovelace"
    assert p.profile_url == "https://www.linkedin.com/in/ada-lovelace/"
    assert p.name.full == "Ada Lovelace"
    assert p.headline.startswith("Mathematician")
    assert p.location == "London, England, United Kingdom"
    assert p.industry == "Computer Software"
    assert "Analytical Engine" in p.about


def test_experience(raw):
    p = parse_profile_view("ada", raw)
    assert len(p.experience) == 2
    first = p.experience[0]
    assert first.title == "Independent Researcher"
    assert first.company == "Analytical Engine Project"
    assert first.start == "1842-06"
    assert first.end == "1843-11"
    # Ongoing position: no end date.
    assert p.experience[1].start == "1833"
    assert p.experience[1].end is None


def test_education_skills_certs_langs(raw):
    p = parse_profile_view("ada", raw)
    assert p.education[0].school == "Private Tutoring"
    assert p.education[0].start == "1828"
    assert p.skills == ["Algorithms", "Mathematics", "Technical Writing"]
    assert p.certifications[0].authority == "Royal Society"
    assert p.certifications[0].start == "1843-10"
    assert {lang.name for lang in p.languages} == {"English", "French"}


def test_images_pick_largest(raw):
    p = parse_profile_view("ada", raw)
    assert p.images.profile_picture_url == "https://media.example.com/image/400_400/pic.jpg"
    assert p.images.background_url == "https://media.example.com/bg/1584/bg.jpg"


def test_empty_response_is_safe():
    p = parse_profile_view("nobody", {})
    assert p.public_id == "nobody"
    assert p.name.full is None
    assert p.experience == []
    assert p.skills == []
    assert p.images.profile_picture_url is None
