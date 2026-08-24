from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from comicload.core.errors import CatalogError
from comicload.core.models import Candidate, Issue, Scope
from comicload.core.storage_registry import Dsn, register_resolver
from comicload.infra.storage.query import Query

# Aliased, and read back by those names below. Two tables here both have a `name` and an
# `id`, so a bare column list would leave the mapping to SELECT order — the same coupling
# gcd_loader refuses when it reads a dump, and for the same reason: reordering this list
# would put the publisher in the series on every issue and nothing would say a word.
_SELECT = """
SELECT i.id AS issue_id,
       p.name AS publisher_name,
       s.name AS series_name,
       i.number AS issue_number,
       i.on_sale_date AS on_sale_date
FROM issue i
JOIN series s ON s.id = i.series_id
JOIN publisher p ON p.id = s.publisher_id
"""

# One photo is one comic. More matches than this is not a longer list worth reading,
# it is a query that failed to narrow anything down.
_MAX_MATCHES = 25

# The year filter runs in SQL so that LIMIT is applied to rows the scope already kept.
# `on_sale_date` is free-form TEXT and may be NULL or unreadable, so the year comes from
# the same parser that fills Issue.on_sale_date — registered on the connection below —
# rather than from a second, subtly different notion of what a date is.
#
# NULL survives every bound on purpose: Scope.includes_year(None) is True because a
# missing date is absence of evidence, not evidence of a mismatch.
_YEAR_FUNCTION = "issue_year"
_YEAR = f"{_YEAR_FUNCTION}(i.on_sale_date)"
_YEAR_AT_LEAST = f"({_YEAR} IS NULL OR {_YEAR} >= ?)"
_YEAR_AT_MOST = f"({_YEAR} IS NULL OR {_YEAR} <= ?)"


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _year_of(raw: str | None) -> int | None:
    parsed = _parse_date(raw)
    return parsed.year if parsed else None


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
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row  # rows are read by column name, never by position
        conn.create_function(_YEAR_FUNCTION, 1, _year_of, deterministic=True)
        return conn

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
        if scope.year_from is not None:
            query = query.where(_YEAR_AT_LEAST, scope.year_from)
        if scope.year_to is not None:
            query = query.where(_YEAR_AT_MOST, scope.year_to)

        sql, params = query.build()

        conn = self._connect()
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()

        return [
            Issue(
                gcd_id=row["issue_id"],
                publisher=row["publisher_name"],
                series=row["series_name"],
                issue_number=row["issue_number"],
                on_sale_date=_parse_date(row["on_sale_date"]),
                printing=candidate.printing,
            )
            for row in rows
        ]
