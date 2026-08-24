import os

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


# --- the settings file must survive the characters a real name contains -------
# The hand-rolled writer quoted every value with a bare `"` and escaped nothing, so each
# of these produced a file that load_config() then refused to read.


@pytest.mark.parametrize(
    "value",
    [
        'My "Comics"',
        r"C:\Users\me\comics",
        "line one\nline two",
        "shelf [A]",
        "issue #12",
        "tab\tseparated",
        "back\\slash",
        "quote ' and \" together",
    ],
    ids=[
        "double-quotes",
        "windows-path",
        "embedded-newline",
        "square-bracket",
        "hash",
        "tab",
        "backslash",
        "both-quotes",
    ],
)
def test_awkward_values_round_trip(tmp_path, value):
    path = tmp_path / "config.toml"
    original = Config()
    original.scan.default_publisher = value
    save_config(original, path)

    assert load_config(path).scan.default_publisher == value


def test_awkward_values_round_trip_inside_a_list(tmp_path):
    path = tmp_path / "config.toml"
    original = Config()
    original.signals.enabled = ['bar"code', "oc\\r", "vis\nion"]
    save_config(original, path)

    assert load_config(path).signals.enabled == ['bar"code', "oc\\r", "vis\nion"]


def test_booleans_stay_booleans_rather_than_becoming_strings(tmp_path):
    path = tmp_path / "config.toml"
    config = Config()
    config.locg.confirm_before_import = False
    save_config(config, path)

    assert 'confirm_before_import = "False"' not in path.read_text()
    assert load_config(path).locg.confirm_before_import is False


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
