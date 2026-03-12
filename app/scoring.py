"""Confidence scoring for reference matches.

Weighted combination of present fields only:
  - Title fuzzy match  (primary signal, via rapidfuzz)
  - First author match
  - Year match
  - Source (journal/book/publisher) fuzzy match
  - Pages match (fpage contained in candidate page range)
  - API relevance score (CrossRef native score, normalised)

Fields missing from either the ref or the candidate are excluded from the
composite rather than penalised, so sparse JATS refs are scored fairly.
"""

import re

from rapidfuzz import fuzz

from app.xml_handler import RefFields

HIGH_CONFIDENCE_THRESHOLD = 0.75

_WEIGHTS = {
    "title": 0.50,
    "source": 0.30,
    "author": 0.20,
    "year": 0.15,
    "pages": 0.05,
    "api_score": 0.05,
}


def _clean(s: str) -> str:
    """Strip HTML tags, normalise whitespace, remove trailing full stop."""
    s = re.sub(r"<[^>]+>", "", s)
    s = s.strip()
    if s.endswith("."):
        s = s[:-1]
    s = re.sub(r"\s+", " ", s)
    return s


def score_match(ref: RefFields, candidate: dict) -> float:
    """Return a 0–1 confidence score for how well candidate matches ref.

    Only fields present in both ref and candidate contribute to the score.
    The weights of present fields are renormalised to sum to 1.0.
    """
    scores: dict[str, float] = {}

    if ref.title and candidate.get("title"):
        scores["title"] = (
            fuzz.token_sort_ratio(
                _clean(ref.title), _clean(candidate["title"])
            ) / 100.0
        )

    if ref.first_author and candidate.get("first_author"):
        scores["author"] = (
            fuzz.token_sort_ratio(
                _clean(ref.first_author), _clean(candidate["first_author"])
            )
            / 100.0
        )

    if ref.year and candidate.get("year"):
        scores["year"] = 1.0 if ref.year == str(candidate["year"]) else 0.0

    if ref.pages and candidate.get("pages"):
        # ref.pages is fpage only; candidate may return a range e.g. "123-145"
        scores["pages"] = 1.0 if ref.pages in candidate["pages"] else 0.0

    if ref.source and (candidate.get("source") or candidate.get("short_source")):
        ref_source = _clean(ref.source)
        cand_sources = [
            _clean(s) for s in (
                candidate.get("source", ""),
                candidate.get("short_source", ""),
            ) if s
        ]
        scores["source"] = max(
            max(fuzz.token_sort_ratio(ref_source, s),
                fuzz.partial_ratio(ref_source, s))
            for s in cand_sources
        ) / 100.0

    raw_api = candidate.get("api_score", 0.0)
    if raw_api:
        scores["api_score"] = min(raw_api / 200.0, 1.0)

    if not scores:
        return 0.0

    total_weight = sum(_WEIGHTS[k] for k in scores)
    return sum(_WEIGHTS[k] * v for k, v in scores.items()) / total_weight
