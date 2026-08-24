"""Tests for the GCD MySQL dump -> SQLite mirror loader."""

import sqlite3
from pathlib import Path

import pytest

from comicload.core.errors import CatalogError
from comicload.infra.storage.gcd_loader import load_dump

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "gcd_sample.sql"


def _dump(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "dump.sql"
    path.write_text(body)
    return path


# --- the brief's cases -------------------------------------------------------


def test_load_reports_row_counts(tmp_path):
    counts = load_dump(FIXTURE, tmp_path / "gcd.sqlite")
    assert counts == {"publisher": 2, "series": 2, "issue": 2}


def test_loaded_issue_joins_series_and_publisher(tmp_path):
    db = tmp_path / "gcd.sqlite"
    load_dump(FIXTURE, db)

    conn = sqlite3.connect(db)
    row = conn.execute(
        """
        SELECT p.name, s.name, i.number, i.on_sale_date, i.barcode
        FROM issue i JOIN series s ON s.id = i.series_id
                     JOIN publisher p ON p.id = s.publisher_id
        WHERE i.barcode = '75960608457000111'
        """
    ).fetchone()
    assert row == ("Marvel", "The Punisher", "12", "2001-03-01", "75960608457000111")


def test_barcode_index_exists(tmp_path):
    db = tmp_path / "gcd.sqlite"
    load_dump(FIXTURE, db)
    conn = sqlite3.connect(db)
    indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_issue_barcode" in indexes


def test_reload_is_idempotent(tmp_path):
    db = tmp_path / "gcd.sqlite"
    load_dump(FIXTURE, db)
    counts = load_dump(FIXTURE, db)
    assert counts == {"publisher": 2, "series": 2, "issue": 2}


def test_progress_callback_is_invoked(tmp_path):
    seen: list[int] = []
    load_dump(FIXTURE, tmp_path / "gcd.sqlite", on_progress=seen.append)
    assert seen


def test_missing_dump_raises_catalog_error(tmp_path):
    with pytest.raises(CatalogError, match="not found"):
        load_dump(tmp_path / "nope.sql", tmp_path / "gcd.sqlite")


# --- extra cases: the MySQL VALUES parser ------------------------------------
# A real GCD dump is full of commas, apostrophes and NULLs inside quoted values.
# The brief's fixture contains none of them, so the parser is exercised here.


def test_comma_inside_quoted_value_is_not_a_column_separator(tmp_path):
    path = _dump(
        tmp_path,
        "INSERT INTO `gcd_series` (`id`,`name`,`publisher_id`,`year_began`) "
        "VALUES (10,'Crisis, Part One',1,1985);\n",
    )
    load_dump(path, tmp_path / "gcd.sqlite")
    conn = sqlite3.connect(tmp_path / "gcd.sqlite")
    assert conn.execute("SELECT name, year_began FROM series").fetchone() == (
        "Crisis, Part One",
        1985,
    )


def test_backslash_escaped_quote_is_kept_in_the_value(tmp_path):
    path = _dump(
        tmp_path,
        "INSERT INTO `gcd_series` (`id`,`name`,`publisher_id`,`year_began`) "
        "VALUES (10,'Alex\\'s Ada',1,2013);\n",
    )
    load_dump(path, tmp_path / "gcd.sqlite")
    conn = sqlite3.connect(tmp_path / "gcd.sqlite")
    assert conn.execute("SELECT name FROM series").fetchone() == ("Alex's Ada",)


def test_doubled_quote_is_kept_in_the_value(tmp_path):
    path = _dump(
        tmp_path,
        "INSERT INTO `gcd_series` (`id`,`name`,`publisher_id`,`year_began`) "
        "VALUES (10,'Alex''s Ada',1,2013);\n",
    )
    load_dump(path, tmp_path / "gcd.sqlite")
    conn = sqlite3.connect(tmp_path / "gcd.sqlite")
    assert conn.execute("SELECT name FROM series").fetchone() == ("Alex's Ada",)


def test_unquoted_null_becomes_none_and_quoted_null_stays_text(tmp_path):
    path = _dump(
        tmp_path,
        "INSERT INTO `gcd_issue` (`id`,`number`,`series_id`,`on_sale_date`,`barcode`) "
        "VALUES (100,'12',10,NULL,'NULL');\n",
    )
    load_dump(path, tmp_path / "gcd.sqlite")
    conn = sqlite3.connect(tmp_path / "gcd.sqlite")
    assert conn.execute("SELECT on_sale_date, barcode FROM issue").fetchone() == (None, "NULL")


def test_semicolon_and_parens_inside_a_value_do_not_end_the_statement(tmp_path):
    path = _dump(
        tmp_path,
        "INSERT INTO `gcd_series` (`id`,`name`,`publisher_id`,`year_began`) "
        "VALUES (10,'Wham; Bam (Deluxe)',1,2000),(11,'Second',1,2001);\n"
        "INSERT INTO `gcd_publisher` (`id`,`name`) VALUES (1,'Marvel');\n",
    )
    counts = load_dump(path, tmp_path / "gcd.sqlite")
    assert counts == {"publisher": 1, "series": 2, "issue": 0}
    conn = sqlite3.connect(tmp_path / "gcd.sqlite")
    assert conn.execute("SELECT name FROM series WHERE id = 10").fetchone() == (
        "Wham; Bam (Deluxe)",
    )


def test_statement_may_span_several_lines(tmp_path):
    path = _dump(
        tmp_path,
        "INSERT INTO `gcd_publisher` (`id`,`name`) VALUES\n(1,'Marvel'),\n(2,'DC');\n",
    )
    assert load_dump(path, tmp_path / "gcd.sqlite")["publisher"] == 2


# --- extra cases: mapping mismatches ----------------------------------------


def test_row_shorter_than_the_mapping_raises_catalog_error(tmp_path):
    """A stale TABLE_MAP must fail loudly, not insert silent NULLs."""
    path = _dump(
        tmp_path,
        "INSERT INTO `gcd_issue` (`id`,`number`,`series_id`) VALUES (100,'12',10);\n",
    )
    with pytest.raises(CatalogError, match=r"gcd_issue.*5.*3|gcd_issue.*3.*5"):
        load_dump(path, tmp_path / "gcd.sqlite")


def test_extra_trailing_columns_are_ignored(tmp_path):
    path = _dump(
        tmp_path,
        "INSERT INTO `gcd_publisher` (`id`,`name`,`country_id`,`year_began`) "
        "VALUES (1,'Marvel',225,1939);\n",
    )
    counts = load_dump(path, tmp_path / "gcd.sqlite")
    assert counts["publisher"] == 1
    conn = sqlite3.connect(tmp_path / "gcd.sqlite")
    assert conn.execute("SELECT id, name FROM publisher").fetchone() == (1, "Marvel")


def test_unmapped_tables_are_skipped(tmp_path):
    path = _dump(
        tmp_path,
        "INSERT INTO `gcd_story` (`id`,`title`) VALUES (1,'Chapter One');\n"
        "INSERT INTO `gcd_publisher` (`id`,`name`) VALUES (1,'Marvel');\n",
    )
    assert load_dump(path, tmp_path / "gcd.sqlite") == {
        "publisher": 1,
        "series": 0,
        "issue": 0,
    }


def test_several_inserts_for_one_table_accumulate(tmp_path):
    path = _dump(
        tmp_path,
        "INSERT INTO `gcd_publisher` (`id`,`name`) VALUES (1,'Marvel');\n"
        "INSERT INTO `gcd_publisher` (`id`,`name`) VALUES (2,'DC'),(3,'Dark Horse');\n",
    )
    assert load_dump(path, tmp_path / "gcd.sqlite")["publisher"] == 3


def test_progress_reports_rows_as_they_load(tmp_path):
    seen: list[int] = []
    counts = load_dump(FIXTURE, tmp_path / "gcd.sqlite", on_progress=seen.append)
    assert sum(seen) == sum(counts.values())
