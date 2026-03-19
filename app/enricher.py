"""Core enrichment.

Pipeline:
  1. Parse structured fields from <ref> via xml_handler
  2a. If DOI or PMID already present: fetch by PID and score against ref fields
      - High confidence: verified; resolve any missing PID
      - Low confidence: run bibliographic lookup (step 2b) and flag differences
        as XML comments
  2b. If neither DOI nor PMID known: query Europe PMC -> CrossRef -> DataCite
  3. If one of DOI/PMID known: resolve the other via OpenAlex
  4. No confident match: leave ref unchanged
"""

import asyncio
import logging
import re
from typing import Optional

import httpx

from app.cache import get_cache
from app.resolvers.crossref import CrossRefResolver
from app.resolvers.datacite import DataCiteResolver
from app.resolvers.europepmc import EuropePMCResolver
from app.resolvers.openalex import OpenAlexResolver
from app.scoring import score_match, HIGH_CONFIDENCE_THRESHOLD
from app.types import EnrichmentResult, RefFields
from app.xml_handler import parse_refs, build_enriched_xml

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
        semaphore = asyncio.Semaphore(3)  # Limit concurrent API requests

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
) -> Optional[EnrichmentResult]:
    """Return an EnrichmentResult for a single ref, or None.

    Steps:
      2a. Existing PID: fetch by PID, score against ref fields.
          Verified: resolve missing PID.
          Suspect: bibliographic lookup, flag differences as
            suspect_doi / suspect_pmid (XML comment only).
      2b. No PIDs: bibliographic lookup via Europe PMC -> CrossRef -> DataCite.
      3.  One PID known: resolve the other via OpenAlex.
    """
    doi = ref.existing_doi
    pmid = ref.existing_pmid
    lookup_result: Optional[dict] = None

    if doi or pmid:
        # Step 2a: verify existing PID via direct lookup
        candidate = await _fetch_by_pid(
            doi, pmid, europepmc, crossref, datacite, cache, semaphore
        )
        score = score_match(ref, candidate) if candidate else 0.0
        logger.debug(
            "PID verify [%s]: score=%.3f doi=%r pmid=%r",
            ref.ref_id, score, doi, pmid,
        )

        if score < HIGH_CONFIDENCE_THRESHOLD:
            # Suspect — run bibliographic pipeline and flag any differences
            lookup_result = await _lookup_doi(
                ref, crossref, datacite, europepmc, cache, semaphore
            )
            if not lookup_result:
                return None
            return _build_suspect_enrichment(ref, lookup_result)

        # Verified — resolve any missing PID
        enrichment = EnrichmentResult()

        # Cross-check PMID from candidate for free (DOI lookup returns both)
        if doi and pmid and candidate:
            candidate_pmid = candidate.get("pmid", "")
            if candidate_pmid and candidate_pmid != pmid:
                enrichment.suspect_pmid = candidate_pmid

        if doi and not pmid:
            new_pmid = (candidate.get("pmid", "") if candidate else "")
            if not new_pmid:
                oa = await _lookup_via_openalex(
                    doi, "", openalex, cache, semaphore
                )
                new_pmid = (oa or {}).get("pmid", "")
            enrichment.pmid = new_pmid

        if pmid and not doi:
            # Candidate from EPMC lookup_by_pmid includes the DOI
            new_doi = (candidate.get("doi", "") if candidate else "")
            if not new_doi:
                oa = await _lookup_via_openalex(
                    "", pmid, openalex, cache, semaphore
                )
                new_doi = (oa or {}).get("doi", "")
            enrichment.doi = new_doi

        if not any([
            enrichment.doi, enrichment.pmid,
            enrichment.suspect_doi, enrichment.suspect_pmid,
        ]):
            return None
        return enrichment

    # Step 2b: no existing PIDs — bibliographic lookup
    lookup_result = await _lookup_doi(
        ref, crossref, datacite, europepmc, cache, semaphore
    )
    if lookup_result:
        doi = lookup_result["doi"] or ""
        pmid = lookup_result.get("pmid", "")

    # Step 3: use OpenAlex to fill in whichever ID is missing
    if (doi and not pmid) or (pmid and not doi):
        oa_result = await _lookup_via_openalex(
            doi, pmid, openalex, cache, semaphore
        )
        if oa_result:
            doi = doi or oa_result.get("doi", "")
            pmid = pmid or oa_result.get("pmid", "")

    new_doi = doi if doi != ref.existing_doi else ""
    new_pmid = pmid if pmid != ref.existing_pmid else ""

    if not new_doi and not new_pmid:
        return None

    enrichment = EnrichmentResult(doi=new_doi, pmid=new_pmid)
    if lookup_result:
        enrichment.resolver = lookup_result.get("resolver", "")
        enrichment.article_title_to_add = lookup_result.get(
            "article_title_to_add", ""
        )
        enrichment.journal_name_to_add = lookup_result.get(
            "journal_name_to_add", ""
        )
    return enrichment


def _build_suspect_enrichment(
    ref: RefFields,
    lookup_result: dict,
) -> Optional[EnrichmentResult]:
    """Build an EnrichmentResult flagging existing PIDs as potentially wrong.

    Only flags PIDs that are present in the source XML and differ from what
    the bibliographic lookup found.  Returns None if nothing to flag.
    """
    enrichment = EnrichmentResult()
    suggested_doi = lookup_result.get("doi", "")
    suggested_pmid = lookup_result.get("pmid", "")

    if ref.existing_doi and suggested_doi and (
        suggested_doi.lower() != ref.existing_doi.lower()
        and not _doi_version_match(suggested_doi, ref.existing_doi)
    ):
        enrichment.suspect_doi = suggested_doi
    if ref.existing_pmid and suggested_pmid and (
        suggested_pmid != ref.existing_pmid
    ):
        enrichment.suspect_pmid = suggested_pmid

    if not enrichment.suspect_doi and not enrichment.suspect_pmid:
        return None
    return enrichment


