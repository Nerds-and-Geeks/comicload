import io
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from PIL import Image as PILImage

from comicload.errors import CatalogError
from comicload.models import Bucket, Candidate, CatalogEntry, IdentifyResult
from comicload.quarantine import repository as catalogue
from comicload.quarantine.repository import MIGRATIONS, SCHEMA_VERSION, SqliteRepository

ENTRY = CatalogEntry(
    publisher_name="Marvel Comics",
    series_name="The Punisher",
    full_title="The Punisher #12",
    release_date=date(2001, 3, 1),
    media_format="Comic",
    tags="comicload;photo=a.jpg;signal=barcode;conf=0.95",
)
CONFIDENT = IdentifyResult("p1", "a.jpg", Bucket.CONFIDENT, entry=ENTRY)
AMBIGUOUS = IdentifyResult(
    "p2",
    "b.jpg",
    Bucket.AMBIGUOUS,
    candidates=(Candidate(signal="barcode", confidence=0.5, barcode="X1"),),
)
UNRECOGNIZED = IdentifyResult("p3", "c.jpg", Bucket.UNRECOGNIZED)


@pytest.fixture
def repo(tmp_path: Path) -> SqliteRepository:
    return SqliteRepository(tmp_path / "comicload.sqlite")


def _user_version(db_path: Path) -> int:
    """Helper to inspect database user_version pragma."""
    conn = sqlite3.connect(db_path)
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


# The schema exactly as unversioned comicload (before PRAGMA user_version) wrote it.
_V0_SCHEMA = (
    "CREATE TABLE scan_result ("
    "photo_id TEXT PRIMARY KEY, filename TEXT NOT NULL, bucket TEXT NOT NULL,"
    "entry TEXT, candidates TEXT NOT NULL DEFAULT '[]');"
)


def catalogue_at_version(db_path: Path, version: int, rows: list[tuple[str, str, str]]) -> Path:
    """Build a catalogue database exactly as schema version N of comicload wrote it.

    One builder for every era: version 0 is the pre-versioned schema, any later
    version applies the real migration scripts up to N and stamps it. Tests describe
    the era and the rows; the SQL lives here, once.
    """
    conn = sqlite3.connect(db_path)
    if version == 0:
        conn.executescript(_V0_SCHEMA)
    else:
        for script in MIGRATIONS[:version]:
            conn.executescript(script)
        conn.execute(f"PRAGMA user_version = {version}")
    for photo_id, filename, bucket in rows:
        conn.execute(
            "INSERT INTO scan_result (photo_id, filename, bucket, entry, candidates)"
            " VALUES (?, ?, ?, NULL, '[]')",
            (photo_id, filename, bucket),
        )
    conn.commit()
    conn.close()
    return db_path


def test_creates_database_on_first_save(tmp_path):
    db = tmp_path / "nested" / "comicload.sqlite"
    SqliteRepository(db).save([CONFIDENT])
    assert db.exists()


def test_confirmed_entries_returns_only_confident(repo: SqliteRepository):
    repo.save([CONFIDENT, AMBIGUOUS, UNRECOGNIZED])
    entries = repo.confirmed_entries()
    assert entries == [ENTRY]


def test_confirmed_entries_cleans_stored_titles(repo: SqliteRepository):
    old_entry = CatalogEntry(
        publisher_name="DC",
        series_name="Supergirl: Woman of Tomorrow - The Deluxe Edition",
        full_title="Supergirl #2 1st Printing",
        release_date=date(2025, 6, 11),
    )
    repo.save([IdentifyResult("p10", "old.jpg", Bucket.CONFIDENT, entry=old_entry)])
    entries = repo.confirmed_entries()
    assert entries[0].full_title == "Supergirl #2"
    assert entries[0].series_name == "Supergirl: Woman of Tomorrow"
    assert entries[0].publisher_name == "DC Comics"


def test_pending_review_excludes_confident(repo):
    repo.save([CONFIDENT, AMBIGUOUS, UNRECOGNIZED])
    pending = repo.pending_review()
    assert {r.photo_id for r in pending} == {"p2", "p3"}


