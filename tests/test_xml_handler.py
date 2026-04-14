"""Tests for JATS XML parsing and output."""

from pathlib import Path

from app.types import EnrichmentResult
from app.xml_handler import build_enriched_xml, parse_refs

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
    assert refs[0].title_from_source is True


def test_title_from_source_false_when_article_title_present():
    refs, _ = _parse("""<article>
      <back><ref-list>
        <ref id="r1">
          <element-citation>
            <article-title>Explicit Title</article-title>
            <source>Journal Name</source>
          </element-citation>
        </ref>
      </ref-list></back>
    </article>""")
    assert refs[0].title == "Explicit Title"
    assert refs[0].title_from_source is False


def test_title_from_source_false_when_no_source_either():
    refs, _ = _parse("""<article>
      <back><ref-list>
        <ref id="r1">
          <element-citation/>
        </ref>
      </ref-list></back>
    </article>""")
    assert refs[0].title_from_source is False


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
    refs[0].enrichment = EnrichmentResult(
        doi="10.1234/test", resolver="crossref"
    )

    result = build_enriched_xml(tree, refs)
    assert b"10.1234/test" in result


def test_build_enriched_xml_adds_pmid():
    raw = FIXTURE.read_bytes()
    refs, tree = parse_refs(raw)
    refs[0].enrichment = EnrichmentResult(
        pmid="36375006", resolver="europepmc"
    )

    result = build_enriched_xml(tree, refs)
    assert b"36375006" in result
    assert b'pub-id-type="pmid"' in result


def test_publication_type_parsed_from_element_citation():
    refs, _ = _parse("""<article>
      <back><ref-list>
        <ref id="r1">
          <element-citation publication-type="software">
            <article-title>My Tool</article-title>
          </element-citation>
        </ref>
      </ref-list></back>
    </article>""")
    assert refs[0].publication_type == "software"


def test_publication_type_parsed_from_mixed_citation():
    refs, _ = _parse("""<article>
      <back><ref-list>
        <ref id="r1">
          <mixed-citation publication-type="data">
            <article-title>My Dataset</article-title>
          </mixed-citation>
        </ref>
      </ref-list></back>
    </article>""")
    assert refs[0].publication_type == "data"


def test_publication_type_normalised_to_lowercase():
    refs, _ = _parse("""<article>
      <back><ref-list>
        <ref id="r1">
          <element-citation publication-type="Software">
            <article-title>My Tool</article-title>
          </element-citation>
        </ref>
      </ref-list></back>
    </article>""")
    assert refs[0].publication_type == "software"


def test_publication_type_absent_is_empty_string():
    refs, _ = _parse("""<article>
      <back><ref-list>
        <ref id="r1">
          <element-citation>
            <article-title>My Article</article-title>
          </element-citation>
        </ref>
      </ref-list></back>
    </article>""")
    assert refs[0].publication_type == ""


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
    refs[0].enrichment = EnrichmentResult(
        doi="10.1234/new", resolver="crossref"
    )

    result = build_enriched_xml(tree, refs)
    assert b"conflicts with existing DOI" in result
    assert b"10.1234/new" in result


def test_build_enriched_xml_inserts_article_title_before_source():
    """Scenario B: source is journal name, article-title was absent."""
    refs, tree = _parse("""<article>
      <back><ref-list>
        <ref id="r1">
          <element-citation>
            <source>Clin Cancer Res</source>
            <volume>10</volume>
            <fpage>2299</fpage>
          </element-citation>
        </ref>
      </ref-list></back>
    </article>""")
    refs[0].enrichment = EnrichmentResult(
        doi="10.1158/1078-0432.ccr-03-0488",
        resolver="europepmc",
        article_title_to_add="Hypoxia and tumour progression",
    )

    result = build_enriched_xml(tree, refs)
    assert (
        b"<article-title>Hypoxia and tumour progression</article-title>"
        in result
    )
    assert b"<source>Clin Cancer Res</source>" in result
    # article-title must appear before source
    assert result.index(b"<article-title>") < result.index(b"<source>")


def test_build_enriched_xml_renames_source_to_article_title():
    """Scenario A: source contains the article title (mis-tagged)."""
    refs, tree = _parse("""<article>
      <back><ref-list>
        <ref id="r1">
          <element-citation>
            <source>Hypoxia and tumour progression</source>
          </element-citation>
        </ref>
      </ref-list></back>
    </article>""")
    refs[0].enrichment = EnrichmentResult(
        doi="10.1158/1078-0432.ccr-03-0488",
        resolver="europepmc",
        journal_name_to_add="Clinical Cancer Research",
    )

    result = build_enriched_xml(tree, refs)
    assert (
        b"<article-title>Hypoxia and tumour progression</article-title>"
        in result
    )
    assert b"<source>Clinical Cancer Research</source>" in result


def test_build_enriched_xml_suspect_doi_adds_comment_only():
    refs, tree = _parse("""<article>
      <back><ref-list>
        <ref id="r1">
          <element-citation>
            <article-title>Title</article-title>
            <pub-id pub-id-type="doi">10.9999/suspect</pub-id>
          </element-citation>
        </ref>
      </ref-list></back>
    </article>""")
    refs[0].enrichment = EnrichmentResult(suspect_doi="10.1234/correct")

    result = build_enriched_xml(tree, refs)
    assert b"existing DOI may be incorrect" in result
    assert b"10.1234/correct" in result
    # Must not insert a new pub-id for the suggested DOI
    assert result.count(b'pub-id-type="doi"') == 1


def test_build_enriched_xml_inserts_source_when_absent():
    """Scenario C: article-title present but <source> entirely absent."""
    refs, tree = _parse("""<article>
      <back><ref-list>
        <ref id="r1">
          <element-citation>
            <article-title>Tumour vasculature</article-title>
            <volume>10</volume>
            <fpage>100</fpage>
          </element-citation>
        </ref>
      </ref-list></back>
    </article>""")
    refs[0].enrichment = EnrichmentResult(
        pmid="12345678",
        resolver="europepmc",
        source_to_add="Nature Medicine",
    )

    result = build_enriched_xml(tree, refs)
    assert b"<source>Nature Medicine</source>" in result
    assert b"<article-title>Tumour vasculature</article-title>" in result
    # <source> must appear after <article-title>
    assert result.index(b"<article-title>") < result.index(b"<source>")


def test_build_enriched_xml_source_to_add_skipped_when_source_exists():
    """source_to_add must not fire when a <source> element is present."""
    refs, tree = _parse("""<article>
      <back><ref-list>
        <ref id="r1">
          <element-citation>
            <article-title>Title</article-title>
            <source>Existing Journal</source>
          </element-citation>
        </ref>
      </ref-list></back>
    </article>""")
    refs[0].enrichment = EnrichmentResult(
        pmid="12345678",
        source_to_add="Should Not Appear",
    )

    result = build_enriched_xml(tree, refs)
    assert b"Should Not Appear" not in result
    assert b"<source>Existing Journal</source>" in result


def test_build_enriched_xml_suspect_pmid_adds_comment_only():
    refs, tree = _parse("""<article>
      <back><ref-list>
        <ref id="r1">
          <element-citation>
            <article-title>Title</article-title>
            <pub-id pub-id-type="pmid">99999</pub-id>
          </element-citation>
        </ref>
      </ref-list></back>
    </article>""")
    refs[0].enrichment = EnrichmentResult(suspect_pmid="11111")

    result = build_enriched_xml(tree, refs)
    assert b"existing PMID may be incorrect" in result
    assert b"11111" in result
    assert result.count(b'pub-id-type="pmid"') == 1
