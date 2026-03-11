"""Tests for confidence scoring."""

import pytest

from app.scoring import score_match, HIGH_CONFIDENCE_THRESHOLD
from app.xml_handler import RefFields


def _make_ref(**kwargs) -> RefFields:
    defaults = dict(element=None, ref_id="r1", title="", first_author="", year="", journal="", volume="", pages="")
    defaults.update(kwargs)
    return RefFields(**defaults)


def test_perfect_match_exceeds_threshold():
    ref = _make_ref(title="A study of something", first_author="Smith J", year="2020", journal="Nature")
    candidate = {"title": "A study of something", "first_author": "Smith J", "year": "2020", "journal": "Nature", "api_score": 150.0}
    assert score_match(ref, candidate) >= HIGH_CONFIDENCE_THRESHOLD


def test_no_match_below_threshold():
    ref = _make_ref(title="Something completely different", first_author="Jones A", year="2021", journal="Science")
    candidate = {"title": "Unrelated work on other topics", "first_author": "Brown B", "year": "2019", "journal": "Cell", "api_score": 0.0}
    assert score_match(ref, candidate) < HIGH_CONFIDENCE_THRESHOLD
