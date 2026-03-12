"""CrossRef REST API client.

Set CROSSREF_MAILTO to use the polite pool (~50 req/sec).
If unset, requests are made without a mailto identifier (standard pool).
"""

import logging
import os
import re
import httpx

from app.http_utils import get_with_retry
from app.xml_handler import RefFields

logger = logging.getLogger(__name__)

_BASE = "https://api.crossref.org/works"
_MAILTO = os.getenv("CROSSREF_MAILTO")
_USER_AGENT = (
    f"jats-ref-refinery/0.1 (mailto:{_MAILTO})" if _MAILTO
    else "jats-ref-refinery/0.1"
)
_ROWS = 5
# Minimum CrossRef score to consider a candidate worth scoring.
# CrossRef scores are unbounded but typically 1–200; anything below
# this is unlikely to survive our own confidence scoring, so is discarded.
_MIN_CANDIDATE_SCORE = 20.0


class CrossRefResolver:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def lookup(self, ref: RefFields) -> list[dict]:
        """Query CrossRef and return up to _ROWS normalised candidate dicts."""
        if not ref.title:
            return []

        source = ref.source if ref.source != ref.title else ""
        raw = " ".join(filter(None, [ref.title, source, ref.year]))
        query = _sanitise_query(raw)
        params = {
            "query.bibliographic": query,
            "rows": _ROWS,
        }
        if _MAILTO:
            params["mailto"] = _MAILTO
        if ref.first_author:
            params["query.author"] = ref.first_author

        try:
            resp = await get_with_retry(
                self._client,
                _BASE,
                params=params,
                headers={"User-Agent": _USER_AGENT},
            )
        except httpx.HTTPError as exc:
            logger.debug("CrossRef request failed: %s", exc)
            return []

        data = resp.json()
        items = data.get("message", {}).get("items", [])
        candidates = [_normalise(item) for item in items]
        filtered = [c for c in candidates
                    if c["api_score"] >= _MIN_CANDIDATE_SCORE]
        return filtered if filtered else candidates


def _sanitise_query(query: str) -> str:
    """Strip reserved characters that might cause a 400."""
    return re.sub(r'[+\-=&|><!(){}\[\]^"~*?:\\/]', " ", query)


def _normalise(item: dict) -> dict:
    titles = item.get("title", [])
    container = item.get("container-title", [])
    short_container = item.get("short-container-title", [])
    authors = item.get("author", [])
    first_author = ""
    if authors:
        first_author = authors[0].get("family", "")

    issued = item.get("issued", {}).get("date-parts", [[None]])
    year = str(issued[0][0]) if issued and issued[0] and issued[0][0] else ""

    return {
        "doi": item.get("DOI", ""),
        "title": titles[0] if titles else "",
        "first_author": first_author,
        "year": year,
        "source": (
            container[0] if container
            else (item.get("institution") or [{}])[0].get("name", "")
        ),
        "short_source": short_container[0] if short_container else "",
        "pages": item.get("page", ""),
        "api_score": item.get("score", 0.0),
    }
