"""Tests for the Europe PMC resolver."""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.resolvers.europepmc import EuropePMCResolver, _normalise, _clean_title
from app.types import RefFields

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


def test_clean_title_strips_html_tags():
    assert _clean_title("The <i>Drosophila</i> clock") == "The Drosophila clock"


def test_clean_title_normalises_whitespace():
    assert _clean_title("word  extra   space") == "word extra space"


def test_clean_title_strips_mixed_tags():
    assert _clean_title("<b>Bold</b> and <i>italic</i>") == "Bold and italic"


def test_normalise_strips_html_from_title():
    result = _normalise(_epmc_result(title="<i>C. elegans</i> biology"))
    assert result["title"] == "C. elegans biology"


@pytest.mark.anyio
async def test_lookup_by_journal_returns_candidates():
    mock_resp = _epmc_response(_epmc_result(
        title="Hypoxia and tumour progression",
        authorString="Yokoi K, Fidler IJ",
        pubYear="2004",
        doi="10.1158/1078-0432.ccr-03-0488",
        pmid=15073106,
        journal={"title": "Clinical Cancer Research",
                 "medlineAbbreviation": "Clin Cancer Res"},
    ))
    with patch(_PATCH, new=AsyncMock(return_value=mock_resp)):
        async with httpx.AsyncClient() as client:
            resolver = EuropePMCResolver(client)
            ref = _make_ref(
                title="Clin Cancer Res", source="Clin Cancer Res",
                first_author="Yokoi", year="2004", volume="10",
                title_from_source=True,
            )
            results = await resolver.lookup_by_journal(ref)

    assert len(results) == 1
    assert results[0]["doi"] == "10.1158/1078-0432.ccr-03-0488"
    assert results[0]["title"] == "Hypoxia and tumour progression"


@pytest.mark.anyio
async def test_lookup_by_journal_empty_source_returns_empty():
    with patch(_PATCH, new=AsyncMock()) as mock:
        async with httpx.AsyncClient() as client:
            resolver = EuropePMCResolver(client)
            results = await resolver.lookup_by_journal(_make_ref())
    assert results == []
    mock.assert_not_called()


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


# --- lookup_by_doi / lookup_by_pmid ---

@pytest.mark.anyio
async def test_lookup_by_doi_returns_candidate():
    mock_resp = _epmc_response(_epmc_result(
        doi="10.7554/elife.89482", pmid=12345678,
        title="A great paper",
    ))
    with patch(_PATCH, new=AsyncMock(return_value=mock_resp)):
        async with httpx.AsyncClient() as client:
            resolver = EuropePMCResolver(client)
            result = await resolver.lookup_by_doi("10.7554/elife.89482")

    assert result is not None
    assert result["doi"] == "10.7554/elife.89482"
    assert result["pmid"] == "12345678"


@pytest.mark.anyio
async def test_lookup_by_doi_returns_none_when_no_results():
    with patch(_PATCH, new=AsyncMock(return_value=_epmc_response())):
        async with httpx.AsyncClient() as client:
            resolver = EuropePMCResolver(client)
            result = await resolver.lookup_by_doi("10.9999/notfound")
    assert result is None


@pytest.mark.anyio
async def test_lookup_by_doi_returns_none_on_http_error():
    with patch(_PATCH, side_effect=httpx.ConnectError("failed")):
        async with httpx.AsyncClient() as client:
            resolver = EuropePMCResolver(client)
            result = await resolver.lookup_by_doi("10.1234/foo")
    assert result is None


@pytest.mark.anyio
async def test_lookup_by_pmid_returns_candidate():
    mock_resp = _epmc_response(_epmc_result(
        doi="10.7554/elife.89482", pmid=12345678,
        title="A great paper",
    ))
    with patch(_PATCH, new=AsyncMock(return_value=mock_resp)):
        async with httpx.AsyncClient() as client:
            resolver = EuropePMCResolver(client)
            result = await resolver.lookup_by_pmid("12345678")

    assert result is not None
    assert result["pmid"] == "12345678"
    assert result["doi"] == "10.7554/elife.89482"


@pytest.mark.anyio
async def test_lookup_by_pmid_returns_none_when_no_results():
    with patch(_PATCH, new=AsyncMock(return_value=_epmc_response())):
        async with httpx.AsyncClient() as client:
            resolver = EuropePMCResolver(client)
            result = await resolver.lookup_by_pmid("99999999")
    assert result is None
