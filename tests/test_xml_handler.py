"""Tests for JATS XML parsing and output."""

from pathlib import Path

from app.xml_handler import parse_refs, build_enriched_xml

FIXTURE = Path(__file__).parent / "fixtures" / "sample.xml"


def test_parse_refs_finds_all_refs():
    raw = FIXTURE.read_bytes()
    refs, _tree = parse_refs(raw)
    assert len(refs) == 2


def test_parse_refs_extracts_fields():
    raw = FIXTURE.read_bytes()
    refs, _tree = parse_refs(raw)
    r = refs[0]
    assert r.ref_id == "r1"
    assert "important" in r.title.lower()
    assert r.first_author == "Smith J"
    assert r.year == "2020"
    assert r.journal == "Nature"


def test_build_enriched_xml_adds_doi():
    raw = FIXTURE.read_bytes()
    refs, tree = parse_refs(raw)
    refs[0].enrichment = {"doi": "10.1234/test", "source": "crossref"}

    result = build_enriched_xml(tree, refs)
    assert b"10.1234/test" in result


def test_build_enriched_xml_skips_unmatched_refs():
    raw = FIXTURE.read_bytes()
    refs, tree = parse_refs(raw)
    # No enrichment set on either ref

    result = build_enriched_xml(tree, refs)
    assert b"pub-id" not in result
