"""Tests for enricher helpers."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.enricher import (
    _build_suspect_enrichment,
    _doi_version_match,
    _lookup_doi,
)
from app.types import RefFields


def _make_ref(**kwargs) -> RefFields:
    defaults = dict(
        element=None, ref_id="r1", title="", first_author="",
        year="", source="", volume="", pages="", nbk_id="",
    )
    defaults.update(kwargs)
    return RefFields(**defaults)


# --- _doi_version_match ---

def test_doi_version_match_elife_different_versions():
    assert _doi_version_match(
        "10.7554/elife.89482.2", "10.7554/elife.89482.3"
    )


def test_doi_version_match_f1000_different_versions():
    assert _doi_version_match(
        "10.12688/f1000research.12345.1", "10.12688/f1000research.12345.2"
    )


def test_doi_version_match_elife_same_version():
    # Same version — not a version mismatch, but still returns True
    # (base DOIs are equal after stripping)
    assert _doi_version_match(
        "10.7554/elife.89482.1", "10.7554/elife.89482.1"
    )


def test_doi_version_match_elife_different_articles():
    assert not _doi_version_match(
        "10.7554/elife.11111.1", "10.7554/elife.22222.1"
    )


def test_doi_version_match_non_versioned_publisher():
    assert not _doi_version_match(
        "10.1234/foo.1", "10.1234/foo.2"
    )


def test_doi_version_match_mixed_publishers():
    assert not _doi_version_match(
        "10.7554/elife.89482.1", "10.12688/f1000research.89482.1"
    )


def test_doi_version_match_case_insensitive():
    assert _doi_version_match(
        "10.7554/eLife.89482.2", "10.7554/elife.89482.3"
    )


# --- _build_suspect_enrichment ---

def test_build_suspect_enrichment_flags_wrong_doi():
    ref = _make_ref(existing_doi="10.9999/wrong", existing_pmid="")
    result = _build_suspect_enrichment(
        ref, {"doi": "10.1234/right", "pmid": ""}
    )
    assert result is not None
    assert result.suspect_doi == "10.1234/right"
    assert result.suspect_pmid == ""


def test_build_suspect_enrichment_flags_wrong_pmid():
    ref = _make_ref(existing_doi="", existing_pmid="11111")
    result = _build_suspect_enrichment(ref, {"doi": "", "pmid": "22222"})
    assert result is not None
    assert result.suspect_pmid == "22222"
    assert result.suspect_doi == ""


def test_build_suspect_enrichment_returns_none_when_doi_matches():
    ref = _make_ref(existing_doi="10.1234/right", existing_pmid="")
    result = _build_suspect_enrichment(
        ref, {"doi": "10.1234/right", "pmid": ""}
    )
    assert result is None


def test_build_suspect_enrichment_returns_none_when_pmid_matches():
    ref = _make_ref(existing_doi="", existing_pmid="12345")
    result = _build_suspect_enrichment(ref, {"doi": "", "pmid": "12345"})
    assert result is None


def test_build_suspect_enrichment_ignores_elife_version_difference():
    ref = _make_ref(existing_doi="10.7554/elife.89482.2", existing_pmid="")
    result = _build_suspect_enrichment(
        ref, {"doi": "10.7554/elife.89482.3", "pmid": ""}
    )
    assert result is None


def test_build_suspect_enrichment_returns_none_when_no_existing_pids():
    # No existing PIDs means nothing to flag as suspect
    ref = _make_ref(existing_doi="", existing_pmid="")
    result = _build_suspect_enrichment(
        ref, {"doi": "10.1234/foo", "pmid": "999"}
    )
    assert result is None


# --- publication-type routing ---

def _make_resolvers(datacite_doi="10.5281/zenodo.123"):
    """Return (europepmc, crossref, datacite, cache, semaphore) mocks."""
    europepmc = MagicMock()
    europepmc.lookup = AsyncMock(return_value=[])
    europepmc.lookup_by_journal = AsyncMock(return_value=[])

    crossref = MagicMock()
    crossref.lookup = AsyncMock(return_value=[])

    datacite = MagicMock()
    datacite.lookup = AsyncMock(return_value=[
        {
            "doi": datacite_doi,
            "title": "My Software Tool",
            "first_author": "Jones",
            "year": "2022",
            "source": "",
        }
    ])

    cache = MagicMock()
    cache.get = MagicMock(return_value=None)
    cache.set = MagicMock()

    semaphore = asyncio.Semaphore(3)
    return europepmc, crossref, datacite, cache, semaphore


@pytest.mark.asyncio
async def test_software_publication_type_skips_epmc_and_crossref():
    ref = _make_ref(
        title="My Software Tool",
        first_author="Jones",
        year="2022",
        publication_type="software",
    )
    epmc, cr, dc, cache, sem = _make_resolvers()

    with patch("app.scoring.score_match", return_value=0.9):
        await _lookup_doi(ref, cr, dc, epmc, cache, sem)

    epmc.lookup.assert_not_called()
    epmc.lookup_by_journal.assert_not_called()
    cr.lookup.assert_not_called()
    dc.lookup.assert_called_once()


@pytest.mark.asyncio
async def test_data_publication_type_skips_epmc_and_crossref():
    ref = _make_ref(
        title="My Dataset",
        first_author="Jones",
        year="2022",
        publication_type="data",
    )
    epmc, cr, dc, cache, sem = _make_resolvers(
        datacite_doi="10.5281/zenodo.456"
    )

    with patch("app.scoring.score_match", return_value=0.9):
        await _lookup_doi(ref, cr, dc, epmc, cache, sem)

    epmc.lookup.assert_not_called()
    cr.lookup.assert_not_called()
    dc.lookup.assert_called_once()


@pytest.mark.asyncio
async def test_journal_publication_type_uses_full_pipeline():
    ref = _make_ref(
        title="Some Article",
        first_author="Jones",
        year="2022",
        publication_type="journal",
    )
    epmc, cr, dc, cache, sem = _make_resolvers()

    with patch("app.scoring.score_match", return_value=0.0):
        await _lookup_doi(ref, cr, dc, epmc, cache, sem)

    epmc.lookup.assert_called_once()
    cr.lookup.assert_called_once()


@pytest.mark.asyncio
async def test_lookup_doi_populates_source_to_add_when_source_missing():
    """When ref has no <source> and EPMC returns a journal name, source_to_add
    is set on the enrichment dict."""
    ref = _make_ref(
        title="Tumour vasculature",
        first_author="Smith",
        year="2020",
        source="",  # no journal name in the XML
    )
    epmc, cr, dc, cache, sem = _make_resolvers()
    epmc.lookup = AsyncMock(return_value=[
        {
            "doi": "10.1038/nm.1234",
            "pmid": "12345678",
            "title": "Tumour vasculature",
            "first_author": "Smith",
            "year": "2020",
            "source": "Nature Medicine",
            "short_source": "Nat Med",
            "pages": "100",
            "api_score": 0.0,
            "epmc_source": "MED",
        }
    ])

    with patch("app.scoring.score_match", return_value=0.9):
        result = await _lookup_doi(ref, cr, dc, epmc, cache, sem)

    assert result is not None
    assert result["source_to_add"] == "Nature Medicine"


@pytest.mark.asyncio
async def test_lookup_doi_no_source_to_add_when_source_already_present():
    """When ref already has a <source>, source_to_add must not be set."""
    ref = _make_ref(
        title="Tumour vasculature",
        first_author="Smith",
        year="2020",
        source="Nature Medicine",  # already present in XML
    )
    epmc, cr, dc, cache, sem = _make_resolvers()
    epmc.lookup = AsyncMock(return_value=[
        {
            "doi": "10.1038/nm.1234",
            "pmid": "12345678",
            "title": "Tumour vasculature",
            "first_author": "Smith",
            "year": "2020",
            "source": "Nature Medicine",
            "short_source": "Nat Med",
            "pages": "100",
            "api_score": 0.0,
            "epmc_source": "MED",
        }
    ])

    with patch("app.scoring.score_match", return_value=0.9):
        result = await _lookup_doi(ref, cr, dc, epmc, cache, sem)

    assert result is not None
    assert result.get("source_to_add", "") == ""
