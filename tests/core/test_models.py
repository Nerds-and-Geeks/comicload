from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from comicload.core.models import (
    Bucket,
    Candidate,
    CatalogEntry,
    ImportResult,
    Issue,
    Photo,
    Scope,
)


def test_photo_is_frozen():
    photo = Photo(id="abc123", data=b"\xff\xd8", filename="a.jpg")
    with pytest.raises(FrozenInstanceError):
        photo.id = "other"  # type: ignore[misc]


def test_scope_defaults_to_unbounded():
    scope = Scope()
    assert scope.publisher is None
    assert scope.year_from is None
    assert scope.year_to is None


def test_scope_matches_year_within_range():
    scope = Scope(year_from=1970, year_to=1985)
    assert scope.includes_year(1974)
    assert not scope.includes_year(1990)
    assert scope.includes_year(None)


def test_candidate_carries_originating_signal():
    candidate = Candidate(signal="barcode", confidence=0.9, barcode="759606084570111")
    assert candidate.signal == "barcode"
    assert candidate.series is None


def test_catalog_entry_full_title_includes_printing():
    entry = CatalogEntry(
        publisher_name="Image Comics",
        series_name="Alex + Ada",
        full_title="Alex + Ada #2 2nd Printing",
        release_date=date(2013, 12, 11),
    )
    assert entry.in_collection is True
    assert entry.in_wish_list is False
    assert entry.notes == ""


def test_issue_to_catalog_entry_builds_full_title():
    issue = Issue(
        gcd_id=1,
        publisher="Image Comics",
        series="Alex + Ada",
        issue_number="2",
        on_sale_date=date(2013, 12, 11),
        printing="2nd Printing",
    )
    entry = issue.to_catalog_entry()
    assert entry.full_title == "Alex + Ada #2 2nd Printing"
    assert entry.release_date == date(2013, 12, 11)


def test_issue_to_catalog_entry_omits_printing_when_absent():
    issue = Issue(gcd_id=2, publisher="Marvel", series="The Punisher", issue_number="12")
    assert issue.to_catalog_entry().full_title == "The Punisher #12"


def test_import_result_reports_counts():
    result = ImportResult(total=10, matched=8, unmatched=2, destination="out.csv")
    assert result.view_url is None
    assert result.matched == 8


def test_bucket_values():
    assert Bucket.CONFIDENT.value == "confident"
    assert Bucket.AMBIGUOUS.value == "ambiguous"
    assert Bucket.UNRECOGNIZED.value == "unrecognized"
