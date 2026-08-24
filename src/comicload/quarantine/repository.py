"""The user's own catalogue: scan outcomes and the review queue.

Deliberately a separate database from the GCD mirror. `gcd.sqlite` is a disposable
mirror rebuilt by `catalog sync`; this file holds the user's irreplaceable results.
"""

from __future__ import annotations

import dataclasses
import io
import json
import re
import sqlite3
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from PIL import Image

from comicload.errors import CatalogError
from comicload.models import Bucket, Candidate, CatalogEntry, IdentifyResult


def _clean_entry_title(entry: CatalogEntry) -> CatalogEntry:
    """Clean LoCG title formatting on stored catalogue entries."""
    publisher = entry.publisher_name
    if publisher == "DC":
        publisher = "DC Comics"
    elif publisher == "Marvel":
        publisher = "Marvel Comics"

    series = (
        entry.series_name.replace(" - The Deluxe Edition", "")
        .replace(" - Deluxe Edition", "")
        .strip()
    )
    title = (
        entry.full_title.replace(" - The Deluxe Edition", "")
        .replace(" - Deluxe Edition", "")
        .strip()
    )

    # Strip printing suffixes like " 1st Printing"
    title = re.sub(r"\s+\d+(st|nd|rd|th)\s+Printing", "", title, flags=re.IGNORECASE)

    # Strip legacy dual parens like " (863)"
    title = re.sub(r"\s*\(\d+\)", "", title)

    # Strip unnumbered trade "[nn]"
    is_trade = "[nn]" in title.lower() or "#" not in title
    title = re.sub(r"\s*#?\[nn\]", "", title, flags=re.IGNORECASE).strip()

    media_format = entry.media_format
    if not media_format or media_format == "Comic":
        if is_trade:
            is_hc = "deluxe" in entry.series_name.lower() or "hc" in entry.series_name.lower()
            media_format = "Hardcover" if is_hc else "Trade Paperback"
        else:
            media_format = "Comic"

    return dataclasses.replace(
        entry,
        publisher_name=publisher,
        series_name=series,
        full_title=title,
        media_format=media_format,
    )


SCHEMA_VERSION = 3

# Index = the version a script migrates FROM. MIGRATIONS[0] takes an empty or
# pre-versioned database (version 0) to version 1. To evolve the schema, append a new
# script and bump SCHEMA_VERSION — never edit an existing entry, since users have
# already run it. Statements are split on ';', so keep semicolons out of string
# literals inside a migration script.
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
    """
    ALTER TABLE scan_result
        ADD COLUMN signal_failures TEXT NOT NULL DEFAULT '[]';
    """,
    """
    ALTER TABLE scan_result
        ADD COLUMN image BLOB;
    """,
)


def _statements(script: str) -> list[str]:
    return [statement.strip() for statement in script.split(";") if statement.strip()]


def _apply(conn: sqlite3.Connection, from_version: int) -> None:
    """Run one migration and its version stamp as a single transaction.

    executescript() commits before it runs, which left the stamp as a separate commit: a
    migration that failed half way through kept its partial schema changes while
    user_version stayed stale, so every later launch re-ran the same migration and failed
    the same way, forever, on a database the user cannot regenerate.
    """
    previous = conn.isolation_level
    conn.isolation_level = None  # take explicit control; DDL included
    try:
        conn.execute("BEGIN")
        try:
            for statement in _statements(MIGRATIONS[from_version]):
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {from_version + 1}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.isolation_level = previous


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring a database up to SCHEMA_VERSION, preserving existing rows.

    The user's catalogue cannot be regenerated, so schema changes must migrate
    rather than recreate. SQLite's user_version pragma is the stamp.
    """
    current: int = conn.execute("PRAGMA user_version").fetchone()[0]
    if current > SCHEMA_VERSION:
        raise CatalogError(
            f"this catalogue was written by a newer comicload (schema version {current}; "
            f"this one understands {SCHEMA_VERSION}). Upgrade comicload, or point --db at "
            "a different file — an older comicload must not write to it."
        )
    for version in range(current, SCHEMA_VERSION):
        _apply(conn, version)


_THUMBNAIL_MAX = 1000  # px on the long side — recognisable, ~100KB, not 5MB of scan


def _thumbnail(image: bytes | None) -> bytes | None:
    """Shrink quarantined cover pixels to a bounded JPEG before storing them."""
    if not image:
        return None
    try:
        with Image.open(io.BytesIO(image)) as source:
            source.thumbnail((_THUMBNAIL_MAX, _THUMBNAIL_MAX))
            out = io.BytesIO()
            source.convert("RGB").save(out, format="JPEG", quality=82)
            return out.getvalue()
    except Exception:
        return None


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


class SqliteRepository:
    """Stores identification outcomes so the review queue survives between runs."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)

    def _connect(self, *, create: bool) -> sqlite3.Connection:
        """Open the catalogue. Writes may create it; reads must not.

        Reading from a database that is not there means the path is wrong or nothing has
        been scanned yet. Creating one on the way past would turn a typo into an empty
        catalogue and an affirmative "everything was identified".
        """
        if not create and (not self._db_path.exists() or self._db_path.stat().st_size == 0):
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
                json.dumps(list(result.signal_failures)),
                _thumbnail(result.image) if result.bucket is not Bucket.CONFIDENT else None,
            )
            for result in results
        ]
        conn = self._connect(create=True)
        try:
            conn.executemany(
                """
                INSERT INTO scan_result
                    (photo_id, filename, bucket, entry, candidates, signal_failures, image)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(photo_id) DO UPDATE SET
                    filename        = excluded.filename,
                    bucket          = excluded.bucket,
                    entry           = excluded.entry,
                    candidates      = excluded.candidates,
                    signal_failures = excluded.signal_failures,
                    image           = excluded.image
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
                "SELECT photo_id, filename, bucket, entry, candidates, signal_failures, image "
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
                signal_failures=tuple(json.loads(row[5] or "[]")),
                image=row[6],
            )
            for row in rows
        ]

    def clear(self) -> None:
        """Clear all scan results and quarantine entries."""
        if not self._db_path.exists():
            return
        conn = self._connect(create=True)
        try:
            conn.execute("DELETE FROM scan_result")
            conn.commit()
        finally:
            conn.close()

    def clear_pending(self) -> None:
        """Clear unconfirmed/quarantined scan results from previous runs."""
        if not self._db_path.exists():
            return
        conn = self._connect(create=True)
        try:
            conn.execute(
                "DELETE FROM scan_result WHERE bucket != ?",
                (Bucket.CONFIDENT.value,),
            )
            conn.commit()
        finally:
            conn.close()

    def pending_review(self) -> list[IdentifyResult]:
        return self._select("bucket != ?", [Bucket.CONFIDENT.value])

    def confirmed_entries(self) -> list[CatalogEntry]:
        results = self._select("bucket = ?", [Bucket.CONFIDENT.value])
        return [_clean_entry_title(r.entry) for r in results if r.entry is not None]
