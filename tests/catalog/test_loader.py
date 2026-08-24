"""Tests for the GCD MySQL dump -> SQLite mirror loader."""

import sqlite3
from pathlib import Path

import pytest

from comicload.catalog.loader import load_dump
from comicload.errors import CatalogError

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
FIXTURE = FIXTURES / "gcd_sample.sql"
REALISTIC = FIXTURES / "gcd_realistic.sql"


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


def test_missing_named_column_raises_catalog_error(tmp_path):
    """A stale TABLE_MAP must fail loudly, not insert silent NULLs."""
    path = _dump(
        tmp_path,
        "INSERT INTO `gcd_issue` (`id`,`number`,`series_id`) VALUES (100,'12',10);\n",
    )
    with pytest.raises(CatalogError, match=r"gcd_issue.*on_sale_date"):
        load_dump(path, tmp_path / "gcd.sqlite")


# --- columns are mapped by name, never by position ---------------------------
# Real GCD tables are 20-40 columns wide and nobody promised the order. Positional
# reading of the fixture below puts 'color; 32 pgs' in series.publisher_id and the
# volume number in issue.series_id — plausible counts, a catalogue that matches nothing.


def test_realistic_wide_dump_maps_columns_by_name(tmp_path):
    db = tmp_path / "gcd.sqlite"
    counts = load_dump(REALISTIC, db)
    assert counts == {"publisher": 2, "series": 2, "issue": 2}

    conn = sqlite3.connect(db)
    assert conn.execute("SELECT id, name, publisher_id, year_began FROM series").fetchall() == [
        (10, "The Punisher", 1, 2000),
        (11, "Alex + Ada", 2, 2013),
    ]
    assert conn.execute("SELECT id, number, series_id, barcode FROM issue WHERE id=100").fetchone()
    row = conn.execute(
        """
        SELECT p.name, s.name, i.number, i.on_sale_date
        FROM issue i JOIN series s ON s.id = i.series_id
                     JOIN publisher p ON p.id = s.publisher_id
        WHERE i.barcode = '75960608457000111'
        """
    ).fetchone()
    assert row == ("Marvel", "The Punisher", "12", "2001-03-01")


def test_reordered_columns_on_the_insert_are_honoured(tmp_path):
    """The INSERT's own column list wins, whatever order it is in."""
    path = _dump(
        tmp_path,
        "INSERT INTO `gcd_series` (`name`,`year_began`,`publisher_id`,`format`,`id`) "
        "VALUES ('The Punisher',2000,1,'color; 32 pgs',10);\n",
    )
    load_dump(path, tmp_path / "gcd.sqlite")
    conn = sqlite3.connect(tmp_path / "gcd.sqlite")
    assert conn.execute("SELECT id, name, publisher_id, year_began FROM series").fetchone() == (
        10,
        "The Punisher",
        1,
        2000,
    )


def test_insert_without_columns_and_without_create_table_raises(tmp_path):
    path = _dump(tmp_path, "INSERT INTO `gcd_publisher` VALUES (1,'Marvel',225);\n")
    with pytest.raises(CatalogError, match="gcd_publisher"):
        load_dump(path, tmp_path / "gcd.sqlite")


def test_rows_that_load_but_never_join_raise_catalog_error(tmp_path):
    """The signature of a mapping mismatch: plausible counts, an unusable catalogue."""
    path = _dump(
        tmp_path,
        "INSERT INTO `gcd_publisher` (`id`,`name`) VALUES (1,'Marvel');\n"
        "INSERT INTO `gcd_series` (`id`,`name`,`publisher_id`,`year_began`) "
        "VALUES (10,'The Punisher',999,2000);\n"
        "INSERT INTO `gcd_issue` (`id`,`number`,`series_id`,`on_sale_date`,`barcode`) "
        "VALUES (100,'12',888,'2001-03-01','75960608457000111');\n",
    )
    with pytest.raises(CatalogError, match="not one issue joins"):
        load_dump(path, tmp_path / "gcd.sqlite")


def test_row_wider_than_its_column_list_raises(tmp_path):
    path = _dump(
        tmp_path,
        "INSERT INTO `gcd_publisher` (`id`,`name`) VALUES (1,'Marvel',225);\n",
    )
    with pytest.raises(CatalogError, match="3 values"):
        load_dump(path, tmp_path / "gcd.sqlite")


def test_truncated_dump_raises_instead_of_dropping_the_last_statement(tmp_path):
    path = _dump(
        tmp_path,
        "INSERT INTO `gcd_publisher` (`id`,`name`) VALUES (1,'Marvel');\n"
        "INSERT INTO `gcd_publisher` (`id`,`name`) VALUES (2,'DC')",
    )
    with pytest.raises(CatalogError, match="truncated"):
        load_dump(path, tmp_path / "gcd.sqlite")


def test_replace_into_statements_are_loaded_not_skipped(tmp_path):
    path = _dump(
        tmp_path,
        "REPLACE INTO `gcd_publisher` (`id`,`name`) VALUES (1,'Marvel');\n",
    )
    assert load_dump(path, tmp_path / "gcd.sqlite")["publisher"] == 1


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


