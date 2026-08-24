"""The resolver's own tests: what SQL it asks for, and how it reads the answer back."""

import sqlite3

import pytest

from comicload.core.models import Candidate, Scope
from comicload.infra.storage.gcd_loader import SCHEMA
from comicload.infra.storage.gcd_repo import SqliteIssueResolver


def build_db(path, issues, publisher="DC", series="Detective Comics"):
    """A one-publisher, one-series mirror holding exactly the issues given.

    `issues` is a sequence of (id, number, on_sale_date) — on_sale_date may be None.
    """
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO publisher VALUES (1, ?)", (publisher,))
        conn.execute("INSERT INTO series VALUES (10, ?, 1, 1937)", (series,))
        conn.executemany(
            "INSERT INTO issue (id, number, series_id, on_sale_date, barcode) "
            "VALUES (?, ?, 10, ?, NULL)",
            issues,
        )
        conn.commit()
    finally:
        conn.close()
    return path


# --- a scoped query must not lose rows to the row limit ------------------------


@pytest.fixture
def thirty_issue_run(tmp_path):
    """Thirty issues numbered '1' — 28 from 1964, then the two the user is scoping to."""
    issues = [(100 + n, "1", "1964-06-01") for n in range(28)]
    issues += [(200, "1", "1980-04-01"), (201, "1", "1980-05-01")]
    return SqliteIssueResolver(build_db(tmp_path / "gcd.sqlite", issues))


def test_a_year_scope_finds_matches_beyond_the_row_limit(thirty_issue_run):
    """LIMIT applied before the year filter discards the very rows the scope keeps."""
    candidate = Candidate(signal="ocr", confidence=0.6, series="Detective Comics", issue_number="1")

    issues = thirty_issue_run.resolve(candidate, Scope(year_from=1980, year_to=1980))

    assert [issue.gcd_id for issue in issues] == [200, 201]


def test_an_unscoped_query_still_stops_at_the_row_limit(thirty_issue_run):
    candidate = Candidate(signal="ocr", confidence=0.6, series="Detective Comics", issue_number="1")

    assert len(thirty_issue_run.resolve(candidate, Scope())) == 25


# --- absence of a date is not evidence of a mismatch ---------------------------


def test_an_issue_with_no_date_survives_a_year_scope(tmp_path):
    """Scope.includes_year(None) is True by design; the SQL must agree with it."""
    resolver = SqliteIssueResolver(
        build_db(tmp_path / "gcd.sqlite", [(1, "7", None), (2, "7", "1964-01-01")])
    )
    candidate = Candidate(signal="ocr", confidence=0.6, series="Detective Comics", issue_number="7")

    issues = resolver.resolve(candidate, Scope(year_from=1980, year_to=1985))

    assert [issue.gcd_id for issue in issues] == [1]
    assert issues[0].on_sale_date is None


def test_an_unreadable_date_survives_a_year_scope(tmp_path):
    """A date the catalogue cannot parse is unknown, not wrong."""
    resolver = SqliteIssueResolver(
        build_db(tmp_path / "gcd.sqlite", [(1, "7", "not-a-date"), (2, "7", "1964-01-01")])
    )
    candidate = Candidate(signal="ocr", confidence=0.6, series="Detective Comics", issue_number="7")

    issues = resolver.resolve(candidate, Scope(year_from=1980, year_to=1985))

    assert [issue.gcd_id for issue in issues] == [1]


def test_a_year_scope_excludes_issues_outside_it(tmp_path):
    resolver = SqliteIssueResolver(
        build_db(
            tmp_path / "gcd.sqlite",
            [(1, "7", "1979-12-31"), (2, "7", "1980-01-01"), (3, "7", "1981-01-01")],
        )
    )
    candidate = Candidate(signal="ocr", confidence=0.6, series="Detective Comics", issue_number="7")

    assert [i.gcd_id for i in resolver.resolve(candidate, Scope(year_from=1980))] == [2, 3]
    assert [i.gcd_id for i in resolver.resolve(candidate, Scope(year_to=1980))] == [1, 2]
