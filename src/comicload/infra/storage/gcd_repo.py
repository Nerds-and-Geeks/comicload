from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from comicload.core.errors import CatalogError
from comicload.core.models import Candidate, Issue, Scope
from comicload.core.storage_registry import Dsn, register_resolver

_BASE_QUERY = """
SELECT i.id, p.name, s.name, i.number, i.on_sale_date
FROM issue i
JOIN series s ON s.id = i.series_id
JOIN publisher p ON p.id = s.publisher_id
"""


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


@register_resolver("sqlite")
class SqliteIssueResolver:
    """Resolves candidates against the local GCD mirror.

    Barcode match is exact and preferred. Series/issue match is the fallback.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)

    @classmethod
    def from_dsn(cls, dsn: Dsn) -> SqliteIssueResolver:
        return cls(Path(dsn.target))

    def _connect(self) -> sqlite3.Connection:
        if not self._db_path.exists():
            raise CatalogError(
                f"no metadata catalogue at {self._db_path}; run 'comicload catalog sync' first"
            )
        return sqlite3.connect(self._db_path)

    def resolve(self, candidate: Candidate, scope: Scope) -> list[Issue]:
        clauses: list[str] = []
        params: list[str] = []

        if candidate.barcode:
            clauses.append("i.barcode = ?")
            params.append(candidate.barcode)
        else:
            if candidate.series:
                clauses.append("s.name = ? COLLATE NOCASE")
                params.append(candidate.series)
            if candidate.issue_number:
                clauses.append("i.number = ?")
                params.append(candidate.issue_number)

        if not clauses:
            return []

        if scope.publisher:
            clauses.append("p.name = ? COLLATE NOCASE")
            params.append(scope.publisher)

        query = f"{_BASE_QUERY} WHERE {' AND '.join(clauses)} ORDER BY i.id LIMIT 25"

        conn = self._connect()
        try:
            rows = conn.execute(query, params).fetchall()
        finally:
            conn.close()

        issues = [
            Issue(
                gcd_id=row[0],
                publisher=row[1],
                series=row[2],
                issue_number=row[3],
                on_sale_date=_parse_date(row[4]),
                printing=candidate.printing,
            )
            for row in rows
        ]
        return [
            issue
            for issue in issues
            if scope.includes_year(issue.on_sale_date.year if issue.on_sale_date else None)
        ]
