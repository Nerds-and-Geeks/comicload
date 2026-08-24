from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path
from types import TracebackType

from comicload.domain.errors import CatalogError
from comicload.domain.models import Candidate, Issue, Scope

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
            sql = f"{_SELECT} WHERE i.barcode = ? ORDER BY i.id LIMIT {_MAX_MATCHES}"
            issues = self._query(sql, (candidate.barcode,))
            if not issues and len(candidate.barcode) == 12:
                upper = candidate.barcode[:-1] + chr(ord(candidate.barcode[-1]) + 1)
                prefix_sql = (
                    f"{_SELECT} WHERE i.barcode >= ? AND i.barcode < ? "
                    f"ORDER BY i.id LIMIT {_MAX_MATCHES}"
                )
                issues = self._query(prefix_sql, (candidate.barcode, upper))
            return issues

        if candidate.series and candidate.issue_number:
            sql = (
                f"{_SELECT} WHERE s.name = ? COLLATE NOCASE AND i.number = ? "
                "ORDER BY (i.on_sale_date IS NULL OR i.on_sale_date = ''), "
                f"i.on_sale_date DESC, i.id LIMIT {_MAX_MATCHES}"
            )
            return self._query(sql, (candidate.series, candidate.issue_number))

        if candidate.series:
            sql = (
                f"{_SELECT} WHERE s.name = ? COLLATE NOCASE "
                "ORDER BY (i.on_sale_date IS NULL OR i.on_sale_date = ''), "
                f"i.on_sale_date DESC, i.id LIMIT {_MAX_MATCHES}"
            )
            return self._query(sql, (candidate.series,))

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
