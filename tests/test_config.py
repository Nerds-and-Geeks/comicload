import os

from comicload.config import Config, load_config, save_config


def test_defaults_are_usable_without_a_file(tmp_path):
    config = load_config(tmp_path / "missing.toml")
    assert config.signals.enabled == ["barcode"]


def test_roundtrip_preserves_list_and_paths(tmp_path):
    path = tmp_path / "config.toml"
    original = Config()
    original.signals.enabled = ["barcode", "ocr", "vision"]
    original.storage.catalog_db = "/tmp/gcd.sqlite"
    save_config(original, path)

    loaded = load_config(path)
    assert loaded.signals.enabled == ["barcode", "ocr", "vision"]
    assert loaded.storage.catalog_db == "/tmp/gcd.sqlite"


def test_saved_file_is_owner_only(tmp_path):
    path = tmp_path / "config.toml"
    save_config(Config(), path)
    assert (path.stat().st_mode & 0o777) == 0o600


def test_the_file_is_never_world_readable_even_for_an_instant(tmp_path, monkeypatch):
    """The mode must be right at creation, not applied by a chmod afterwards."""
    path = tmp_path / "config.toml"
    seen: list[int] = []
    real_replace = os.replace

    def spy(source, destination):
        seen.append(os.stat(source).st_mode & 0o777)
        return real_replace(source, destination)

    monkeypatch.setattr(os, "replace", spy)
    save_config(Config(), path)

    assert seen == [0o600], "the settings were readable by others before the chmod landed"
    assert (path.stat().st_mode & 0o777) == 0o600
