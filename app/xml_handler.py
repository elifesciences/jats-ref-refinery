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

    Parsed fields (title, first_author, year, source, volume, pages) are used
    for validation scoring.
    """

    element: Any  # lxml element — the <ref> node
    ref_id: str
    title: str
    first_author: str
    year: str
    source: str
    volume: str
    pages: str
    existing_doi: str = ""   # DOI already present in the input XML, if any
    existing_pmid: str = ""  # PMID already present in the input XML, if any
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
    title = _text("article-title") or _text("chapter-title") or source
    year = _parse_year(_text("year"))
    volume = _text("volume")
    pages = _text("fpage") or _text("elocation-id")

    # First author: first <name> or <string-name> in author person-group
    first_author = ""
    if citation is not None:
        pg = citation.find(".//person-group[@person-group-type='author']")
        node = pg if pg is not None else citation
        name_el = node.find("name") or node.find("string-name")
        if name_el is not None:
            surname = name_el.findtext("surname") or ""
            given = name_el.findtext("given-names") or ""
            if surname or given:
                first_author = f"{surname} {given}".strip()
            else:
                first_author = (name_el.text or "").strip()

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
        existing_doi=existing_doi,
        existing_pmid=existing_pmid,
    )


def build_enriched_xml(tree: Any, refs: list[RefFields]) -> bytes:
    """Add <pub-id> for confirmed DOIs/PMIDs."""
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

    doctype = tree.docinfo.doctype
    out = BytesIO()
    tree.write(out, encoding="UTF-8", xml_declaration=True, doctype=doctype)
    return out.getvalue()
