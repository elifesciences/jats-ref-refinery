"""Core enrichment.

Pipeline:
  1. Parse structured fields from <ref> via xml_handler
  2. If neither DOI nor PMID known: query Europe PMC -> CrossRef -> DataCite
  3. If one of DOI/PMID known: resolve the other via OpenAlex
  4. No confident match: leave ref unchanged
"""

import asyncio
import logging
from typing import Optional

import httpx

from app.cache import get_cache
from app.resolvers.crossref import CrossRefResolver
from app.resolvers.datacite import DataCiteResolver
from app.resolvers.europepmc import EuropePMCResolver
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
        europepmc = EuropePMCResolver(client)
        openalex = OpenAlexResolver(client)
        cache = get_cache()
        # Limit concurrent API requests
        semaphore = asyncio.Semaphore(3)

        tasks = [
            _enrich_ref(
                ref, crossref, datacite, europepmc, openalex, cache, semaphore
            )
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
    europepmc: EuropePMCResolver,
    openalex: OpenAlexResolver,
    cache,
    semaphore: asyncio.Semaphore,
) -> Optional[dict]:
    """Return an enrichment dict for a single ref, or None.

    Steps:
      1. If neither DOI nor PMID: query Europe PMC -> CrossRef -> DataCite
      2. If exactly one of DOI/PMID is known: resolve the other via OpenAlex
    """
    doi = ref.existing_doi
    pmid = ref.existing_pmid
    source: Optional[str] = None

    # Step 1: bibliographic lookup when we have no PIDs
    if not doi and not pmid:
        result = await _lookup_doi(
            ref, crossref, datacite, europepmc, cache, semaphore
        )
        if result:
            doi = result["doi"]
            pmid = result.get("pmid", "")
            source = result["source"]

    # Step 2: use OpenAlex to fill in whichever ID is missing
    if (doi and not pmid) or (pmid and not doi):
        result = await _lookup_via_openalex(
            doi, pmid, openalex, cache, semaphore
        )
        if result:
            doi = doi or result.get("doi", "")
            pmid = pmid or result.get("pmid", "")

    new_doi = doi if doi != ref.existing_doi else None
    new_pmid = pmid if pmid != ref.existing_pmid else None

    if not new_doi and not new_pmid:
        return None

    return {"doi": new_doi, "pmid": new_pmid, "source": source}


async def _lookup_via_openalex(
    doi: str,
    pmid: str,
    openalex: OpenAlexResolver,
    cache,
    semaphore: asyncio.Semaphore,
) -> Optional[dict]:
    """Resolve a missing DOI from a known PMID, or vice versa."""
    if doi and not pmid:
        cache_key = f"pmid|{doi}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        async with semaphore:
            pmid = await openalex.lookup_pmid(doi)
            if not pmid:
                return None
            result = {"pmid": pmid}
            cache.set(cache_key, result)
            return result

    if pmid and not doi:
        cache_key = f"doi|pmid:{pmid}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        async with semaphore:
            doi = await openalex.lookup_doi(pmid)
            if not doi:
                return None
            result = {"doi": doi}
            cache.set(cache_key, result)
            return result

    return None


async def _lookup_doi(
    ref: RefFields,
    crossref: CrossRefResolver,
    datacite: DataCiteResolver,
    europepmc: EuropePMCResolver,
    cache,
    semaphore: asyncio.Semaphore,
) -> Optional[dict]:
    """Return a dict with a confirmed DOI (and PMID if available), or None."""
    async with semaphore:
        cache_key = (
            f"biblio|{ref.title.lower()}|{ref.first_author.lower()}|{ref.year}"
        )
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        # Europe PMC — returns DOI and PMID together
        epmc_candidates = await europepmc.lookup(ref)
        if epmc_candidates:
            best = max(epmc_candidates, key=lambda c: score_match(ref, c))
            score = score_match(ref, best)
            logger.debug(
                "EuropePMC [%s]: best score=%.3f doi=%s title=%r"
                " (%d candidates)",
                ref.ref_id, score,
                best.get("doi"), best.get("title"), len(epmc_candidates),
            )
            if score >= HIGH_CONFIDENCE_THRESHOLD:
                enrichment = {
                    "doi": best["doi"],
                    "pmid": best.get("pmid", ""),
                    "source": "europepmc",
                }
                cache.set(cache_key, enrichment)
                return enrichment
        else:
            logger.debug("EuropePMC [%s]: no results returned", ref.ref_id)

        # CrossRef fallback
        source = ref.source if ref.source != ref.title else ""
        query = " ".join(filter(None, [ref.title, source, ref.year]))
        logger.debug(
            "CrossRef [%s]: querying %r author=%r",
            ref.ref_id, query, ref.first_author,
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
        dc_candidates = await datacite.lookup(ref)
        if dc_candidates:
            best = max(dc_candidates, key=lambda c: score_match(ref, c))
            score = score_match(ref, best)
            logger.debug(
                "DataCite [%s]: best score=%.3f doi=%s title=%r"
                " (%d candidates)",
                ref.ref_id, score,
                best.get("doi"), best.get("title"), len(dc_candidates),
            )
            if score >= HIGH_CONFIDENCE_THRESHOLD:
                enrichment = {"doi": best["doi"], "source": "datacite"}
                cache.set(cache_key, enrichment)
                return enrichment
        else:
            logger.debug("DataCite [%s]: no results returned", ref.ref_id)

        return None
