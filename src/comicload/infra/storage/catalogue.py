"""The user's own catalogue: scan outcomes and the review queue.

Deliberately a separate database from the GCD mirror. `gcd.sqlite` is a disposable
mirror rebuilt by `catalog sync`; this file holds the user's irreplaceable results.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from comicload.core.errors import CatalogError
from comicload.core.models import Bucket, Candidate, CatalogEntry, IdentifyResult
from comicload.core.storage_registry import Dsn, register_repository

SCHEMA_VERSION = 1

# Index = target version. MIGRATIONS[0] takes an empty or pre-versioned database to
# version 1. To evolve the schema, append a new script and bump SCHEMA_VERSION —
# never edit an existing entry, since users have already run it.
MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS scan_result (
        photo_id   TEXT PRIMARY KEY,
        filename   TEXT NOT NULL,
        bucket     TEXT NOT NULL,
        entry      TEXT,
        candidates TEXT NOT NULL DEFAULT '[]'
    );
    CREATE INDEX IF NOT EXISTS idx_scan_result_bucket ON scan_result(bucket);
    """,
)


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring a database up to SCHEMA_VERSION, preserving existing rows.

    The user's catalogue cannot be regenerated, so schema changes must migrate
    rather than recreate. SQLite's user_version pragma is the stamp.
    """
    current: int = conn.execute("PRAGMA user_version").fetchone()[0]
    for version in range(current, SCHEMA_VERSION):
        conn.executescript(MIGRATIONS[version])
    if current < SCHEMA_VERSION:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()


def _entry_to_json(entry: CatalogEntry | None) -> str | None:
    if entry is None:
        return None
    raw = dataclasses.asdict(entry)
    if raw["release_date"] is not None:
        raw["release_date"] = raw["release_date"].isoformat()
    return json.dumps(raw)


def _entry_from_json(blob: str | None) -> CatalogEntry | None:
    if not blob:
        return None
    raw: dict[str, Any] = json.loads(blob)
    if raw.get("release_date"):
        raw["release_date"] = date.fromisoformat(raw["release_date"])
    return CatalogEntry(**raw)


def _candidates_to_json(candidates: Sequence[Candidate]) -> str:
    return json.dumps([dataclasses.asdict(c) for c in candidates])


def _candidates_from_json(blob: str) -> tuple[Candidate, ...]:
    return tuple(Candidate(**raw) for raw in json.loads(blob or "[]"))


@register_repository("sqlite")
class SqliteRepository:
    """Stores identification outcomes so the review queue survives between runs."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)

    @classmethod
    def from_dsn(cls, dsn: Dsn) -> SqliteRepository:
        return cls(Path(dsn.target))

    def _connect(self, *, create: bool) -> sqlite3.Connection:
        """Open the catalogue. Writes may create it; reads must not.

        Reading from a database that is not there means the path is wrong or nothing has
        been scanned yet. Creating one on the way past would turn a typo into an empty
        catalogue and an affirmative "everything was identified".
        """
        if not create and not self._db_path.exists():
            raise CatalogError(
                f"no catalogue at {self._db_path}\n"
                "Run 'comicload scan' on a folder of photos first — or check that path, "
                "it may not be the one your scans went to."
            )
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        _migrate(conn)
        return conn

    def save(self, results: Sequence[IdentifyResult]) -> None:
        rows = [
            (
                result.photo_id,
                result.filename,
                result.bucket.value,
                _entry_to_json(result.entry),
                _candidates_to_json(result.candidates),
            )
            for result in results
        ]
        conn = self._connect(create=True)
        try:
            conn.executemany(
                """
                INSERT INTO scan_result (photo_id, filename, bucket, entry, candidates)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(photo_id) DO UPDATE SET
                    filename   = excluded.filename,
                    bucket     = excluded.bucket,
                    entry      = excluded.entry,
                    candidates = excluded.candidates
                """,
                rows,
            )
            conn.commit()
        finally:
            conn.close()

    def _select(self, where: str, params: Sequence[str]) -> list[IdentifyResult]:
        conn = self._connect(create=False)
        try:
            rows = conn.execute(
                "SELECT photo_id, filename, bucket, entry, candidates "
                f"FROM scan_result WHERE {where} ORDER BY filename",
                params,
            ).fetchall()
        finally:
            conn.close()
        return [
            IdentifyResult(
                photo_id=row[0],
                filename=row[1],
                bucket=Bucket(row[2]),
                entry=_entry_from_json(row[3]),
                candidates=_candidates_from_json(row[4]),
            )
            for row in rows
        ]

    def pending_review(self) -> list[IdentifyResult]:
        return self._select("bucket != ?", [Bucket.CONFIDENT.value])

    def confirmed_entries(self) -> list[CatalogEntry]:
        results = self._select("bucket = ?", [Bucket.CONFIDENT.value])
        return [r.entry for r in results if r.entry is not None]
