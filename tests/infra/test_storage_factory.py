from pathlib import Path

from comicload.core.models import Bucket, CatalogEntry, IdentifyResult
from comicload.infra.config import Config
from comicload.infra.storage.factory import open_repository, open_resolver

ENTRY = CatalogEntry("Marvel", "The Punisher", "The Punisher #12")


def test_sqlite_repository_is_reachable_by_dsn(tmp_path):
    dsn = f"sqlite://{tmp_path / 'c.sqlite'}"
    repo = open_repository(dsn)
    repo.save([IdentifyResult("p1", "a.jpg", Bucket.CONFIDENT, entry=ENTRY)])

    assert open_repository(dsn).confirmed_entries() == [ENTRY]


def test_sqlite_resolver_is_reachable_by_dsn(tmp_path):
    from comicload.infra.storage.gcd_loader import load_dump

    db = tmp_path / "gcd.sqlite"
    load_dump(Path("tests/fixtures/gcd_sample.sql"), db)

    resolver = open_resolver(f"sqlite://{db}")
    from comicload.core.models import Candidate, Scope

    issues = resolver.resolve(
        Candidate(signal="barcode", confidence=1.0, barcode="75960608457000111"), Scope()
    )
    assert issues[0].series == "The Punisher"


def test_config_exposes_dsns_not_paths():
    config = Config()
    assert config.catalogue_dsn().startswith("sqlite://")
    assert config.catalog_dsn().startswith("sqlite://")


def test_configured_dsn_overrides_the_default():
    config = Config()
    config.storage.catalogue = "sqlite:///custom/place.sqlite"
    assert config.catalogue_dsn() == "sqlite:///custom/place.sqlite"


def test_a_non_file_dsn_survives_config_roundtrip(tmp_path):
    from comicload.infra.config import load_config, save_config

    config = Config()
    config.storage.catalogue = "postgresql://user@host/comicload"
    save_config(config, tmp_path / "c.toml")

    assert load_config(tmp_path / "c.toml").catalogue_dsn() == ("postgresql://user@host/comicload")
