"""The resolver's own tests: what SQL it asks for, and how it reads the answer back."""

import dataclasses
import sqlite3
from datetime import date

import pytest

from comicload.core.errors import CatalogError
from comicload.core.models import Candidate, Scope
from comicload.infra.storage import gcd_repo
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


# --- columns are read by name, so the SELECT list can be reordered -------------


REORDERED_SELECT = """
SELECT i.on_sale_date AS on_sale_date,
       s.name AS series_name,
       i.number AS issue_number,
       p.name AS publisher_name,
       i.id AS issue_id
FROM issue i
JOIN series s ON s.id = i.series_id
JOIN publisher p ON p.id = s.publisher_id
"""


def test_reordering_the_select_list_cannot_misassign_a_field(monkeypatch, tmp_path):
    """Positional reads put the publisher in the series and never say a word."""
    resolver = SqliteIssueResolver(build_db(tmp_path / "gcd.sqlite", [(1, "7", "1964-01-01")]))
    monkeypatch.setattr(gcd_repo, "_SELECT", REORDERED_SELECT)

    issue = resolver.resolve(
        Candidate(signal="ocr", confidence=0.6, series="Detective Comics", issue_number="7"),
        Scope(),
    )[0]

    assert issue.gcd_id == 1
    assert issue.publisher == "DC"
    assert issue.series == "Detective Comics"
    assert issue.issue_number == "7"
    assert issue.on_sale_date == date(1964, 1, 1)


# --- one connection for a whole scan, not one per photo ------------------------


CANDIDATE = Candidate(signal="ocr", confidence=0.6, series="Detective Comics", issue_number="7")


@pytest.fixture
def one_issue(tmp_path):
    return SqliteIssueResolver(build_db(tmp_path / "gcd.sqlite", [(1, "7", "1964-01-01")]))


@pytest.fixture
def opened(monkeypatch):
    """Every connection sqlite3 hands out from here on, in order."""
    connections = []
    real_connect = sqlite3.connect

    def recording(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        connections.append(conn)
        return conn

    monkeypatch.setattr(gcd_repo.sqlite3, "connect", recording)
    return connections


def test_repeated_resolves_share_one_connection(one_issue, opened):
    """A 500-photo scan used to open 500 connections and stat the file 500 times."""
    for _ in range(3):
        assert one_issue.resolve(CANDIDATE, Scope())

    assert len(opened) == 1


def test_close_releases_the_connection(one_issue, opened):
    one_issue.resolve(CANDIDATE, Scope())
    one_issue.close()

    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")


def test_close_is_safe_to_call_twice_and_before_any_query(one_issue):
    one_issue.close()
    one_issue.close()


def test_a_closed_resolver_still_works(one_issue, opened):
    one_issue.resolve(CANDIDATE, Scope())
    one_issue.close()

    assert one_issue.resolve(CANDIDATE, Scope())
    assert len(opened) == 2


def test_the_resolver_is_a_context_manager(one_issue, opened):
    with one_issue as resolver:
        assert resolver.resolve(CANDIDATE, Scope())

    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")


def test_a_missing_database_is_reported_when_it_is_used(tmp_path):
    """Constructing a resolver must not touch the disk; resolving must say what is wrong."""

    resolver = SqliteIssueResolver(tmp_path / "absent.sqlite")

    with pytest.raises(CatalogError, match="catalog sync"):
        resolver.resolve(CANDIDATE, Scope())


# --- the resolver reports the catalogue, not its own input ---------------------


def test_resolve_reports_the_printing_the_database_holds(one_issue):
    """Copying the candidate's printing onto the result is a fusion decision, and
    fusion belongs to IdentifyService — a data-access method must not answer with
    something it was merely handed."""
    candidate = dataclasses.replace(CANDIDATE, printing="2nd Printing")

    assert one_issue.resolve(candidate, Scope())[0].printing is None
