from datetime import date
from pathlib import Path

import pytest

from comicload.core.models import Candidate, Scope
from comicload.infra.storage.gcd_loader import load_dump
from comicload.infra.storage.gcd_repo import SqliteIssueResolver

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "gcd_sample.sql"


@pytest.fixture
def resolver(tmp_path):
    db = tmp_path / "gcd.sqlite"
    load_dump(FIXTURE, db)
    return SqliteIssueResolver(db)


def test_exact_barcode_match_wins(resolver):
    candidate = Candidate(signal="barcode", confidence=0.95, barcode="75960608457000111")
    issues = resolver.resolve(candidate, Scope())

    assert len(issues) == 1
    assert issues[0].series == "The Punisher"
    assert issues[0].publisher == "Marvel"
    assert issues[0].issue_number == "12"
    assert issues[0].on_sale_date == date(2001, 3, 1)


def test_unknown_barcode_returns_nothing(resolver):
    candidate = Candidate(signal="barcode", confidence=0.95, barcode="00000000000000000")
    assert resolver.resolve(candidate, Scope()) == []


def test_series_and_issue_match_when_no_barcode(resolver):
    candidate = Candidate(signal="ocr", confidence=0.6, series="Alex + Ada", issue_number="2")
    issues = resolver.resolve(candidate, Scope())
    assert [i.publisher for i in issues] == ["Image Comics"]


def test_scope_publisher_filters_results(resolver):
    candidate = Candidate(signal="ocr", confidence=0.6, issue_number="12")
    assert resolver.resolve(candidate, Scope(publisher="Image Comics")) == []
    assert resolver.resolve(candidate, Scope(publisher="Marvel"))


def test_candidate_with_no_usable_fields_returns_nothing(resolver):
    assert resolver.resolve(Candidate(signal="none", confidence=0.1), Scope()) == []


def test_missing_database_raises(tmp_path):
    from comicload.core.errors import CatalogError

    resolver = SqliteIssueResolver(tmp_path / "absent.sqlite")
    with pytest.raises(CatalogError, match="catalog sync"):
        resolver.resolve(Candidate(signal="barcode", confidence=1.0, barcode="1"), Scope())
