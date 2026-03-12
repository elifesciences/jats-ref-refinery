"""Tests for the Europe PMC resolver."""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.resolvers.europepmc import EuropePMCResolver, _normalise
from app.xml_handler import RefFields

_PATCH = "app.resolvers.europepmc.get_with_retry"


def _make_ref(**kwargs) -> RefFields:
    defaults = dict(
        element=None, ref_id="r1", title="", first_author="",
        year="", source="", volume="", pages="", nbk_id="",
    )
    defaults.update(kwargs)
    return RefFields(**defaults)


def _epmc_response(*results: dict) -> httpx.Response:
    body = json.dumps({"resultList": {"result": list(results)}})
    return httpx.Response(200, text=body)


def _epmc_result(**kwargs) -> dict:
    """Minimal Europe PMC result dict."""
    defaults = {
        "title": "",
        "authorString": "",
        "pubYear": "",
        "doi": "",
        "pmid": None,
        "pageInfo": "",
        "journalTitle": "",
        "journal": {},
    }
    defaults.update(kwargs)
    return defaults


# --- _normalise unit tests ---

def test_normalise_extracts_doi_and_pmid():
    result = _normalise(_epmc_result(doi="10.1234/test", pmid=36375006))
    assert result["doi"] == "10.1234/test"
    assert result["pmid"] == "36375006"


def test_normalise_extracts_surname_from_author_string():
    result = _normalise(_epmc_result(authorString="Li Q, Xie Y, Cui Z"))
    assert result["first_author"] == "Li"


def test_normalise_uses_journal_object_over_journalTitle():
    result = _normalise(_epmc_result(
        journalTitle="Dev cell",
        journal={
            "title": "Developmental Cell",
            "medlineAbbreviation": "Dev Cell",
        },
    ))
    assert result["source"] == "Developmental Cell"
    assert result["short_source"] == "Dev Cell"


def test_normalise_falls_back_to_journalTitle():
    result = _normalise(_epmc_result(journalTitle="eLife", journal={}))
    assert result["source"] == "eLife"


def test_normalise_uses_isoabbreviation_fallback():
    result = _normalise(_epmc_result(
        journal={
            "title": "Developmental Cell",
            "isoabbreviation": "Dev Cell",
        },
    ))
    assert result["short_source"] == "Dev Cell"


def test_normalise_missing_pmid_gives_empty_string():
    result = _normalise(_epmc_result(pmid=None))
    assert result["pmid"] == ""


# --- EuropePMCResolver integration tests ---

@pytest.mark.anyio
async def test_lookup_returns_candidates():
    mock_resp = _epmc_response(_epmc_result(
        title="Myrf transcription factor study",
        authorString="Meng J, Ma X",
        pubYear="2017",
        doi="10.1016/j.devcel.2017.03.022",
        pmid=28399394,
        journal={
            "title": "Developmental Cell",
            "medlineAbbreviation": "Dev Cell",
        },
    ))
    with patch(_PATCH, new=AsyncMock(return_value=mock_resp)):
        async with httpx.AsyncClient() as client:
            resolver = EuropePMCResolver(client)
            ref = _make_ref(
                title="Myrf transcription factor study",
                first_author="Meng", year="2017",
                source="Developmental Cell",
            )
            results = await resolver.lookup(ref)

    assert len(results) == 1
    assert results[0]["doi"] == "10.1016/j.devcel.2017.03.022"
    assert results[0]["pmid"] == "28399394"
    assert results[0]["source"] == "Developmental Cell"
    assert results[0]["short_source"] == "Dev Cell"


@pytest.mark.anyio
async def test_lookup_empty_title_returns_empty():
    with patch(_PATCH, new=AsyncMock()) as mock:
        async with httpx.AsyncClient() as client:
            resolver = EuropePMCResolver(client)
            results = await resolver.lookup(_make_ref())
    assert results == []
    mock.assert_not_called()


@pytest.mark.anyio
async def test_lookup_http_error_returns_empty():
    with patch(_PATCH, side_effect=httpx.ConnectError("failed")):
        async with httpx.AsyncClient() as client:
            resolver = EuropePMCResolver(client)
            ref = _make_ref(title="Some title", year="2020")
            results = await resolver.lookup(ref)
    assert results == []


@pytest.mark.anyio
async def test_lookup_empty_response_returns_empty():
    with patch(_PATCH, new=AsyncMock(return_value=_epmc_response())):
        async with httpx.AsyncClient() as client:
            resolver = EuropePMCResolver(client)
            ref = _make_ref(title="Some title", year="2020")
            results = await resolver.lookup(ref)
    assert results == []


@pytest.mark.anyio
async def test_nbk_lookup_sets_exact_match_flag():
    mock_resp = _epmc_response(_epmc_result(
        title="MYRF-Related Cardiac Urogenital Syndrome",
        pmid=36375006,
    ))
    with patch(_PATCH, new=AsyncMock(return_value=mock_resp)):
        async with httpx.AsyncClient() as client:
            resolver = EuropePMCResolver(client)
            ref = _make_ref(
                title="MYRF-Related Cardiac Urogenital Syndrome",
                nbk_id="NBK586169",
            )
            results = await resolver.lookup(ref)

    assert len(results) == 1
    assert results[0]["exact_match"] is True
    assert results[0]["pmid"] == "36375006"


@pytest.mark.anyio
async def test_nbk_lookup_empty_falls_through_to_title_search():
    """If NBK lookup returns nothing, fall through to title search."""
    nbk_empty = _epmc_response()
    title_hit = _epmc_response(_epmc_result(
        title="MYRF-Related Cardiac Urogenital Syndrome",
        pmid=36375006,
    ))
    with patch(
        _PATCH,
        new=AsyncMock(side_effect=[nbk_empty, title_hit]),
    ):
        async with httpx.AsyncClient() as client:
            resolver = EuropePMCResolver(client)
            ref = _make_ref(
                title="MYRF-Related Cardiac Urogenital Syndrome",
                nbk_id="NBK586169",
            )
            results = await resolver.lookup(ref)

    assert len(results) == 1
    assert results[0].get("exact_match") is not True
