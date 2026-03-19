"""Shared data types."""

import dataclasses


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
