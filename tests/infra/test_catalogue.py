from datetime import date

import pytest

from comicload.core.errors import CatalogError
from comicload.core.models import Bucket, Candidate, CatalogEntry, IdentifyResult
from comicload.infra.storage.catalogue import SqliteRepository

ENTRY = CatalogEntry(
    publisher_name="Marvel",
    series_name="The Punisher",
    full_title="The Punisher #12",
    release_date=date(2001, 3, 1),
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
def repo(tmp_path):
    return SqliteRepository(tmp_path / "comicload.sqlite")


def test_creates_database_on_first_save(tmp_path):
    db = tmp_path / "nested" / "comicload.sqlite"
    SqliteRepository(db).save([CONFIDENT])
    assert db.exists()


def test_confirmed_entries_returns_only_confident(repo):
    repo.save([CONFIDENT, AMBIGUOUS, UNRECOGNIZED])
    entries = repo.confirmed_entries()
    assert entries == [ENTRY]


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
    import sqlite3

    from comicload.infra.storage.catalogue import SCHEMA_VERSION

    db = tmp_path / "comicload.sqlite"
    SqliteRepository(db).save([])
    version = sqlite3.connect(db).execute("PRAGMA user_version").fetchone()[0]
    assert version == SCHEMA_VERSION


def test_existing_data_survives_reopening(tmp_path):
    """Opening an already-migrated database must not reset or drop anything."""
    db = tmp_path / "comicload.sqlite"
    SqliteRepository(db).save([CONFIDENT])
    assert SqliteRepository(db).confirmed_entries() == [ENTRY]


def test_migration_from_version_zero_is_applied(tmp_path):
    """A pre-versioned database is migrated up, not wiped."""
    import sqlite3

    from comicload.infra.storage.catalogue import SCHEMA_VERSION

    db = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE scan_result ("
        "photo_id TEXT PRIMARY KEY, filename TEXT NOT NULL, bucket TEXT NOT NULL,"
        "entry TEXT, candidates TEXT NOT NULL DEFAULT '[]');"
    )
    conn.execute("INSERT INTO scan_result VALUES ('p9', 'old.jpg', 'unrecognized', NULL, '[]')")
    conn.commit()
    conn.close()

    repo = SqliteRepository(db)
    pending = repo.pending_review()

    assert [r.photo_id for r in pending] == ["p9"], "existing row was lost during migration"
    version = sqlite3.connect(db).execute("PRAGMA user_version").fetchone()[0]
    assert version == SCHEMA_VERSION


# --- reads must never conjure a database out of a mistyped path ---------------


def test_pending_review_on_a_missing_database_raises_rather_than_creating_one(tmp_path):
    """`review --db /path/with/typo` must not answer 'everything was identified'."""
    missing = tmp_path / "typo.sqlite"
    with pytest.raises(CatalogError, match="no catalogue"):
        SqliteRepository(missing).pending_review()
    assert not missing.exists()


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