def _doi_version_match(a: str, b: str) -> bool:
    """Return True if two DOIs refer to different versions of the same article.

    Applies to publishers that append a single-digit version suffix:
      - eLife (10.7554/)       e.g. 10.7554/elife.89482.2
      - F1000Research (10.12688/)  e.g. 10.12688/f1000research.12345.2
    """
    _VERSIONED_PREFIXES = ("10.7554/", "10.12688/")
    a, b = a.lower(), b.lower()
    if not any(a.startswith(p) for p in _VERSIONED_PREFIXES):
        return False
    if not any(b.startswith(p) for p in _VERSIONED_PREFIXES):
        return False
    return re.sub(r'\.\d$', '', a) == re.sub(r'\.\d$', '', b)


async def _fetch_by_pid(
    doi: str,
    pmid: str,
    europepmc: EuropePMCResolver,
    crossref: CrossRefResolver,
    datacite: DataCiteResolver,
    cache,
    semaphore: asyncio.Semaphore,
) -> Optional[dict]:
    """Fetch a normalised candidate dict for a known PID, or None."""
    cache_key = f"pid|doi:{doi}" if doi else f"pid|pmid:{pmid}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    async with semaphore:
        candidate: Optional[dict] = None
        if doi:
            candidate = await europepmc.lookup_by_doi(doi)
            if not candidate:
                candidate = await crossref.lookup_by_doi(doi)
            if not candidate:
                candidate = await datacite.lookup_by_doi(doi)
        elif pmid:
            candidate = await europepmc.lookup_by_pmid(pmid)

    if candidate:
        cache.set(cache_key, candidate)
    return candidate


def _best_epmc_candidate(
    ref: RefFields,
    title_candidates: list[dict],
    journal_candidates: list[dict],
) -> tuple[Optional[dict], bool]:
    """Return (best_candidate, source_was_title).

    Runs both candidate lists through scoring and picks the higher scorer.
    source_was_title=True means the TITLE: query won — the <source> value in
    the original XML is the article title, not the journal name.
    """
    title_best = (
        max(title_candidates, key=lambda c: score_match(ref, c))
        if title_candidates else None
    )
    journal_best = (
        max(journal_candidates, key=lambda c: score_match(ref, c))
        if journal_candidates else None
    )
    if title_best is None and journal_best is None:
        return None, False
    title_score = score_match(ref, title_best) if title_best else 0.0
    journal_score = score_match(ref, journal_best) if journal_best else 0.0
    if title_score > journal_score:
        return title_best, True
    return journal_best, False


def _build_epmc_enrichment(
    best: dict,
    ref: RefFields,
    source_was_title: bool,
) -> dict:
    """Build the enrichment dict for a Europe PMC match.

    When title_from_source is set, also include the tag-fix instruction:
      source_was_title=True: journal_name_to_add (rename <source>, add journal)
      source_was_title=False: article_title_to_add (insert missing title)
    """
    enrichment: dict = {
        "doi": best["doi"],
        "pmid": best.get("pmid", ""),
        "resolver": "europepmc",
    }
    if ref.title_from_source:
        if source_was_title:
            enrichment["journal_name_to_add"] = best.get("source", "")
        else:
            enrichment["article_title_to_add"] = best.get("title", "")
    return enrichment


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

        # Europe PMC — returns DOI and PMID together.
        # When title_from_source is set, also run a journal-based query and
        # pick whichever produces the higher-scoring candidate.
        epmc_title_candidates = await europepmc.lookup(ref)
        epmc_journal_candidates: list[dict] = []
        if ref.title_from_source:
            epmc_journal_candidates = await europepmc.lookup_by_journal(ref)

        epmc_best, source_was_title = _best_epmc_candidate(
            ref, epmc_title_candidates, epmc_journal_candidates
        )
        if epmc_best:
            if epmc_best.get("exact_match"):
                logger.debug(
                    "EuropePMC [%s]: exact NBK match doi=%s pmid=%s",
                    ref.ref_id, epmc_best.get("doi"), epmc_best.get("pmid"),
                )
                enrichment = _build_epmc_enrichment(
                    epmc_best, ref, source_was_title
                )
                cache.set(cache_key, enrichment)
                return enrichment
            score = score_match(ref, epmc_best)
            logger.debug(
                "EuropePMC [%s]: best score=%.3f doi=%s title=%r"
                " (%d title-candidates, %d journal-candidates)",
                ref.ref_id, score,
                epmc_best.get("doi"), epmc_best.get("title"),
                len(epmc_title_candidates), len(epmc_journal_candidates),
            )
            if score >= HIGH_CONFIDENCE_THRESHOLD:
                enrichment = _build_epmc_enrichment(
                    epmc_best, ref, source_was_title
                )
                cache.set(cache_key, enrichment)
                return enrichment
        else:
            logger.debug("EuropePMC [%s]: no results returned", ref.ref_id)

        # score_match cannot compare titles, CrossRef and DataCite results
        # cannot be reliably evaluated
        if ref.title_from_source:
            return None

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
                enrichment = {"doi": best["doi"], "resolver": "crossref"}
                if ref.title_from_source:
                    enrichment["article_title_to_add"] = best.get("title", "")
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
                enrichment = {"doi": best["doi"], "resolver": "datacite"}
                if ref.title_from_source:
                    enrichment["article_title_to_add"] = best.get("title", "")
                cache.set(cache_key, enrichment)
                return enrichment
        else:
            logger.debug("DataCite [%s]: no results returned", ref.ref_id)

        return None
