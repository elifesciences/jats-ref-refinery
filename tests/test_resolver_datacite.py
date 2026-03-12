"""Tests for the DataCite resolver."""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.resolvers.datacite import DataCiteResolver, _normalise
from app.xml_handler import RefFields

_PATCH = "app.resolvers.datacite.get_with_retry"


def _make_ref(**kwargs) -> RefFields:
    defaults = dict(
        element=None, ref_id="r1", title="", first_author="",
        year="", source="", volume="", pages="", nbk_id="",
    )
    defaults.update(kwargs)
    return RefFields(**defaults)


def _datacite_response(*items: dict) -> httpx.Response:
    body = json.dumps({"data": list(items)})
    return httpx.Response(200, text=body)


def _datacite_item(**attrs) -> dict:
    """Minimal DataCite item dict with attributes."""
    defaults = {
        "doi": "",
        "titles": [],
        "creators": [],
        "publicationYear": None,
        "publisher": "",
    }
    defaults.update(attrs)
    return {"attributes": defaults}


# --- _normalise unit tests ---

def test_normalise_extracts_doi_and_title():
    item = _datacite_item(
        doi="10.5281/zenodo.1234",
        titles=[{"title": "A dataset about something"}],
    )
    result = _normalise(item)
    assert result["doi"] == "10.5281/zenodo.1234"
    assert result["title"] == "A dataset about something"


def test_normalise_extracts_family_name():
    item = _datacite_item(
        creators=[{"familyName": "Jones", "givenName": "A"}],
    )
    result = _normalise(item)
    assert result["first_author"] == "Jones"


def test_normalise_extracts_year():
    item = _datacite_item(publicationYear=2021)
    result = _normalise(item)
    assert result["year"] == "2021"


def test_normalise_uses_publisher_as_source():
    item = _datacite_item(publisher="Zenodo")
    result = _normalise(item)
    assert result["source"] == "Zenodo"


def test_normalise_empty_creators_gives_empty_author():
    item = _datacite_item(creators=[])
    result = _normalise(item)
    assert result["first_author"] == ""


def test_normalise_empty_titles_gives_empty_title():
    item = _datacite_item(titles=[])
    result = _normalise(item)
    assert result["title"] == ""


def test_normalise_api_score_is_zero():
    result = _normalise(_datacite_item())
    assert result["api_score"] == 0.0


# --- DataCiteResolver integration tests ---

@pytest.mark.anyio
async def test_lookup_returns_candidates():
    mock_resp = _datacite_response(_datacite_item(
        doi="10.5281/zenodo.1234",
        titles=[{"title": "A dataset about something"}],
        creators=[{"familyName": "Jones"}],
        publicationYear=2021,
        publisher="Zenodo",
    ))
    with patch(_PATCH, new=AsyncMock(return_value=mock_resp)):
        async with httpx.AsyncClient() as client:
            resolver = DataCiteResolver(client)
            ref = _make_ref(
                title="A dataset about something",
                first_author="Jones", year="2021",
            )
            results = await resolver.lookup(ref)

    assert len(results) == 1
    assert results[0]["doi"] == "10.5281/zenodo.1234"
    assert results[0]["source"] == "Zenodo"


@pytest.mark.anyio
async def test_lookup_empty_title_returns_empty():
    with patch(_PATCH, new=AsyncMock()) as mock:
        async with httpx.AsyncClient() as client:
            resolver = DataCiteResolver(client)
            results = await resolver.lookup(_make_ref())
    assert results == []
    mock.assert_not_called()


@pytest.mark.anyio
async def test_lookup_http_error_returns_empty():
    with patch(_PATCH, side_effect=httpx.ConnectError("failed")):
        async with httpx.AsyncClient() as client:
            resolver = DataCiteResolver(client)
            ref = _make_ref(title="Some title", year="2021")
            results = await resolver.lookup(ref)
    assert results == []


@pytest.mark.anyio
async def test_lookup_empty_response_returns_empty():
    with patch(_PATCH, new=AsyncMock(return_value=_datacite_response())):
        async with httpx.AsyncClient() as client:
            resolver = DataCiteResolver(client)
            ref = _make_ref(title="Some title", year="2021")
            results = await resolver.lookup(ref)
    assert results == []


@pytest.mark.anyio
async def test_lookup_multiple_candidates_returned():
    mock_resp = _datacite_response(
        _datacite_item(
            doi="10.5281/zenodo.001",
            titles=[{"title": "First result"}],
        ),
        _datacite_item(
            doi="10.5281/zenodo.002",
            titles=[{"title": "Second result"}],
        ),
    )
    with patch(_PATCH, new=AsyncMock(return_value=mock_resp)):
        async with httpx.AsyncClient() as client:
            resolver = DataCiteResolver(client)
            ref = _make_ref(title="First result", year="2021")
            results = await resolver.lookup(ref)

    assert len(results) == 2
    assert results[0]["doi"] == "10.5281/zenodo.001"
    assert results[1]["doi"] == "10.5281/zenodo.002"
