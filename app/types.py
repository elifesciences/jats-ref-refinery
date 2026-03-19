"""Shared data types."""

import dataclasses
from typing import Any, Optional


@dataclasses.dataclass
class EnrichmentResult:
    """Structured enrichment output for a single <ref>.

    doi/pmid: new PIDs to insert as <pub-id> elements.
    suspect_doi/suspect_pmid: existing PIDs that appear incorrect,
        added as XML comments.
    resolver: which API found the match (for internal routing/logging).
    article_title_to_add/journal_name_to_add: tag-fix instructions when the
        original ref had no <article-title> or a mis-tagged <source>.
    """

    doi: str = ""
    pmid: str = ""
    suspect_doi: str = ""
    suspect_pmid: str = ""
    resolver: str = ""
    article_title_to_add: str = ""
    journal_name_to_add: str = ""


@dataclasses.dataclass
class RefFields:
    """Structured fields extracted from a single <ref> element.

    Parsed fields are used for candidate scoring.  title_from_source is set
    when no <article-title> was present and title was derived from <source>;
    this changes both lookup strategy and scoring behaviour.
    """

    element: Any  # lxml element — the <ref> node
    ref_id: str
    title: str
    first_author: str
    year: str
    source: str
    volume: str
    pages: str
    title_from_source: bool = False  # True if derived from <source>
    existing_doi: str = ""   # DOI already present in the input XML, if any
    existing_pmid: str = ""  # PMID already present in the input XML, if any
    nbk_id: str = ""         # NCBI Bookshelf ID from ext-link, if present
    enrichment: Optional[EnrichmentResult] = None  # populated by enricher
