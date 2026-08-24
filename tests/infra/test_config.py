import pytest

from comicload.infra.config import Config, load_config, save_config
from comicload.infra.secrets import KeyringSecretStore


def test_defaults_are_usable_without_a_file(tmp_path):
    config = load_config(tmp_path / "missing.toml")
    assert config.export.sink == "csv"
    assert config.signals.enabled == ["barcode"]
    assert config.llm.enabled is False


def test_roundtrip_preserves_values(tmp_path):
    path = tmp_path / "config.toml"
    original = Config()
    original.scan.default_publisher = "marvel"
    original.scan.default_years = "1970-1985"
    save_config(original, path)

    loaded = load_config(path)
    assert loaded.scan.default_publisher == "marvel"
    assert loaded.scan.default_years == "1970-1985"


def test_roundtrip_preserves_list_and_boolean_fields(tmp_path):
    path = tmp_path / "config.toml"
    original = Config()
    original.signals.enabled = ["barcode", "ocr", "vision"]
    original.locg.confirm_before_import = False
    save_config(original, path)

    loaded = load_config(path)
    assert loaded.signals.enabled == ["barcode", "ocr", "vision"]
    assert loaded.locg.confirm_before_import is False


def test_saved_file_is_owner_only(tmp_path):
    path = tmp_path / "config.toml"
    save_config(Config(), path)
    assert (path.stat().st_mode & 0o777) == 0o600


def test_secret_values_are_never_written_to_config(tmp_path):
    path = tmp_path / "config.toml"
    config = Config()
    config.llm.secret_name = "comicload/anthropic"
    save_config(config, path)

    text = path.read_text()
    assert "comicload/anthropic" in text
    assert "secret_value" not in text
    assert "api_key" not in text


def test_year_range_parses_into_scope():
    config = Config()
    config.scan.default_years = "1970-1985"
    assert config.scan.year_bounds() == (1970, 1985)


def test_blank_year_range_is_unbounded():
    assert Config().scan.year_bounds() == (None, None)


def test_malformed_year_range_raises():
    config = Config()
    config.scan.default_years = "not-a-range"
    with pytest.raises(ValueError, match="1970-1985"):
        config.scan.year_bounds()


def test_secret_store_roundtrip_in_memory():
    store = KeyringSecretStore(backend={})
    store.set("comicload/test", "s3cret")
    assert store.get("comicload/test") == "s3cret"
    store.delete("comicload/test")
    assert store.get("comicload/test") is None
