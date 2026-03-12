"""Tests for JATS XML parsing and output."""

from pathlib import Path

from app.xml_handler import parse_refs, build_enriched_xml

FIXTURE = Path(__file__).parent / "fixtures" / "sample.xml"


def _parse(xml: str):
    return parse_refs(xml.encode())


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
    assert r.first_author == "Smith"
    assert r.year == "2020"
    assert r.source == "eLife"
    assert r.pages == "e58580"


def test_elocation_id_used_when_no_fpage():
    refs, _ = _parse("""<article>
      <back><ref-list>
        <ref id="r1">
          <element-citation>
            <article-title>Title</article-title>
            <elocation-id>e12345</elocation-id>
          </element-citation>
        </ref>
      </ref-list></back>
    </article>""")
    assert refs[0].pages == "e12345"


def test_chapter_title_used_for_book_refs():
    refs, _ = _parse("""<article>
      <back><ref-list>
        <ref id="r1">
          <element-citation publication-type="book">
            <chapter-title>Introduction</chapter-title>
            <source>Against Method</source>
          </element-citation>
        </ref>
      </ref-list></back>
    </article>""")
    assert refs[0].title == "Introduction"
    assert refs[0].source == "Against Method"


def test_source_used_as_title_fallback():
    refs, _ = _parse("""<article>
      <back><ref-list>
        <ref id="r1">
          <element-citation>
            <source>Only A Source</source>
          </element-citation>
        </ref>
      </ref-list></back>
    </article>""")
    assert refs[0].title == "Only A Source"


def test_existing_doi_and_pmid_parsed():
    refs, _ = _parse("""<article>
      <back><ref-list>
        <ref id="r1">
          <element-citation>
            <article-title>Title</article-title>
            <pub-id pub-id-type="doi">10.1234/perma-existing</pub-id>
            <pub-id pub-id-type="pmid">12345678</pub-id>
          </element-citation>
        </ref>
      </ref-list></back>
    </article>""")
    assert refs[0].existing_doi == "10.1234/perma-existing"
    assert refs[0].existing_pmid == "12345678"


def test_nbk_id_extracted_from_ext_link():
    refs, _ = _parse("""<article xmlns:xlink="http://www.w3.org/1999/xlink">
      <back><ref-list>
        <ref id="r1">
          <mixed-citation>
            <article-title>Title</article-title>
            <ext-link ext-link-type="uri"
              xlink:href="https://www.ncbi.nlm.nih.gov/books/NBK586169/">
              NBK586169
            </ext-link>
          </mixed-citation>
        </ref>
      </ref-list></back>
    </article>""")
    assert refs[0].nbk_id == "NBK586169"


def test_italic_text_included_in_title():
    refs, _ = _parse("""<article>
      <back><ref-list>
        <ref id="r1">
          <element-citation>
            <article-title><italic>C. elegans</italic> biology</article-title>
          </element-citation>
        </ref>
      </ref-list></back>
    </article>""")
    assert refs[0].title == "C. elegans biology"


def test_build_enriched_xml_adds_doi():
    raw = FIXTURE.read_bytes()
    refs, tree = parse_refs(raw)
    refs[0].enrichment = {"doi": "10.1234/test", "source": "crossref"}

    result = build_enriched_xml(tree, refs)
    assert b"10.1234/test" in result


def test_build_enriched_xml_adds_pmid():
    raw = FIXTURE.read_bytes()
    refs, tree = parse_refs(raw)
    refs[0].enrichment = {
        "doi": None, "pmid": "36375006", "source": "europepmc"
    }

    result = build_enriched_xml(tree, refs)
    assert b"36375006" in result
    assert b'pub-id-type="pmid"' in result


def test_build_enriched_xml_skips_unmatched_refs():
    raw = FIXTURE.read_bytes()
    refs, tree = parse_refs(raw)

    result = build_enriched_xml(tree, refs)
    assert b"pub-id" not in result


def test_build_enriched_xml_conflict_comment():
    refs, tree = _parse("""<article>
      <back><ref-list>
        <ref id="r1">
          <element-citation>
            <article-title>Title</article-title>
            <pub-id pub-id-type="doi">10.9999/old</pub-id>
          </element-citation>
        </ref>
      </ref-list></back>
    </article>""")
    refs[0].enrichment = {"doi": "10.1234/new", "source": "crossref"}

    result = build_enriched_xml(tree, refs)
    assert b"conflicts with existing DOI" in result
    assert b"10.1234/new" in result
