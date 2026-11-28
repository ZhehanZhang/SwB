import pytest

from switchboard.doc_links import doc_link, rewrite_doc_links

GUIDE = "https://swb.one/docs/get-started"
ADDRESS = "https://swb.example.com"
SUFFIX = "#swb=https%3A%2F%2Fswb.example.com"


@pytest.mark.parametrize(("address", "fragment"), [
    (ADDRESS, "https%3A%2F%2Fswb.example.com"),
    (ADDRESS + "/", "https%3A%2F%2Fswb.example.com"),
    ("https://swb.example.com:8870", "https%3A%2F%2Fswb.example.com%3A8870"),
    ("http://localhost:8870", "http%3A%2F%2Flocalhost%3A8870"),
    ("http://127.0.0.1", "http%3A%2F%2F127.0.0.1"),
    ("http://[::1]:8870", "http%3A%2F%2F%5B%3A%3A1%5D%3A8870"),
    ("https://[2001:db8::1]", "https%3A%2F%2F%5B2001%3Adb8%3A%3A1%5D"),
    ("https://münchen.example", "https%3A%2F%2Fm%C3%BCnchen.example"),
])
def test_doc_link_accepts_valid_origins(address, fragment):
    assert doc_link("get-started", address) == f"{GUIDE}#swb={fragment}"


@pytest.mark.parametrize("address", [
    None,
    "",
    "http://example.com",
    "https://user:password@example.com",
    "https://@example.com",
    "https://example.com?query=value",
    "https://example.com?",
    "https://example.com#fragment",
    "https://example.com#",
    "https://example.com/setup",
    "https://example.com//",
    "javascript:alert(1)",
    "https:///example.com",
    " https://example.com",
    "https://exam ple.com",
    "https://exam\nple.com",
    "https://exam\x85ple.com",
    "https://example.com\\other.test",
    "https://example..com",
    "https://-example.com",
    "https://example.com:",
    "https://example.com:65536",
    "https://[::1]extra",
    "https://[fe80::1%25en0]",
])
def test_invalid_origins_leave_links_plain(address):
    assert doc_link("get-started", address) == GUIDE
    assert rewrite_doc_links(f"Read {GUIDE}.", address) == f"Read {GUIDE}."


def test_rewrite_is_idempotent_and_keeps_trailing_punctuation():
    text = f"Read {GUIDE}. ([setup]({GUIDE})), then `{GUIDE}`, or <{GUIDE}>."
    link = GUIDE + SUFFIX
    rewritten = f"Read {link}. ([setup]({link})), then `{link}`, or <{link}>."
    assert rewrite_doc_links(text, ADDRESS) == rewritten
    assert rewrite_doc_links(rewritten, ADDRESS) == rewritten


def test_rewrite_preserves_the_query_in_a_docs_link():
    url = f"{GUIDE}?agents=(claude)"
    assert rewrite_doc_links(f"{url}.", ADDRESS) == f"{url}{SUFFIX}."


@pytest.mark.parametrize("url", [
    f"{GUIDE}#existing",
    f"{GUIDE}#",
    "https://swb.one/docs",
    "https://swb.one.evil.test/docs/get-started",
    "https://example.com/docs/get-started",
    f"https://example.com/redirect?url={GUIDE}",
    f"https://example.com/redirect?url=({GUIDE})",
    f"https://[2001:db8::1]/redirect?url={GUIDE}",
])
def test_rewrite_leaves_existing_fragments_and_other_urls_alone(url):
    assert rewrite_doc_links(f"Read {url}.", ADDRESS) == f"Read {url}."
