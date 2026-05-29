"""Curated online source library for controlled web learning.

This is not a crawler. It is a small catalog of source families the agent can
search deliberately, then open through `web_fetch` so every learned claim still
has provenance, timestamps, and content hashes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlparse


@dataclass(frozen=True)
class SourceLibraryEntry:
    id: str
    name: str
    category: str
    source_type: str
    search_template: str
    allowed_domains: tuple[str, ...]
    trust_level: float
    description: str
    license_hint: str = ""
    notes: str = ""

    def query(self, topic: str) -> str:
        return self.search_template.format(topic=topic.strip())

    def allows_url(self, url: str) -> bool:
        if not self.allowed_domains:
            return True
        domain = _domain(url)
        return any(domain == item or domain.endswith("." + item) for item in self.allowed_domains)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "source_type": self.source_type,
            "search_template": self.search_template,
            "allowed_domains": list(self.allowed_domains),
            "trust_level": round(float(self.trust_level), 3),
            "description": self.description,
            "license_hint": self.license_hint,
            "notes": self.notes,
        }


SOURCE_LIBRARY: tuple[SourceLibraryEntry, ...] = (
    SourceLibraryEntry(
        id="wikipedia",
        name="Wikipedia",
        category="wikis",
        source_type="article",
        search_template="site:wikipedia.org {topic}",
        allowed_domains=("wikipedia.org",),
        trust_level=0.72,
        description="General encyclopedic overview; useful for orientation, not final authority.",
        license_hint="CC BY-SA",
    ),
    SourceLibraryEntry(
        id="wikibooks",
        name="Wikibooks",
        category="books",
        source_type="book",
        search_template="site:wikibooks.org {topic}",
        allowed_domains=("wikibooks.org",),
        trust_level=0.68,
        description="Open textbook-style material.",
        license_hint="CC BY-SA",
    ),
    SourceLibraryEntry(
        id="wikisource",
        name="Wikisource",
        category="books",
        source_type="book",
        search_template="site:wikisource.org {topic}",
        allowed_domains=("wikisource.org",),
        trust_level=0.66,
        description="Public-domain and freely licensed source texts.",
        license_hint="public domain / free licenses",
    ),
    SourceLibraryEntry(
        id="project_gutenberg",
        name="Project Gutenberg",
        category="books",
        source_type="book",
        search_template="site:gutenberg.org {topic}",
        allowed_domains=("gutenberg.org",),
        trust_level=0.74,
        description="Public-domain books and metadata pages.",
        license_hint="public domain in many jurisdictions",
    ),
    SourceLibraryEntry(
        id="internet_archive",
        name="Internet Archive",
        category="books",
        source_type="book",
        search_template="site:archive.org/details {topic}",
        allowed_domains=("archive.org",),
        trust_level=0.70,
        description="Digital archive pages for books, media, and historical documents.",
        notes="Binary PDFs are refused by web_fetch; text/HTML pages are preferred.",
    ),
    SourceLibraryEntry(
        id="open_library",
        name="Open Library",
        category="books",
        source_type="book",
        search_template="site:openlibrary.org {topic}",
        allowed_domains=("openlibrary.org",),
        trust_level=0.66,
        description="Book catalog pages and metadata.",
    ),
    SourceLibraryEntry(
        id="arxiv",
        name="arXiv",
        category="science",
        source_type="article",
        search_template="site:arxiv.org {topic}",
        allowed_domains=("arxiv.org",),
        trust_level=0.82,
        description="Open scientific preprints; stronger than general web, weaker than peer-reviewed final versions.",
    ),
    SourceLibraryEntry(
        id="pubmed",
        name="PubMed",
        category="science",
        source_type="article",
        search_template="site:pubmed.ncbi.nlm.nih.gov {topic}",
        allowed_domains=("pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov"),
        trust_level=0.86,
        description="Biomedical abstracts and indexed literature metadata.",
        notes="Medical/safety decisions still require escalation and stronger verification.",
    ),
    SourceLibraryEntry(
        id="python_docs",
        name="Python Documentation",
        category="docs",
        source_type="documentation",
        search_template="site:docs.python.org {topic}",
        allowed_domains=("docs.python.org",),
        trust_level=0.90,
        description="Official Python documentation.",
    ),
    SourceLibraryEntry(
        id="mdn",
        name="MDN Web Docs",
        category="docs",
        source_type="documentation",
        search_template="site:developer.mozilla.org {topic}",
        allowed_domains=("developer.mozilla.org",),
        trust_level=0.86,
        description="Web platform documentation.",
    ),
    SourceLibraryEntry(
        id="microsoft_learn",
        name="Microsoft Learn",
        category="docs",
        source_type="documentation",
        search_template="site:learn.microsoft.com {topic}",
        allowed_domains=("learn.microsoft.com",),
        trust_level=0.86,
        description="Microsoft official documentation and learning material.",
    ),
    SourceLibraryEntry(
        id="rfc_editor",
        name="RFC Editor",
        category="docs",
        source_type="documentation",
        search_template="site:rfc-editor.org {topic}",
        allowed_domains=("rfc-editor.org",),
        trust_level=0.90,
        description="Canonical RFC documents.",
    ),
    SourceLibraryEntry(
        id="stanford_encyclopedia",
        name="Stanford Encyclopedia of Philosophy",
        category="encyclopedia",
        source_type="article",
        search_template="site:plato.stanford.edu {topic}",
        allowed_domains=("plato.stanford.edu",),
        trust_level=0.84,
        description="Scholarly encyclopedia articles in philosophy and adjacent topics.",
    ),
)


SOURCE_LIBRARY_GROUPS: dict[str, tuple[str, ...]] = {
    "all": tuple(entry.id for entry in SOURCE_LIBRARY),
    "default": (
        "wikipedia",
        "wikibooks",
        "project_gutenberg",
        "internet_archive",
        "open_library",
        "arxiv",
    ),
    "wikis": ("wikipedia", "wikibooks", "wikisource"),
    "books": ("wikibooks", "wikisource", "project_gutenberg", "internet_archive", "open_library"),
    "science": ("arxiv", "pubmed"),
    "docs": ("python_docs", "mdn", "microsoft_learn", "rfc_editor"),
    "encyclopedia": ("wikipedia", "stanford_encyclopedia"),
}


def list_source_library() -> tuple[SourceLibraryEntry, ...]:
    return SOURCE_LIBRARY


def source_library_payload() -> dict[str, Any]:
    return {
        "groups": {key: list(value) for key, value in SOURCE_LIBRARY_GROUPS.items()},
        "sources": [entry.to_dict() for entry in SOURCE_LIBRARY],
    }


def resolve_source_library(selection: str | Iterable[str] | None = None) -> tuple[SourceLibraryEntry, ...]:
    raw_ids: list[str] = []
    if selection is None:
        raw_ids.extend(SOURCE_LIBRARY_GROUPS["default"])
    elif isinstance(selection, str):
        for piece in selection.replace(";", ",").split(","):
            item = piece.strip().lower()
            if item:
                raw_ids.append(item)
    else:
        raw_ids.extend(str(item).strip().lower() for item in selection if str(item).strip())

    by_id = {entry.id: entry for entry in SOURCE_LIBRARY}
    selected: list[SourceLibraryEntry] = []
    seen: set[str] = set()
    for item in raw_ids:
        expanded = SOURCE_LIBRARY_GROUPS.get(item, (item,))
        for source_id in expanded:
            entry = by_id.get(source_id)
            if entry is None:
                known = ", ".join(sorted(set(by_id) | set(SOURCE_LIBRARY_GROUPS)))
                raise ValueError(f"unknown source library id/group: {source_id}; known: {known}")
            if entry.id in seen:
                continue
            seen.add(entry.id)
            selected.append(entry)
    return tuple(selected)


def _domain(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc or parsed.path.split("/", 1)[0]
    host = host.lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return host
