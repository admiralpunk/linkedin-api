import pytest

from app.utils import InvalidProfileURL, canonical_profile_url, extract_public_id


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.linkedin.com/in/williamhgates/", "williamhgates"),
        ("https://www.linkedin.com/in/williamhgates", "williamhgates"),
        ("http://linkedin.com/in/ada-lovelace-123", "ada-lovelace-123"),
        ("https://in.linkedin.com/in/some-slug/?originalSubdomain=in", "some-slug"),
        ("https://m.linkedin.com/in/mobile-user/", "mobile-user"),
        ("https://www.linkedin.com/pub/legacy-user/1/2/3", "legacy-user"),
        ("www.linkedin.com/in/no-scheme/", "no-scheme"),
        ("linkedin.com/in/bare-host", "bare-host"),
        ("https://www.linkedin.com/in/name%20encoded/", "name encoded"),
        ("just-a-slug", "just-a-slug"),
    ],
)
def test_extract_public_id_ok(url, expected):
    assert extract_public_id(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://www.google.com/in/foo",
        "https://www.linkedin.com/feed/",
        "https://www.linkedin.com/company/microsoft/",
    ],
)
def test_extract_public_id_invalid(url):
    with pytest.raises(InvalidProfileURL):
        extract_public_id(url)


def test_canonical_url():
    assert canonical_profile_url("abc") == "https://www.linkedin.com/in/abc/"
