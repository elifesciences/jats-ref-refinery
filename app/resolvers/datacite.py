"""DataCite REST API client.

Fallback when CrossRef yields no high-confidence match.
Covers datasets, software, Zenodo preprints, and other non-journal content.
"""

from typing import Optional

import httpx

from app.http_utils import get_with_retry
from app.xml_handler import RefFields

_BASE = "https://api.datacite.org/dois"


class DataCiteResolver:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def lookup(self, ref: RefFields) -> Optional[dict]:
        """Query DataCite and return a normalised candidate dict, or None."""
        if not ref.title:
            return None

        params = {
            "query": ref.title,
            "page[size]": 1,
        }

        try:
            resp = await get_with_retry(self._client, _BASE, params=params)
        except httpx.HTTPError:
            return None

        data = resp.json()
        items = data.get("data", [])
        if not items:
            return None

        return _normalise(items[0])


def _normalise(item: dict) -> dict:
    attrs = item.get("attributes", {})
    creators = attrs.get("creators", [])
    first_author = ""
    if creators:
        c = creators[0]
        first_author = (
            c.get("name", "")
            or f"{c.get('familyName', '')} {c.get('givenName', '')}".strip()
        )

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