def test_entry_roundtrips_including_release_date(repo):
    repo.save([CONFIDENT])
    assert repo.confirmed_entries()[0].release_date == date(2001, 3, 1)


def test_candidates_roundtrip_for_review(repo):
    repo.save([AMBIGUOUS])
    pending = repo.pending_review()
    assert pending[0].candidates[0].barcode == "X1"
    assert pending[0].candidates[0].signal == "barcode"


def test_rescanning_same_photo_updates_rather_than_duplicates(repo):
    repo.save([AMBIGUOUS])
    resolved = IdentifyResult("p2", "b.jpg", Bucket.CONFIDENT, entry=ENTRY)
    repo.save([resolved])

    assert repo.pending_review() == []
    assert len(repo.confirmed_entries()) == 1


def test_empty_database_returns_empty_lists(repo):
    repo.save([])  # a scan that found no photos still creates the catalogue
    assert repo.pending_review() == []
    assert repo.confirmed_entries() == []


def test_saving_nothing_is_harmless(repo):
    repo.save([])
    assert repo.confirmed_entries() == []


def test_new_database_is_stamped_with_current_schema_version(tmp_path):
    db = tmp_path / "comicload.sqlite"
    SqliteRepository(db).save([])
    assert _user_version(db) == SCHEMA_VERSION


def test_existing_data_survives_reopening(tmp_path):
    """Opening an already-migrated database must not reset or drop anything."""
    db = tmp_path / "comicload.sqlite"
    SqliteRepository(db).save([CONFIDENT])
    assert SqliteRepository(db).confirmed_entries() == [ENTRY]


def test_migration_from_version_zero_is_applied(tmp_path):
    """A pre-versioned database is migrated up, not wiped."""
    db = catalogue_at_version(tmp_path / "legacy.sqlite", 0, [("p9", "old.jpg", "unrecognized")])
    repo = SqliteRepository(db)
    pending = repo.pending_review()

    assert [r.photo_id for r in pending] == ["p9"], "existing row was lost during migration"
    assert _user_version(db) == SCHEMA_VERSION


# --- reads must never conjure a database out of a mistyped path ---------------


def test_pending_review_on_a_missing_database_raises_rather_than_creating_one(tmp_path):
    """`review --db /path/with/typo` must not answer 'everything was identified'."""
    missing = tmp_path / "typo.sqlite"
    with pytest.raises(CatalogError, match="no catalogue"):
        SqliteRepository(missing).pending_review()
    assert not missing.exists()


def test_pending_review_on_a_zero_byte_database_raises_catalog_error(tmp_path):
    empty = tmp_path / "empty.sqlite"
    empty.touch()
    with pytest.raises(CatalogError, match="no catalogue"):
        SqliteRepository(empty).pending_review()


def test_confirmed_entries_on_a_missing_database_raises_rather_than_creating_one(tmp_path):
    missing = tmp_path / "typo.sqlite"
    with pytest.raises(CatalogError, match="comicload scan"):
        SqliteRepository(missing).confirmed_entries()
    assert not missing.exists()


def test_saving_still_creates_the_database(tmp_path):
    """Writes may create; only reads may not."""
    db = tmp_path / "fresh" / "comicload.sqlite"
    SqliteRepository(db).save([])
    assert db.exists()
    assert SqliteRepository(db).pending_review() == []


# --- a migration must not be able to wedge an irreplaceable database ----------


BROKEN_MIGRATION = "CREATE TABLE extra (id INTEGER); INSERT INTO no_such_table VALUES (1);"


