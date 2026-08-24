from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from types import TracebackType

from comicload.errors import CatalogError
from comicload.models import Candidate, Issue, Scope

_SELECT = """
SELECT i.id AS issue_id,
       p.name AS publisher_name,
       s.name AS series_name,
       i.number AS issue_number,
       i.on_sale_date AS on_sale_date,
       s.year_began AS series_year
FROM issue i
JOIN series s ON s.id = i.series_id
JOIN publisher p ON p.id = s.publisher_id
"""

_MAX_MATCHES = 25


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


class SqliteIssueResolver:
    """Resolves candidates against the local GCD mirror database."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            if not self._db_path.exists() or self._db_path.stat().st_size == 0:
                raise CatalogError(
                    f"no metadata catalogue at {self._db_path}; run 'comicload catalog sync' first"
                )
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("SELECT 1 FROM issue LIMIT 1")
            except sqlite3.OperationalError as exc:
                conn.close()
                raise CatalogError(
                    f"no metadata catalogue at {self._db_path}; run 'comicload catalog sync' first"
                ) from exc
            self._conn = conn
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> SqliteIssueResolver:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def resolve(self, candidate: Candidate, scope: Scope | None = None) -> list[Issue]:
        if candidate.barcode:
            clean_bc = candidate.barcode.replace(" ", "").replace("-", "")
            bare_upc = clean_bc[:12] if len(clean_bc) >= 12 else clean_bc

            # Fast B-Tree indexed lookup first (0.1ms)
            sql = f"{_SELECT} WHERE i.barcode IN (?, ?, ?) ORDER BY i.id LIMIT {_MAX_MATCHES}"
            issues = self._query(sql, (candidate.barcode, clean_bc, bare_upc))

            # Fast B-Tree range lookup for 12-digit UPC (0.1ms)
            if not issues and len(bare_upc) == 12:
                upper = bare_upc[:-1] + chr(ord(bare_upc[-1]) + 1)
                prefix_sql = (
                    f"{_SELECT} WHERE i.barcode >= ? AND i.barcode < ? "
                    f"ORDER BY i.id LIMIT {_MAX_MATCHES}"
                )
                issues = self._query(prefix_sql, (bare_upc, upper))

            if len(issues) > 1 and candidate.issue_number:
                cand_num = candidate.issue_number.lstrip("0") or "0"
                filtered = [
                    i
                    for i in issues
                    if i.issue_number.lstrip("0") == cand_num or cand_num in i.issue_number.split()
                ]
                if filtered:
                    return filtered
            return issues

        if candidate.series and candidate.issue_number:
            sql = (
                f"{_SELECT} WHERE s.name = ? COLLATE NOCASE AND i.number = ? "
                "ORDER BY (i.on_sale_date IS NULL OR i.on_sale_date = ''), "
                f"i.on_sale_date DESC, i.id LIMIT {_MAX_MATCHES}"
            )
            return self._query(sql, (candidate.series, candidate.issue_number))

        if candidate.series:
            # Every typed word must appear somewhere in the name, not the whole
            # phrase contiguously: a person typing what they read off a cover
            # writes "Superman Brainiac" for a series GCD stores as "Superman:
            # Brainiac" — the colon breaks a single substring match even though
            # a human would call these the same query.
            words = candidate.series.split()
            clauses = " AND ".join("s.name LIKE ? COLLATE NOCASE" for _ in words)
            sql = (
                f"{_SELECT} WHERE {clauses} "
                "ORDER BY (i.on_sale_date IS NULL OR i.on_sale_date = ''), "
                f"i.on_sale_date DESC, i.id LIMIT {_MAX_MATCHES}"
            )
            params = tuple(f"%{word}%" for word in words)
            return self._query(sql, params)

        return []

    def _query(self, sql: str, params: tuple[object, ...]) -> list[Issue]:
        rows = self._connect().execute(sql, params).fetchall()
        return [
            Issue(
                gcd_id=row["issue_id"],
                publisher=row["publisher_name"],
                series=row["series_name"],
                issue_number=row["issue_number"],
                on_sale_date=_parse_date(row["on_sale_date"]),
                series_year=row["series_year"],
            )
            for row in rows
        ]
