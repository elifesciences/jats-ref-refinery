"""JATS XML parsing and output manipulation using lxml."""

from __future__ import annotations

import dataclasses
import re
from io import BytesIO
from typing import Any, Optional

from lxml import etree


@dataclasses.dataclass
class RefFields:
    """Structured fields extracted from a single <ref> element.

    Parsed fields are used for candidate scoring.  title_from_source is set
    when no <article-title> was present and title was derived from <source>;
    this changes both lookup strategy and scoring behaviour.
    """

    element: Any  # lxml element — the <ref> node
    ref_id: str
    title: str
    first_author: str
    year: str
    source: str
    volume: str
    pages: str
    title_from_source: bool = False  # True when no <article-title> and title == source
    existing_doi: str = ""   # DOI already present in the input XML, if any
    existing_pmid: str = ""  # PMID already present in the input XML, if any
    nbk_id: str = ""         # NCBI Bookshelf ID from ext-link, if present
    enrichment: Optional[dict] = None  # populated by enricher after lookup


def parse_refs(raw_xml: bytes) -> tuple[list[RefFields], Any]:
    """Parse JATS XML and extract structured fields from every <ref> element.

    Returns:
        refs: list of RefFields (one per <ref>)
        tree: lxml ElementTree for the full document
    """
    parser = etree.XMLParser(
        remove_blank_text=False, resolve_entities=False, load_dtd=False
    )
    tree = etree.parse(BytesIO(raw_xml), parser)

    refs: list[RefFields] = []
    for ref_el in tree.getroot().iter("ref"):
        refs.append(_extract_ref_fields(ref_el))

    return refs, tree


def _parse_year(raw: str) -> str:
    """Strip non-digits and return the year if >= 1900, else empty string."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 4 and int(digits) >= 1900:
        return digits
    return ""


def _extract_ref_fields(ref_el: Any) -> RefFields:
    citation = ref_el.find(".//mixed-citation")
    if citation is None:
        citation = ref_el.find(".//element-citation")

    def _text(tag: str) -> str:
        el = citation.find(f".//{tag}") if citation is not None else None
        if el is None:
            return ""
        return "".join(el.itertext()).strip()

    source = _text("source")
    raw_title = _text("article-title") or _text("chapter-title")
    title = raw_title or source
    title_from_source = not bool(raw_title) and bool(source)
    year = _parse_year(_text("year"))
    volume = _text("volume")
    pages = _text("fpage") or _text("elocation-id")

    # First author: first <name> or <string-name> in author person-group
    first_author = ""
    if citation is not None:
        pg = citation.find(".//person-group[@person-group-type='author']")
        node = pg if pg is not None else citation
        name_el = node.find("name")
        if name_el is None:
            name_el = node.find("string-name")
        if name_el is not None:
            surname = name_el.findtext("surname") or ""
            if surname:
                first_author = surname
            else:
                # Untagged string-name fallback: take first token as surname
                first_author = ((name_el.text or "").strip().split()[0:1]
                                or [""])[0]

    # NCBI Bookshelf ID from ext-link, if present
    nbk_id = ""
    for link_el in ref_el.iter("ext-link"):
        href = (
            link_el.get("{http://www.w3.org/1999/xlink}href", "")
            or link_el.get("href", "")
        )
        match = re.search(r"ncbi\.nlm\.nih\.gov/books/(NBK\d+)", href)
        if match:
            nbk_id = match.group(1)
            break

    # Existing pub-ids in the input, if any
    existing_doi = ""
    existing_pmid = ""
    for pub_id_el in ref_el.iter("pub-id"):
        id_type = pub_id_el.get("pub-id-type", "")
        if id_type == "doi" and not existing_doi:
            existing_doi = (pub_id_el.text or "").strip().lower()
        elif id_type == "pmid" and not existing_pmid:
            existing_pmid = (pub_id_el.text or "").strip()

    return RefFields(
        element=ref_el,
        ref_id=ref_el.get("id", ""),
        title=title,
        first_author=first_author,
        year=year,
        source=source,
        volume=volume,
        pages=pages,
        title_from_source=title_from_source,
        existing_doi=existing_doi,
        existing_pmid=existing_pmid,
        nbk_id=nbk_id,
    )


def build_enriched_xml(tree: Any, refs: list[RefFields]) -> bytes:
    """Write enrichment results back into the XML tree and serialise.

    For each ref with enrichment data:
      - Inserts <pub-id pub-id-type="doi"> and/or <pub-id pub-id-type="pmid">
      - If journal_name_to_add is set: renames the existing <source> element to
        <article-title> and inserts a new <source> with the correct journal name
      - If article_title_to_add is set: inserts a new <article-title> element
        before the existing <source>
    """
    for ref in refs:
        if not ref.enrichment:
            continue

        doi = ref.enrichment.get("doi")
        pmid = ref.enrichment.get("pmid")

        if not doi and not pmid:
            continue

        citation = ref.element.find(".//mixed-citation")
        if citation is None:
            citation = ref.element.find(".//element-citation")
        if citation is None:
            continue

        # Insert new DOI with comment for conflict
        if doi and not (ref.existing_doi and ref.existing_doi == doi.lower()):
            prev = citation[-1] if len(citation) else None
            if prev is not None:
                prev.tail = (prev.tail or "") + " "
            if ref.existing_doi:
                citation.append(
                    etree.Comment(
                        f" refinery: conflicts with existing DOI"
                        f" {ref.existing_doi} "
                    )
                )
            pub_id = etree.SubElement(citation, "pub-id")
            pub_id.set("pub-id-type", "doi")
            pub_id.text = doi

        # Insert PMID after DOI, if not in the input
        if pmid and not ref.existing_pmid:
            prev = citation[-1] if len(citation) else None
            if prev is not None:
                prev.tail = (prev.tail or "") + " "
            pmid_el = etree.SubElement(citation, "pub-id")
            pmid_el.set("pub-id-type", "pmid")
            pmid_el.text = pmid

        # Fix tag structure when the original had no <article-title>.
        # journal_name_to_add  → <source> was actually the article title
        #                         (mis-tagged): rename it and add the real
        #                         journal name.
        # article_title_to_add → <source> is correct; article title was just
        #                         absent: insert it from the matched candidate.
        journal_name = ref.enrichment.get("journal_name_to_add", "")
        article_title = ref.enrichment.get("article_title_to_add", "")
        source_el = citation.find("source")

        if journal_name and source_el is not None:
            source_el.tag = "article-title"
            new_source = etree.Element("source")
            new_source.text = journal_name
            citation.insert(list(citation).index(source_el) + 1, new_source)
        elif article_title and source_el is not None:
            new_title = etree.Element("article-title")
            new_title.text = article_title
            new_title.tail = ", "
            citation.insert(list(citation).index(source_el), new_title)

    doctype = tree.docinfo.doctype
    out = BytesIO()
    tree.write(out, encoding="UTF-8", xml_declaration=True, doctype=doctype)
    return out.getvalue()
