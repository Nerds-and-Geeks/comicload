"""The resolver's own tests: what SQL it asks for, and how it reads the answer back."""

import dataclasses
import sqlite3
from datetime import date

import pytest

from comicload.catalog import repository as gcd_repo
from comicload.catalog.loader import SCHEMA
from comicload.catalog.repository import SqliteIssueResolver
from comicload.errors import CatalogError
from comicload.models import Candidate, Scope


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


def test_an_unscoped_query_still_stops_at_the_row_limit(thirty_issue_run):
    candidate = Candidate(signal="ocr", confidence=0.6, series="Detective Comics", issue_number="1")

    assert len(thirty_issue_run.resolve(candidate, Scope())) == 25


# --- columns are read by name, so the SELECT list can be reordered -------------


REORDERED_SELECT = """
SELECT i.on_sale_date AS on_sale_date,
       s.year_began AS series_year,
       i.number AS issue_number,
       s.name AS series_name,
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


def test_a_zero_byte_database_raises_catalog_error(tmp_path):
    empty_db = tmp_path / "empty.sqlite"
    empty_db.touch()
    resolver = SqliteIssueResolver(empty_db)

    with pytest.raises(CatalogError, match="catalog sync"):
        resolver.resolve(CANDIDATE, Scope())


# --- the resolver reports the catalogue, not its own input ---------------------


def test_resolve_reports_the_printing_the_database_holds(one_issue):
    """Copying the candidate's printing onto the result is a fusion decision, and
    fusion belongs to IdentifyService — a data-access method must not answer with
    something it was merely handed."""
    candidate = dataclasses.replace(CANDIDATE, printing="2nd Printing")

    assert one_issue.resolve(candidate, Scope())[0].printing is None


def test_a_bare_upc_prefix_matches_gcds_17_digit_barcode(tmp_path):
    """GCD stores UPC+EAN5 concatenated (e.g. 76194134388495711). A scan that only
    read the 12-digit UPC must still find those rows. Found against the real dump."""
    db = tmp_path / "gcd.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO publisher VALUES (1,'DC')")
    conn.execute("INSERT INTO series VALUES (10,'Action Comics',1,1938)")
    conn.execute("INSERT INTO issue VALUES (100,'957',10,'2016-06-08','76194134388495711')")
    conn.commit()
    conn.close()

    resolver = SqliteIssueResolver(db)
    candidate = Candidate(signal="barcode", confidence=0.9, barcode="761941343884")
    issues = resolver.resolve(candidate, Scope())
    assert [issue.issue_number for issue in issues] == ["957"]


def test_dated_matches_outrank_undated_reprints(tmp_path):
    """Foreign reprints with no on-sale date must not bury the real answer."""
    db = tmp_path / "gcd.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO publisher VALUES (1,'DC'),(2,'Zinco'),(3,'Play Press')")
    conn.execute(
        "INSERT INTO series VALUES (10,'Superman',1,2023),(11,'Superman',2,1984),"
        "(12,'Superman',3,1993)"
    )
    conn.execute(
        "INSERT INTO issue VALUES (5,'28',11,NULL,NULL),(6,'28',12,'',NULL),"
        "(7,'28',10,'2025-07-23',NULL)"
    )
    conn.commit()
    conn.close()

    issues = SqliteIssueResolver(db).resolve(
        Candidate(signal="human", confidence=1.0, series="Superman", issue_number="28"),
        Scope(),
    )
    assert issues[0].publisher == "DC"
    assert issues[0].on_sale_date is not None


def test_series_only_lookup_matches_a_substring_not_just_exact(tmp_path):
    """Found against real data: GCD's real series is 'Superman: Brainiac', but a
    person typing what they read off the cover writes 'Superman Brainiac' — no
    colon. An exact match rejects every real query a human actually types."""
    db = build_db(
        tmp_path / "gcd.sqlite",
        [(1, "[nn]", "2023-11-07")],
        series="Superman: Brainiac",
    )
    resolver = SqliteIssueResolver(db)

    candidate = Candidate(signal="human", confidence=1.0, series="Superman Brainiac")
    issues = resolver.resolve(candidate, Scope())

    assert len(issues) == 1
    assert issues[0].series == "Superman: Brainiac"


def test_series_only_lookup_still_works_case_insensitively(tmp_path):
    db = build_db(tmp_path / "gcd.sqlite", [(1, "1", "2020-01-01")], series="Detective Comics")
    resolver = SqliteIssueResolver(db)
    candidate = Candidate(signal="human", confidence=1.0, series="detective")
    assert len(resolver.resolve(candidate, Scope())) == 1


def test_series_only_lookup_does_not_match_an_unrelated_series(tmp_path):
    db = build_db(tmp_path / "gcd.sqlite", [(1, "1", "2020-01-01")], series="Detective Comics")
    resolver = SqliteIssueResolver(db)
    candidate = Candidate(signal="human", confidence=1.0, series="Action Comics")
    assert resolver.resolve(candidate, Scope()) == []
