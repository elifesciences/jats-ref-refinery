"""Europe PMC REST API client.

Returns both DOI and PMID in a single query.
"""

import logging
import os
import re
from typing import Optional

import httpx

from app.http_utils import get_with_retry, parse_json
from app.types import RefFields

logger = logging.getLogger(__name__)

_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
_MAILTO = os.getenv("CROSSREF_MAILTO")
_USER_AGENT = (
    f"jats-ref-refinery/0.1 (mailto:{_MAILTO})" if _MAILTO
    else "jats-ref-refinery/0.1"
)
_ROWS = 5


class EuropePMCResolver:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def lookup(self, ref: RefFields) -> list[dict]:
        """Query Europe PMC and return up to _ROWS normalised candidate
        dicts."""
        if ref.nbk_id:
            result = await self._lookup_by_nbk_id(ref.nbk_id, ref.ref_id)
            if result:
                result["exact_match"] = True
                return [result]

        if not ref.title:
            return []

        parts = [f'TITLE:"{_sanitise(ref.title)}"']
        if ref.first_author:
            parts.append(f"AUTH:{ref.first_author}")
        if ref.year:
            parts.append(f"PUB_YEAR:{ref.year}")

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

        data = parse_json(resp, context=f"europepmc {ref.ref_id}")
        return _parse_results(data)

    async def lookup_by_journal(self, ref: RefFields) -> list[dict]:
        """Query Europe PMC by journal+author+year+volume (no title field).

        Used when ref.title_from_source is True — the <source> value
        is treated as a journal name rather than an article title.
        """
        if not ref.source:
            return []

        parts = [f'JOURNAL:"{_sanitise(ref.source)}"']
        if ref.first_author:
            parts.append(f"AUTH:{ref.first_author}")
        if ref.year:
            parts.append(f"PUB_YEAR:{ref.year}")
        if ref.volume:
            parts.append(f"VOLUME:{ref.volume}")

        params = {
            "query": " AND ".join(parts),
            "format": "json",
            "pageSize": _ROWS,
            "resultType": "core",
        }

        logger.debug(
            "EuropePMC journal-query [%s]: %r",
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
            logger.debug("EuropePMC journal-query failed: %r", exc)
            return []

        data = parse_json(resp, context=f"europepmc-journal {ref.ref_id}")
        return _parse_results(data)

    async def lookup_by_doi(self, doi: str) -> Optional[dict]:
        """Fetch ePMC record by DOI.

        Returns a normalised candidate dict, or None.
        """
        params = {
            "query": f"DOI:{doi}",
            "format": "json",
            "pageSize": 1,
            "resultType": "core",
        }
        logger.debug("EuropePMC verify doi=%s", doi)
        try:
            resp = await get_with_retry(
                self._client,
                _BASE,
                params=params,
                headers={"User-Agent": _USER_AGENT},
            )
        except httpx.HTTPError as exc:
            logger.debug("EuropePMC lookup_by_doi failed: %r", exc)
            return None

        data = parse_json(resp, context=f"europepmc doi:{doi}")
        results = _parse_results(data)
        return results[0] if results else None

    async def lookup_by_pmid(self, pmid: str) -> Optional[dict]:
        """Fetch ePMC record by PMID.

        Returns a normalised candidate dict, or None.
        """
        params = {
            "query": f"EXT_ID:{pmid} SRC:MED",
            "format": "json",
            "pageSize": 1,
            "resultType": "core",
        }
        logger.debug("EuropePMC verify pmid=%s", pmid)
        try:
            resp = await get_with_retry(
                self._client,
                _BASE,
                params=params,
                headers={"User-Agent": _USER_AGENT},
            )
        except httpx.HTTPError as exc:
            logger.debug("EuropePMC lookup_by_pmid failed: %r", exc)
            return None

        data = parse_json(resp, context=f"europepmc pmid:{pmid}")
        results = _parse_results(data)
        return results[0] if results else None

    async def _lookup_by_nbk_id(
        self, nbk_id: str, ref_id: str
    ) -> Optional[dict]:
        """Direct lookup by NCBI Bookshelf ID (e.g. NBK586169)."""
        params = {
            "query": f"BOOK_ID:{nbk_id}",
            "format": "json",
            "pageSize": 1,
            "resultType": "core",
        }
        logger.debug("EuropePMC [%s]: NBK lookup %s", ref_id, nbk_id)
        try:
            resp = await get_with_retry(
                self._client,
                _BASE,
                params=params,
                headers={"User-Agent": _USER_AGENT},
            )
        except httpx.HTTPError as exc:
            logger.debug("EuropePMC NBK lookup failed: %r", exc)
            return None

        data = parse_json(resp, context=f"europepmc nbk {nbk_id}")
        results = _parse_results(data)
        return results[0] if results else None


def _parse_results(data: Optional[dict]) -> list[dict]:
    """Extract and normalise the result list from a Europe PMC response."""
    if data is None:
        return []
    return [
        _normalise(r)
        for r in data.get("resultList", {}).get("result", [])
    ]


def _sanitise(s: str) -> str:
    """Strip characters that break Lucene phrase queries."""
    s = re.sub(r'["\\]', " ", s)
    s = s.replace(".", "")
    return re.sub(r"\s+", " ", s).strip()


def _clean_title(s: str) -> str:
    """Strip inline HTML markup, trailing full stop, and normalise whitespace
    in article titles."""
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s.rstrip(".")


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

    journal = (result.get("journalInfo") or {}).get("journal") or {}
    source = (
        journal.get("title", "")
        or result.get("journalTitle", "")
        or (result.get("bookOrReportDetails") or {}).get("publisher", "")
    )
    short_source = (
        journal.get("medlineAbbreviation", "")
        or journal.get("isoabbreviation", "")
    )

    return {
        "doi": doi,
        "pmid": pmid,
        "title": _clean_title(result.get("title", "")),
        "first_author": first_author,
        "year": str(result.get("pubYear", "")),
        "source": source,
        "short_source": short_source,
        "pages": result.get("pageInfo", ""),
        "api_score": 0.0,  # Europe PMC does not expose a relevance score
        "epmc_source": result.get("source", ""),  # e.g. "MED", "PPR"
    }
