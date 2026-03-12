"""Europe PMC REST API client.

Returns both DOI and PMID in a single query.
"""

import logging
import re

import httpx

from app.http_utils import get_with_retry
from app.xml_handler import RefFields

logger = logging.getLogger(__name__)

_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
_USER_AGENT = "jats-ref-refinery/0.1"
_ROWS = 5


class EuropePMCResolver:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def lookup(self, ref: RefFields) -> list[dict]:
        """Query Europe PMC and return up to _ROWS normalised candidate
        dicts."""
        if not ref.title:
            return []

        parts = [f'TITLE:"{_sanitise(ref.title)}"']
        if ref.first_author:
            parts.append(f"AUTH:{ref.first_author}")
        if ref.year:
            parts.append(f"PUB_YEAR:{ref.year}")
        if ref.source and ref.source != ref.title:
            parts.append(f'JOURNAL:"{_sanitise(ref.source)}"')

        params = {
            "query": " AND ".join(parts),
            "format": "json",
            "pageSize": _ROWS,
            "resultType": "core",
        }

        logger.debug(
            "EuropePMC [%s]: querying %r",
            ref.ref_id, params["query"],
        )

        try:
            resp = await get_with_retry(
                self._client,
                _BASE,
                params=params,
                headers={"User-Agent": _USER_AGENT},
            )
        except httpx.HTTPError as exc:
            logger.debug("EuropePMC request failed: %r", exc)
            return []

        results = (
            resp.json()
            .get("resultList", {})
            .get("result", [])
        )
        return [_normalise(r) for r in results]


def _sanitise(s: str) -> str:
    """Strip characters that break Lucene phrase queries."""
    return re.sub(r'["\\]', " ", s).strip()


def _normalise(result: dict) -> dict:
    """Normalise a Europe PMC result to the shared candidate schema."""
    # authorString: "Li Q, Xie Y, ..." — surname is first token
    author_string = result.get("authorString", "")
    first_author = ""
    if author_string:
        first = author_string.split(",")[0].strip()
        first_author = first.split()[0] if first else ""

    doi = result.get("doi", "") or ""
    pmid = str(result.get("pmid", "")) if result.get("pmid") else ""

    journal = result.get("journal") or {}
    source = journal.get("title", "") or result.get("journalTitle", "")
    short_source = (
        journal.get("medlineAbbreviation", "")
        or journal.get("isoabbreviation", "")
    )

    return {
        "doi": doi,
        "pmid": pmid,
        "title": result.get("title", ""),
        "first_author": first_author,
        "year": str(result.get("pubYear", "")),
        "source": source,
        "short_source": short_source,
        "pages": result.get("pageInfo", ""),
        "api_score": 0.0,  # Europe PMC does not expose a relevance score
    }
