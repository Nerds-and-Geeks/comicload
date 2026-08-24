"""The resolver's query plan must not degrade into table scans.

`SqliteIssueResolver` filters series and publisher names with COLLATE NOCASE. A
BINARY-collated index cannot serve those predicates, and without an index on
issue(series_id) the planner falls back to scanning `issue` — the largest table in
a real GCD dump, around two million rows. Neither shows up in correctness tests.
"""

import sqlite3

from comicload.infra.storage.gcd_loader import SCHEMA

SERIES_LOOKUP = (
    "SELECT i.id FROM issue i "
    "JOIN series s ON s.id = i.series_id "
    "JOIN publisher p ON p.id = s.publisher_id "
    "WHERE s.name = ? COLLATE NOCASE"
)


def _plan(db, sql, params):
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    try:
        rows = conn.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    finally:
        conn.close()
    return " | ".join(row[-1] for row in rows)


def test_case_insensitive_series_lookup_uses_the_name_index(tmp_path):
    plan = _plan(tmp_path / "a.sqlite", SERIES_LOOKUP, ("x",))
    assert "idx_series_name" in plan, f"name index unused; plan: {plan}"


def test_series_lookup_does_not_scan_the_issue_table(tmp_path):
    plan = _plan(tmp_path / "b.sqlite", SERIES_LOOKUP, ("x",))
    assert "SCAN i" not in plan, f"full scan of issue table; plan: {plan}"


def test_publisher_scope_uses_the_name_index(tmp_path):
    sql = SERIES_LOOKUP + " AND p.name = ? COLLATE NOCASE"
    plan = _plan(tmp_path / "c.sqlite", sql, ("x", "Marvel"))
    assert "SCAN" not in plan, f"scan present; plan: {plan}"


def test_barcode_lookup_still_uses_its_index(tmp_path):
    sql = (
        "SELECT i.id FROM issue i "
        "JOIN series s ON s.id = i.series_id "
        "JOIN publisher p ON p.id = s.publisher_id "
        "WHERE i.barcode = ?"
    )
    plan = _plan(tmp_path / "d.sqlite", sql, ("759606084570",))
    assert "idx_issue_barcode" in plan, f"barcode index unused; plan: {plan}"


def test_barcode_prefix_fallback_seeks_rather_than_scans(tmp_path):
    """LIKE 'prefix%' walks the whole barcode index (SCAN); half-open range bounds
    seek straight to the prefix (SEARCH). Matters at 2.5M issues, once per photo."""
    plan = _plan(
        tmp_path / "e.sqlite",
        "SELECT i.id FROM issue i WHERE i.barcode >= ? AND i.barcode < ?",
        ("761941343884", "761941343885"),
    )
    assert "SEARCH i" in plan and "SCAN" not in plan, plan
