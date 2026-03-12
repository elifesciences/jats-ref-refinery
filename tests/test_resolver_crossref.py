"""Tests for the CrossRef resolver."""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.resolvers.crossref import (
    CrossRefResolver,
    _normalise,
    _sanitise_query,
)
from app.xml_handler import RefFields

_PATCH = "app.resolvers.crossref.get_with_retry"


def _make_ref(**kwargs) -> RefFields:
    defaults = dict(
        element=None, ref_id="r1", title="", first_author="",
        year="", source="", volume="", pages="", nbk_id="",
    )
    defaults.update(kwargs)
    return RefFields(**defaults)


def _crossref_response(*items: dict) -> httpx.Response:
    body = json.dumps({"message": {"items": list(items)}})
    return httpx.Response(200, text=body)


def _crossref_item(**kwargs) -> dict:
    """Minimal CrossRef item dict."""
    defaults = {
        "DOI": "",
        "title": [],
        "author": [],
        "container-title": [],
        "short-container-title": [],
        "issued": {"date-parts": [[None]]},
        "page": "",
        "score": 100.0,
    }
    defaults.update(kwargs)
    return defaults


# --- _sanitise_query unit tests ---

def test_sanitise_removes_lucene_chars():
    assert _sanitise_query("foo (bar) [baz]") == "foo  bar   baz "


def test_sanitise_removes_colon():
    assert ":" not in _sanitise_query("Hif-1: a study")


def test_sanitise_leaves_plain_text_unchanged():
    result = _sanitise_query("A simple title 2020")
    assert result == "A simple title 2020"


# --- _normalise unit tests ---

def test_normalise_extracts_doi_and_title():
    item = _crossref_item(
        **{"DOI": "10.1234/test", "title": ["A Study"]},
    )
    result = _normalise(item)
    assert result["doi"] == "10.1234/test"
    assert result["title"] == "A Study"


def test_normalise_extracts_first_author_family_name():
    item = _crossref_item(
        author=[{"family": "Smith", "given": "John"}],
    )
    result = _normalise(item)
    assert result["first_author"] == "Smith"


def test_normalise_extracts_year_from_date_parts():
    item = _crossref_item(
        issued={"date-parts": [[2020, 1, 15]]},
    )
    result = _normalise(item)
    assert result["year"] == "2020"


def test_normalise_empty_year_when_no_date():
    item = _crossref_item(issued={"date-parts": [[None]]})
    result = _normalise(item)
    assert result["year"] == ""


def test_normalise_uses_container_title_as_source():
    item = _crossref_item(**{
        "container-title": ["eLife"],
        "short-container-title": ["eLife"],
    })
    result = _normalise(item)
    assert result["source"] == "eLife"
    assert result["short_source"] == "eLife"


def test_normalise_falls_back_to_institution_name():
    item = _crossref_item(institution=[{"name": "bioRxiv"}])
    result = _normalise(item)
    assert result["source"] == "bioRxiv"


def test_normalise_returns_api_score():
    item = _crossref_item(score=142.5)
    result = _normalise(item)
    assert result["api_score"] == 142.5


# --- CrossRefResolver integration tests ---

@pytest.mark.anyio
async def test_lookup_returns_candidates():
    mock_resp = _crossref_response(_crossref_item(
        **{
            "DOI": "10.7554/eLife.58580",
            "title": ["A study of something important"],
            "author": [{"family": "Smith", "given": "J"}],
            "container-title": ["eLife"],
            "short-container-title": ["eLife"],
            "issued": {"date-parts": [[2020]]},
            "score": 150.0,
        }
    ))
    with patch(_PATCH, new=AsyncMock(return_value=mock_resp)):
        async with httpx.AsyncClient() as client:
            resolver = CrossRefResolver(client)
            ref = _make_ref(
                title="A study of something important",
                first_author="Smith", year="2020", source="eLife",
            )
            results = await resolver.lookup(ref)

    assert len(results) == 1
    assert results[0]["doi"] == "10.7554/eLife.58580"
    assert results[0]["source"] == "eLife"
    assert results[0]["api_score"] == 150.0


@pytest.mark.anyio
async def test_lookup_empty_title_returns_empty():
    with patch(_PATCH, new=AsyncMock()) as mock:
        async with httpx.AsyncClient() as client:
            resolver = CrossRefResolver(client)
            results = await resolver.lookup(_make_ref())
    assert results == []
    mock.assert_not_called()


@pytest.mark.anyio
async def test_lookup_http_error_returns_empty():
    with patch(_PATCH, side_effect=httpx.ConnectError("failed")):
        async with httpx.AsyncClient() as client:
            resolver = CrossRefResolver(client)
            ref = _make_ref(title="Some title", year="2020")
            results = await resolver.lookup(ref)
    assert results == []


@pytest.mark.anyio
async def test_lookup_empty_response_returns_empty():
    with patch(_PATCH, new=AsyncMock(return_value=_crossref_response())):
        async with httpx.AsyncClient() as client:
            resolver = CrossRefResolver(client)
            ref = _make_ref(title="Some title", year="2020")
            results = await resolver.lookup(ref)
    assert results == []


@pytest.mark.anyio
async def test_lookup_filters_low_score_candidates():
    """Items below _MIN_CANDIDATE_SCORE are excluded when higher exist."""
    high = _crossref_item(
        **{"DOI": "10.1234/high", "title": ["High score"], "score": 150.0}
    )
    low = _crossref_item(
        **{"DOI": "10.1234/low", "title": ["Low score"], "score": 5.0}
    )
    mock_resp = _crossref_response(high, low)
    with patch(_PATCH, new=AsyncMock(return_value=mock_resp)):
        async with httpx.AsyncClient() as client:
            resolver = CrossRefResolver(client)
            ref = _make_ref(title="High score", year="2020")
            results = await resolver.lookup(ref)

    dois = [r["doi"] for r in results]
    assert "10.1234/high" in dois
    assert "10.1234/low" not in dois


@pytest.mark.anyio
async def test_lookup_returns_all_when_all_below_min_score():
    """If every item is below _MIN_CANDIDATE_SCORE, return all anyway."""
    item = _crossref_item(
        **{"DOI": "10.1234/only", "title": ["Only result"], "score": 5.0}
    )
    mock_resp = _crossref_response(item)
    with patch(_PATCH, new=AsyncMock(return_value=mock_resp)):
        async with httpx.AsyncClient() as client:
            resolver = CrossRefResolver(client)
            ref = _make_ref(title="Only result", year="2020")
            results = await resolver.lookup(ref)

    assert len(results) == 1
    assert results[0]["doi"] == "10.1234/only"
