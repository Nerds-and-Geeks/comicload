from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from comicload.core.errors import CatalogError
from comicload.core.models import Candidate, Issue, Scope
from comicload.core.storage_registry import Dsn, register_resolver
from comicload.infra.storage.query import Query

_SELECT = """
SELECT i.id, p.name, s.name, i.number, i.on_sale_date
FROM issue i
JOIN series s ON s.id = i.series_id
JOIN publisher p ON p.id = s.publisher_id
"""

# One photo is one comic. More matches than this is not a longer list worth reading,
# it is a query that failed to narrow anything down.
_MAX_MATCHES = 25


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
        query = Query(select=_SELECT, order_by="i.id", limit=_MAX_MATCHES)

        if candidate.barcode:
            query = query.where("i.barcode = ?", candidate.barcode)
        else:
            if candidate.series:
                query = query.where("s.name = ? COLLATE NOCASE", candidate.series)
            if candidate.issue_number:
                query = query.where("i.number = ?", candidate.issue_number)

        if not query.predicates:
            return []

        if scope.publisher:
            query = query.where("p.name = ? COLLATE NOCASE", scope.publisher)

        sql, params = query.build()

        conn = self._connect()
        try:
            rows = conn.execute(sql, params).fetchall()
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
