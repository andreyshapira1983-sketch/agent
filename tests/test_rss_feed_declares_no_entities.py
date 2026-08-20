"""A feed carrying a DTD or entity declaration is refused before parsing.

The XML comes off someone else's server. `xml.etree` resolves no external
entities, but it does expand internal ones, which is what "billion laughs"
and quadratic blowup need: a 1 MB feed inside the size cap can expand to
gigabytes while parsing. RSS and Atom have no legitimate use for `<!DOCTYPE`
or `<!ENTITY`, so the declaration itself is the refusal — no new dependency,
and no attempt to guess which expansion is small enough to survive.
"""
from __future__ import annotations

import pytest

from tools.rss_fetch import _parse_feed

_BILLION_LAUGHS = """<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
 <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
]>
<rss><channel><title>&lol3;</title></channel></rss>
"""

_PLAIN_DOCTYPE = '<?xml version="1.0"?><!DOCTYPE rss><rss><channel/></rss>'


@pytest.mark.parametrize("payload", [_BILLION_LAUGHS, _PLAIN_DOCTYPE])
def test_a_feed_with_a_doctype_is_refused(payload: str) -> None:
    with pytest.raises(ValueError):
        _parse_feed(payload, limit=5)


def test_an_ordinary_feed_still_parses() -> None:
    xml = (
        '<?xml version="1.0"?><rss><channel><title>News</title>'
        "<item><title>One</title><link>https://example.org/1</link></item>"
        "</channel></rss>"
    )
    title, _kind, entries = _parse_feed(xml, limit=5)
    assert title == "News"
    assert entries and entries[0]["title"] == "One"
