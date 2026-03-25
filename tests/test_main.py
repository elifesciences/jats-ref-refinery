"""Integration tests for the /enrich HTTP endpoint."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

_FIXTURE = Path(__file__).parent / "fixtures" / "sample.xml"
_VALID_XML = _FIXTURE.read_bytes()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


# --- /enrich happy path ---

def test_enrich_returns_xml_on_valid_input(client):
    enriched = b"<article/>"
    with patch("app.main.enrich_jats", new=AsyncMock(return_value=enriched)):
        response = client.post(
            "/enrich",
            content=_VALID_XML,
            headers={"Content-Type": "application/xml"},
        )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert response.content == enriched


# --- /enrich error handling ---

def test_enrich_returns_422_on_malformed_xml(client):
    response = client.post(
        "/enrich",
        content=b"this is not valid xml <<<",
        headers={"Content-Type": "application/xml"},
    )
    assert response.status_code == 422
    assert "Invalid or unparseable XML" in response.text


def test_enrich_returns_422_on_empty_body(client):
    response = client.post(
        "/enrich",
        content=b"",
        headers={"Content-Type": "application/xml"},
    )
    assert response.status_code == 422
    assert "Invalid or unparseable XML" in response.text


def test_enrich_returns_422_on_truncated_xml(client):
    response = client.post(
        "/enrich",
        content=b"<?xml version='1.0'?><article><back>",
        headers={"Content-Type": "application/xml"},
    )
    assert response.status_code == 422


# --- /health and /ready ---

def test_health(client):
    assert client.get("/health").status_code == 200


def test_ready(client):
    assert client.get("/ready").status_code == 200