def test_progress_reports_bytes_of_the_dump_including_skipped_tables(tmp_path):
    """Progress measures how much of the file has been read, not how many rows were
    kept — a sync spends most of its time in tables it skips, and a bar that only
    moved on kept rows sat still long enough that users killed and retried the sync."""
    seen: list[int] = []
    load_dump(FIXTURE, tmp_path / "gcd.sqlite", on_progress=seen.append)
    reported = sum(seen)
    file_size = FIXTURE.stat().st_size
    # statements exclude inter-statement whitespace, so reported <= size, but the
    # two must be the same order of magnitude for the bar to be honest
    assert file_size * 0.8 <= reported <= file_size


def test_a_failed_sync_leaves_the_previous_mirror_intact(tmp_path):
    """C1: DROP TABLE ran before parsing, so any load failure destroyed the mirror."""
    good = tmp_path / "good.sql"
    good.write_text(
        "CREATE TABLE `gcd_publisher` (`id` int, `name` varchar(255));\n"
        "INSERT INTO `gcd_publisher` VALUES (1,'Marvel');\n"
        "CREATE TABLE `gcd_series` (`id` int, `name` varchar(255), `publisher_id` int,"
        " `year_began` int);\n"
        "INSERT INTO `gcd_series` VALUES (10,'The Punisher',1,2000);\n"
        "CREATE TABLE `gcd_issue` (`id` int, `number` varchar(50), `series_id` int,"
        " `on_sale_date` date, `barcode` varchar(38));\n"
        "INSERT INTO `gcd_issue` VALUES (100,'12',10,'2001-03-01','75960608457000111');\n"
    )
    db = tmp_path / "gcd.sqlite"
    load_dump(good, db)

    bad = tmp_path / "bad.sql"
    bad.write_text("INSERT INTO `gcd_issue` VALUES (1,'1'")  # truncated mid-statement
    with pytest.raises(CatalogError):
        load_dump(bad, db)

    conn = sqlite3.connect(db)
    count = conn.execute("SELECT COUNT(*) FROM issue").fetchone()[0]
    conn.close()
    assert count == 1, "failed sync must not destroy the existing mirror"


def test_database_qualified_table_names_are_loaded(tmp_path):
    """I1: mysqldump --databases emits `db`.`table` names; these loaded 0 rows silently."""
    dump = tmp_path / "qual.sql"
    dump.write_text(
        "CREATE TABLE `gcd`.`gcd_publisher` (`id` int, `name` varchar(255));\n"
        "INSERT INTO `gcd`.`gcd_publisher` VALUES (1,'Marvel');\n"
        "CREATE TABLE `gcd`.`gcd_series` (`id` int, `name` varchar(255), `publisher_id` int,"
        " `year_began` int);\n"
        "INSERT INTO `gcd`.`gcd_series` VALUES (10,'The Punisher',1,2000);\n"
        "CREATE TABLE `gcd`.`gcd_issue` (`id` int, `number` varchar(50), `series_id` int,"
        " `on_sale_date` date, `barcode` varchar(38));\n"
        "INSERT INTO `gcd`.`gcd_issue` VALUES (100,'12',10,'2001-03-01','x1');\n"
    )
    counts = load_dump(dump, tmp_path / "g.sqlite")
    assert counts == {"publisher": 1, "series": 1, "issue": 1}


def test_insert_data_containing_create_table_text_is_not_mistaken_for_ddl(tmp_path):
    """I2: _create_table_columns searched anywhere in the statement, so an INSERT whose
    DATA contained 'CREATE TABLE x (' was silently discarded."""
    dump = tmp_path / "evil.sql"
    dump.write_text(
        "CREATE TABLE `gcd_publisher` (`id` int, `name` varchar(255));\n"
        "INSERT INTO `gcd_publisher` VALUES (1,'CREATE TABLE evil ('),(2,'Marvel');\n"
        "CREATE TABLE `gcd_series` (`id` int, `name` varchar(255), `publisher_id` int,"
        " `year_began` int);\n"
        "INSERT INTO `gcd_series` VALUES (10,'S',2,2000);\n"
        "CREATE TABLE `gcd_issue` (`id` int, `number` varchar(50), `series_id` int,"
        " `on_sale_date` date, `barcode` varchar(38));\n"
        "INSERT INTO `gcd_issue` VALUES (100,'1',10,'2001-03-01','x1');\n"
    )
    counts = load_dump(dump, tmp_path / "g.sqlite")
    assert counts["publisher"] == 2, "rows containing DDL-like text must not be dropped"


def test_a_dump_that_loads_nothing_is_an_error_not_a_success(tmp_path):
    """A dump whose tables all miss the mapping must not print 'Database ready'."""
    dump = tmp_path / "wrong.sql"
    dump.write_text(
        "CREATE TABLE `other_table` (`id` int);\nINSERT INTO `other_table` VALUES (1);\n"
    )
    with pytest.raises(CatalogError, match="no comic data"):
        load_dump(dump, tmp_path / "g.sqlite")
