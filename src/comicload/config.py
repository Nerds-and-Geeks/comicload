from __future__ import annotations

import os
import tempfile
import tomllib
from pathlib import Path
from typing import Any

import tomli_w
from platformdirs import user_config_path, user_data_path
from pydantic import BaseModel, Field, ValidationError

from comicload.domain.errors import ConfigError


class StorageConfig(BaseModel):
    catalog_db: str = ""  # Path to local GCD mirror database
    catalogue_db: str = ""  # Path to local user review/quarantine database


class SignalsConfig(BaseModel):
    enabled: list[str] = Field(default_factory=lambda: ["barcode"])


class Config(BaseModel):
    storage: StorageConfig = Field(default_factory=StorageConfig)
    signals: SignalsConfig = Field(default_factory=SignalsConfig)

    def gcd_db_path(self) -> Path:
        if self.storage.catalog_db:
            return Path(self.storage.catalog_db).expanduser()
        return user_data_path("comicload") / "gcd.sqlite"

    def catalogue_db_path(self) -> Path:
        if self.storage.catalogue_db:
            return Path(self.storage.catalogue_db).expanduser()
        return user_data_path("comicload") / "comicload.sqlite"


def default_config_path() -> Path:
    return user_config_path("comicload") / "config.toml"


def load_config(path: Path | None = None) -> Config:
    target = path or default_config_path()
    if not target.exists():
        return Config()
    try:
        with target.open("rb") as handle:
            data = tomllib.load(handle)
        return Config.model_validate(data)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{target} is not valid TOML: {exc}") from exc
    except ValidationError as exc:
        raise ConfigError(f"{target} has an invalid configuration: {exc}") from exc


def _without_nulls(value: Any) -> Any:
    """TOML has no null. A field that is None is simply absent from the file."""
    if isinstance(value, dict):
        return {k: _without_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_without_nulls(item) for item in value if item is not None]
    return value


def _to_toml(config: Config) -> str:
    return tomli_w.dumps(_without_nulls(config.model_dump()))


def save_config(config: Config, path: Path | None = None) -> Path:
    target = path or default_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    body = _to_toml(config)

    handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(body)
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return target
