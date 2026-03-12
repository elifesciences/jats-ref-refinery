"""Tests for confidence scoring."""

from app.scoring import score_match, HIGH_CONFIDENCE_THRESHOLD
from app.xml_handler import RefFields


def _make_ref(**kwargs) -> RefFields:
    defaults = dict(
        element=None, ref_id="r1", title="", first_author="",
        year="", source="", volume="", pages="",
    )
    defaults.update(kwargs)
    return RefFields(**defaults)


def _make_candidate(**kwargs) -> dict:
    defaults = dict(
        title="", first_author="", year="", source="",
        short_source="", pages="", api_score=0.0,
    )
    defaults.update(kwargs)
    return defaults


def test_perfect_match_exceeds_threshold():
    ref = _make_ref(
        title="A study of something", first_author="Smith",
        year="2020", source="eLife",
    )
    candidate = _make_candidate(
        title="A study of something", first_author="Smith",
        year="2020", source="eLife", api_score=150.0,
    )
    assert score_match(ref, candidate) >= HIGH_CONFIDENCE_THRESHOLD


def test_no_match_below_threshold():
    ref = _make_ref(
        title="Something completely different", first_author="Jones",
        year="2018", source="eLife",
    )
    candidate = _make_candidate(
        title="Unrelated work on other topics", first_author="Brown",
        year="2016", source="Cell",
    )
    assert score_match(ref, candidate) < HIGH_CONFIDENCE_THRESHOLD


def test_different_title_fails_threshold():
    ref = _make_ref(
        title="Mechanisms of apoptosis in cancer cells",
        first_author="Smith", year="2020", source="eLife",
    )
    candidate = _make_candidate(
        title="Regulation of autophagy in neural tissue",
        first_author="Smith", year="2020", source="eLife",
    )
    assert score_match(ref, candidate) < HIGH_CONFIDENCE_THRESHOLD


def test_different_source_reduces_score():
    """Source mismatch lowers the score but a perfect title+author+year
    is sufficient to still exceed the threshold."""
    ref = _make_ref(
        title="A study of something important",
        first_author="Smith", year="2020", source="Nature",
    )
    candidate_match = _make_candidate(
        title="A study of something important",
        first_author="Smith", year="2020", source="Nature",
    )
    candidate_mismatch = _make_candidate(
        title="A study of something important",
        first_author="Smith", year="2020", source="Cell",
    )
    assert (
        score_match(ref, candidate_match)
        > score_match(ref, candidate_mismatch)
    )
    assert score_match(ref, candidate_mismatch) >= HIGH_CONFIDENCE_THRESHOLD


def test_missing_fields_excluded_from_score():
    """A ref with no year or source should still score well on title alone."""
    ref = _make_ref(title="A study of something", first_author="Smith")
    candidate = _make_candidate(
        title="A study of something", first_author="Smith",
        year="2020", source="eLife",
    )
    assert score_match(ref, candidate) >= HIGH_CONFIDENCE_THRESHOLD


def test_empty_ref_scores_zero():
    ref = _make_ref()
    candidate = _make_candidate(
        title="Some title", first_author="Smith", year="2020",
    )
    assert score_match(ref, candidate) == 0.0


def test_year_mismatch_reduces_score():
    ref = _make_ref(
        title="A study of something", first_author="Smith",
        year="2020", source="eLife",
    )
    candidate_correct = _make_candidate(
        title="A study of something", first_author="Smith",
        year="2020", source="eLife",
    )
    candidate_wrong_year = _make_candidate(
        title="A study of something", first_author="Smith",
        year="1999", source="eLife",
    )
    assert (
        score_match(ref, candidate_correct)
        > score_match(ref, candidate_wrong_year)
    )


def test_short_source_matched_against_abbreviated_ref_source():
    """Abbreviated journal in ref should match via short_source."""
    ref = _make_ref(
        title="A study of something", source="Dev Cell",
    )
    candidate = _make_candidate(
        title="A study of something",
        source="Developmental Cell",
        short_source="Dev Cell",
    )
    assert score_match(ref, candidate) >= HIGH_CONFIDENCE_THRESHOLD


def test_html_tags_stripped_before_comparison():
    ref = _make_ref(title="A study of something")
    candidate = _make_candidate(
        title="A study of <i>something</i>",
    )
    assert score_match(ref, candidate) >= HIGH_CONFIDENCE_THRESHOLD


def test_full_stops_stripped_before_comparison():
    ref = _make_ref(source="Dev Cell")
    candidate = _make_candidate(source="Dev. Cell")
    assert score_match(ref, candidate) >= HIGH_CONFIDENCE_THRESHOLD


def test_api_score_contributes_to_result():
    """api_score breaks a tie when titles are similar but not identical."""
    ref = _make_ref(title="A study of something important")
    candidate_high = _make_candidate(
        title="A study of something", api_score=200.0,
    )
    candidate_low = _make_candidate(
        title="A study of something", api_score=0.0,
    )
    assert (
        score_match(ref, candidate_high)
        > score_match(ref, candidate_low)
    )


def test_pages_match_within_range():
    ref = _make_ref(title="T", pages="123")
    candidate = _make_candidate(title="T", pages="123-145")
    assert score_match(ref, candidate) >= HIGH_CONFIDENCE_THRESHOLD


def test_pages_mismatch_reduces_score():
    ref = _make_ref(title="A study of something", pages="123")
    candidate_match = _make_candidate(
        title="A study of something", pages="123-145",
    )
    candidate_no_match = _make_candidate(
        title="A study of something", pages="200-210",
    )
    assert (
        score_match(ref, candidate_match)
        > score_match(ref, candidate_no_match)
    )