def test_a_failing_migration_leaves_the_database_unchanged_and_re_runnable(tmp_path, monkeypatch):
    """executescript() auto-commits, so a half-applied migration used to persist forever."""
    db = tmp_path / "comicload.sqlite"
    SqliteRepository(db).save([CONFIDENT])

    original_version = catalogue.SCHEMA_VERSION
    monkeypatch.setattr(catalogue, "MIGRATIONS", (*catalogue.MIGRATIONS, BROKEN_MIGRATION))
    monkeypatch.setattr(catalogue, "SCHEMA_VERSION", catalogue.SCHEMA_VERSION + 1)

    with pytest.raises(sqlite3.OperationalError):
        SqliteRepository(db).pending_review()

    assert _user_version(db) == original_version, "stamp moved despite failure"
    conn = sqlite3.connect(db)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "extra" not in tables, "a partial schema change survived the failed migration"
    conn.close()

    monkeypatch.undo()
    assert SqliteRepository(db).confirmed_entries() == [ENTRY]


def test_a_catalogue_from_a_newer_comicload_is_refused(tmp_path):
    """Better a clear refusal than a confusing OperationalError against precious data."""
    db = tmp_path / "comicload.sqlite"
    SqliteRepository(db).save([CONFIDENT])
    conn = sqlite3.connect(db)
    conn.execute(f"PRAGMA user_version = {catalogue.SCHEMA_VERSION + 1}")
    conn.commit()
    conn.close()

    with pytest.raises(CatalogError, match="newer comicload"):
        SqliteRepository(db).pending_review()
    with pytest.raises(CatalogError, match="newer comicload"):
        SqliteRepository(db).save([CONFIDENT])


def test_the_migrations_comment_matches_the_indexing():
    """MIGRATIONS[n] migrates FROM version n; the comment used to say the opposite."""
    assert len(catalogue.MIGRATIONS) == catalogue.SCHEMA_VERSION


def test_signal_failures_survive_persistence(tmp_path):
    """I3: the fix that surfaced signal failures only covered the screen where the
    failure happened — the persisted row silently dropped them."""
    repo = SqliteRepository(tmp_path / "c.sqlite")
    repo.save(
        [
            IdentifyResult(
                "p1",
                "a.jpg",
                Bucket.UNRECOGNIZED,
                signal_failures=("barcode", "ocr"),
            )
        ]
    )
    pending = repo.pending_review()
    assert pending[0].signal_failures == ("barcode", "ocr")


def test_a_version_one_catalogue_is_migrated_and_keeps_its_rows(tmp_path):
    """The v2 migration adds the signal_failures column without touching data."""
    db = catalogue_at_version(tmp_path / "v1.sqlite", 1, [("p9", "old.jpg", "unrecognized")])
    repo = SqliteRepository(db)
    pending = repo.pending_review()
    assert [r.photo_id for r in pending] == ["p9"]
    assert pending[0].signal_failures == ()


def test_quarantined_rows_keep_a_viewable_image(tmp_path):
    """Review happens days later when the source folder may be gone — the catalogue
    must be able to show the cover it is asking about."""
    buffer = io.BytesIO()
    PILImage.new("RGB", (2000, 3000), (200, 40, 40)).save(buffer, format="PNG")
    repo = SqliteRepository(tmp_path / "c.sqlite")
    repo.save([IdentifyResult("p1", "a.jpg", Bucket.UNRECOGNIZED, image=buffer.getvalue())])
    stored = repo.pending_review()[0].image
    assert stored is not None
    with PILImage.open(io.BytesIO(stored)) as thumb:
        assert max(thumb.size) <= 1000, "image must be stored as a bounded thumbnail"


def test_identified_rows_do_not_hoard_pixels(tmp_path):
    repo = SqliteRepository(tmp_path / "c.sqlite")
    repo.save([IdentifyResult("p1", "a.jpg", Bucket.CONFIDENT, entry=ENTRY, image=b"x" * 100)])
    conn = sqlite3.connect(tmp_path / "c.sqlite")
    blob = conn.execute("SELECT image FROM scan_result WHERE photo_id='p1'").fetchone()[0]
    conn.close()
    assert blob is None


def test_a_version_two_catalogue_is_migrated_and_keeps_its_rows(tmp_path):
    db = catalogue_at_version(tmp_path / "v2.sqlite", 2, [("p9", "old.jpg", "unrecognized")])
    repo = SqliteRepository(db)
    pending = repo.pending_review()
    assert [r.photo_id for r in pending] == ["p9"]
    assert pending[0].image is None
