"""OpenAlex API client for PMID/DOI lookups.

Set OPENALEX_API_KEY for authenticated (higher rate limit) access.
If unset, requests are made without authentication.
"""

import logging
import os
import re

import httpx

from app.http_utils import get_with_retry, parse_json

logger = logging.getLogger(__name__)

_BASE = "https://api.openalex.org/works"
_USER_AGENT = "jats-ref-refinery/0.1"
_API_KEY = os.getenv("OPENALEX_API_KEY")


class OpenAlexResolver:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def lookup_pmid(self, doi: str) -> str:
        """Return the PMID for a DOI, or empty string if not found."""
        url = f"{_BASE}/doi:{doi}"
        params = {"api_key": _API_KEY} if _API_KEY else {}
        try:
            resp = await get_with_retry(
                self._client,
                url,
                params=params,
                headers={"User-Agent": _USER_AGENT},
            )
        except httpx.HTTPError:
            logger.debug("OpenAlex: no result for DOI %s", doi)
            return ""

        data = parse_json(resp, context=f"openalex doi:{doi}")
        if data is None:
            return ""
        pmid_url = data.get("ids", {}).get("pmid", "")
        if not pmid_url:
            return ""

        # pmid_url format: "https://pubmed.ncbi.nlm.nih.gov/26294353"
        match = re.search(r"/(\d+)/?$", str(pmid_url))
        if match:
            logger.debug(
                "OpenAlex: doi=%s → pmid=%s", doi, match.group(1)
            )
            return match.group(1)
        return ""

    async def lookup_doi(self, pmid: str) -> str:
        """Return the DOI for a PMID, or empty string if not found."""
        url = f"{_BASE}/pmid:{pmid}"
        params = {"api_key": _API_KEY} if _API_KEY else {}
        try:
            resp = await get_with_retry(
                self._client,
                url,
                params=params,
                headers={"User-Agent": _USER_AGENT},
            )
        except httpx.HTTPError:
            logger.debug("OpenAlex: no result for PMID %s", pmid)
            return ""

        data = parse_json(resp, context=f"openalex pmid:{pmid}")
        if data is None:
            return ""
        doi = data.get("doi", "")
        if doi:
            # doi format: "https://doi.org/10.1234/example"
            doi = re.sub(r"^https?://doi\.org/", "", doi)
            logger.debug("OpenAlex: pmid=%s → doi=%s", pmid, doi)
        return doi
