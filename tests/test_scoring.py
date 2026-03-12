"""Tests for confidence scoring."""

import pytest

from app.scoring import score_match, HIGH_CONFIDENCE_THRESHOLD
from app.xml_handler import RefFields


def _make_ref(**kwargs) -> RefFields:
    defaults = dict(
        element=None, ref_id="r1", title="", first_author="",
        year="", source="", volume="", pages="",
    )
    defaults.update(kwargs)
    return RefFields(**defaults)


def test_perfect_match_exceeds_threshold():
    ref = _make_ref(
        title="A study of something", first_author="Smith",
        year="2020", source="eLife",
    )
    candidate = {
        "title": "A study of something", "first_author": "Smith",
        "year": "2020", "source": "eLife", "api_score": 150.0,
    }
    assert score_match(ref, candidate) >= HIGH_CONFIDENCE_THRESHOLD


def test_no_match_below_threshold():
    ref = _make_ref(
        title="Something completely different", first_author="Jones",
        year="2018", source="eLife",
    )
    candidate = {
        "title": "Unrelated work on other topics", "first_author": "Brown",
        "year": "2016", "source": "Cell", "api_score": 0.0,
    }
    assert score_match(ref, candidate) < HIGH_CONFIDENCE_THRESHOLD
