"""Tests for the OpenAlex resolver."""

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.resolvers.openalex import OpenAlexResolver

_PATCH = "app.resolvers.openalex.get_with_retry"


def _openalex_response(data: dict) -> httpx.Response:
    return httpx.Response(200, text=json.dumps(data))


# --- lookup_pmid tests ---

@pytest.mark.anyio
async def test_lookup_pmid_extracts_pmid_from_url():
    resp = _openalex_response({
        "ids": {"pmid": "https://pubmed.ncbi.nlm.nih.gov/28399394"}
    })
    with patch(_PATCH, new=AsyncMock(return_value=resp)):
        async with httpx.AsyncClient() as client:
            resolver = OpenAlexResolver(client)
            pmid = await resolver.lookup_pmid("10.1016/j.devcel.2017.03.022")
    assert pmid == "28399394"


@pytest.mark.anyio
async def test_lookup_pmid_missing_ids_returns_empty():
    resp = _openalex_response({"ids": {}})
    with patch(_PATCH, new=AsyncMock(return_value=resp)):
        async with httpx.AsyncClient() as client:
            resolver = OpenAlexResolver(client)
            pmid = await resolver.lookup_pmid("10.1234/no-pmid")
    assert pmid == ""


@pytest.mark.anyio
async def test_lookup_pmid_http_error_returns_empty():
    with patch(_PATCH, side_effect=httpx.ConnectError("failed")):
        async with httpx.AsyncClient() as client:
            resolver = OpenAlexResolver(client)
            pmid = await resolver.lookup_pmid("10.1234/some-doi")
    assert pmid == ""


@pytest.mark.anyio
async def test_lookup_pmid_no_ids_key_returns_empty():
    resp = _openalex_response({})
    with patch(_PATCH, new=AsyncMock(return_value=resp)):
        async with httpx.AsyncClient() as client:
            resolver = OpenAlexResolver(client)
            pmid = await resolver.lookup_pmid("10.1234/no-ids")
    assert pmid == ""


# --- lookup_doi tests ---

@pytest.mark.anyio
async def test_lookup_doi_strips_prefix():
    resp = _openalex_response({
        "doi": "https://doi.org/10.1016/j.devcel.2017.03.022"
    })
    with patch(_PATCH, new=AsyncMock(return_value=resp)):
        async with httpx.AsyncClient() as client:
            resolver = OpenAlexResolver(client)
            doi = await resolver.lookup_doi("28399394")
    assert doi == "10.1016/j.devcel.2017.03.022"


@pytest.mark.anyio
async def test_lookup_doi_missing_doi_returns_empty():
    resp = _openalex_response({})
    with patch(_PATCH, new=AsyncMock(return_value=resp)):
        async with httpx.AsyncClient() as client:
            resolver = OpenAlexResolver(client)
            doi = await resolver.lookup_doi("12345678")
    assert doi == ""


@pytest.mark.anyio
async def test_lookup_doi_http_error_returns_empty():
    with patch(_PATCH, side_effect=httpx.ConnectError("failed")):
        async with httpx.AsyncClient() as client:
            resolver = OpenAlexResolver(client)
            doi = await resolver.lookup_doi("12345678")
    assert doi == ""
