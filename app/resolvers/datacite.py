"""DataCite REST API client.

Fallback when CrossRef yields no high-confidence match.
Covers datasets, software, Zenodo preprints, and other non-journal content.
"""

import logging

import httpx

from app.http_utils import get_with_retry
from app.xml_handler import RefFields

logger = logging.getLogger(__name__)

_BASE = "https://api.datacite.org/dois"
_ROWS = 5


class DataCiteResolver:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def lookup(self, ref: RefFields) -> list[dict]:
        """Query DataCite and return up to _ROWS normalised candidate dicts."""
        if not ref.title:
            return []

        parts = [f'titles.title:"{ref.title}"']
        if ref.first_author:
            parts.append(f"creators.familyName:{ref.first_author}")
        if ref.year:
            parts.append(f"publicationYear:{ref.year}")
        query = " AND ".join(parts)

        params = {
            "query": query,
            "page[size]": _ROWS,
        }

        try:
            resp = await get_with_retry(self._client, _BASE, params=params)
        except httpx.HTTPError as exc:
            logger.debug("DataCite request failed: %r", exc)
            return []

        items = resp.json().get("data", [])
        return [_normalise(item) for item in items]


def _normalise(item: dict) -> dict:
    attrs = item.get("attributes", {})
    creators = attrs.get("creators", [])
    first_author = ""
    if creators:
        first_author = creators[0].get("familyName", "")

    titles = attrs.get("titles", [])
    title = titles[0].get("title", "") if titles else ""

    year = str(attrs.get("publicationYear", ""))
    publisher = attrs.get("publisher", "")
    doi = attrs.get("doi", "")

    return {
        "doi": doi,
        "title": title,
        "first_author": first_author,
        "year": year,
        "source": publisher,
        "api_score": 0.0,  # DataCite does not expose a relevance score
    }
