"""Core enrichment.

Pipeline:
  1. Parse structured fields from <ref> via xml_handler
  2. Query CrossRef — if high confidence match: add DOI
  3. Query DataCite as fallback — if high confidence match: add DOI
  4. No confident match: leave ref unchanged
"""

import asyncio
import logging
from typing import Optional

import httpx

from app.cache import get_cache
from app.resolvers.crossref import CrossRefResolver
from app.resolvers.datacite import DataCiteResolver
from app.resolvers.openalex import OpenAlexResolver
from app.scoring import score_match, HIGH_CONFIDENCE_THRESHOLD
from app.xml_handler import parse_refs, build_enriched_xml, RefFields

logging.basicConfig(level=logging.DEBUG)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def enrich_jats(raw_xml: bytes) -> bytes:
    """Parse JATS XML, enrich each <ref> with a DOI and PMID where possible."""
    refs, tree = parse_refs(raw_xml)

    async with httpx.AsyncClient(timeout=5.0) as client:
        crossref = CrossRefResolver(client)
        datacite = DataCiteResolver(client)
        openalex = OpenAlexResolver(client)
        cache = get_cache()
        # Limit concurrent API requests
        semaphore = asyncio.Semaphore(5)

        tasks = [
            _enrich_ref(ref, crossref, datacite, openalex, cache, semaphore)
            for ref in refs
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for ref, result in zip(refs, results):
            if isinstance(result, Exception):
                logger.error(
                    "Failed to enrich ref %s: %s", ref.ref_id, result
                )
                continue
            ref.enrichment = result

    return build_enriched_xml(tree, refs)


async def _enrich_ref(
    ref: RefFields,
    crossref: CrossRefResolver,
    datacite: DataCiteResolver,
    openalex: OpenAlexResolver,
    cache,
    semaphore: asyncio.Semaphore,
) -> Optional[dict]:
    """Return an enrichment dict for a single ref, or None.

    Steps (each skipped if the information is already known):
      1. PMID -> DOI via OpenAlex (if ref has a PMID but no DOI)
      2. DOI via CrossRef, then DataCite (if no DOI)
      3. DOI -> PMID via OpenAlex (if now has a DOI but no PMID)
    """
    doi = ref.existing_doi
    pmid = ref.existing_pmid
    source: Optional[str] = None

    # Step 1: resolve DOI from existing PMID
    if pmid and not doi:
        result = await _lookup_doi_from_pmid(pmid, openalex, cache, semaphore)
        if result:
            doi = result["doi"]
            source = result["source"]

    # Step 2: resolve DOI via CrossRef / DataCite
    if not doi:
        result = await _lookup_doi(ref, crossref, datacite, cache, semaphore)
        if result:
            doi = result["doi"]
            source = result["source"]

    # Step 3: resolve PMID from DOI
    if doi and not pmid:
        result = await _lookup_pmid(doi, openalex, cache, semaphore)
        if result:
            pmid = result["pmid"]

    new_doi = doi if doi != ref.existing_doi else None
    new_pmid = pmid if pmid != ref.existing_pmid else None

    if not new_doi and not new_pmid:
        return None

    return {"doi": new_doi, "pmid": new_pmid, "source": source}


async def _lookup_doi_from_pmid(
    pmid: str,
    openalex: OpenAlexResolver,
    cache,
    semaphore: asyncio.Semaphore,
) -> Optional[dict]:
    cache_key = f"doi|pmid:{pmid}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    async with semaphore:
        doi = await openalex.lookup_doi(pmid)
        if not doi:
            return None
        result = {"doi": doi, "source": "openalex"}
        cache.set(cache_key, result)
        return result


async def _lookup_pmid(
    doi: str,
    openalex: OpenAlexResolver,
    cache,
    semaphore: asyncio.Semaphore,
) -> Optional[dict]:
    cache_key = f"pmid|{doi}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    async with semaphore:
        pmid = await openalex.lookup_pmid(doi)
        if not pmid:
            return None
        result = {"pmid": pmid, "source": "openalex"}
        cache.set(cache_key, result)
        return result


async def _lookup_doi(
    ref: RefFields,
    crossref: CrossRefResolver,
    datacite: DataCiteResolver,
    cache,
    semaphore: asyncio.Semaphore,
) -> Optional[dict]:
    """Return a dict with a confirmed DOI, or None if no confident match."""
    async with semaphore:
        cache_key = f"{ref.title}|{ref.first_author}|{ref.year}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        # CrossRef — score all candidates, pick the best
        query = " ".join(filter(None, [ref.title, ref.source]))
        logger.debug(
            "CrossRef [%s]: querying %r author=%r year=%r",
            ref.ref_id, query, ref.first_author, ref.year,
        )
        cr_candidates = await crossref.lookup(ref)
        if cr_candidates:
            best = max(cr_candidates, key=lambda c: score_match(ref, c))
            score = score_match(ref, best)
            logger.debug(
                "CrossRef [%s]: best score=%.3f doi=%s title=%r"
                " (%d candidates)",
                ref.ref_id, score,
                best.get("doi"), best.get("title"), len(cr_candidates),
            )
            if score >= HIGH_CONFIDENCE_THRESHOLD:
                enrichment = {"doi": best["doi"], "source": "crossref"}
                cache.set(cache_key, enrichment)
                return enrichment
        else:
            logger.debug("CrossRef [%s]: no results returned", ref.ref_id)

        # DataCite fallback
        dc_result = await datacite.lookup(ref)
        if dc_result:
            score = score_match(ref, dc_result)
            logger.debug(
                "DataCite [%s]: score=%.3f doi=%s title=%r",
                ref.ref_id, score,
                dc_result.get("doi"), dc_result.get("title"),
            )
            if score >= HIGH_CONFIDENCE_THRESHOLD:
                enrichment = {"doi": dc_result["doi"], "source": "datacite"}
                cache.set(cache_key, enrichment)
                return enrichment
        else:
            logger.debug("DataCite [%s]: no result returned", ref.ref_id)

        return None
