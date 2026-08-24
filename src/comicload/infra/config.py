from __future__ import annotations

import tomllib
from pathlib import Path

from platformdirs import user_config_path, user_data_path
from pydantic import BaseModel, Field, ValidationError

from comicload.core.errors import ConfigError
from comicload.core.storage_registry import parse_dsn


class ExportConfig(BaseModel):
    sink: str = "csv"


class LocgConfig(BaseModel):
    state_file: str = ""
    confirm_before_import: bool = True


class StorageConfig(BaseModel):
    """Storage addresses, not paths: the scheme picks the backend, the rest is its business."""

    catalog: str = ""  # the GCD mirror — disposable
    catalogue: str = ""  # the user's own results — precious


class ScanConfig(BaseModel):
    default_publisher: str = ""
    default_years: str = ""

    def year_bounds(self) -> tuple[int | None, int | None]:
        raw = self.default_years.strip()
        if not raw:
            return (None, None)
        parts = raw.split("-")
        if len(parts) != 2 or not all(p.strip().isdigit() for p in parts):
            raise ValueError(f"default_years must look like '1970-1985', got {raw!r}")
        return (int(parts[0]), int(parts[1]))


class SignalsConfig(BaseModel):
    enabled: list[str] = Field(default_factory=lambda: ["barcode"])


class LlmConfig(BaseModel):
    enabled: bool = False
    provider: str = "anthropic"
    model: str = "claude-haiku-4-5"
    secret_name: str = "comicload/anthropic"


class Config(BaseModel):
    export: ExportConfig = Field(default_factory=ExportConfig)
    locg: LocgConfig = Field(default_factory=LocgConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    scan: ScanConfig = Field(default_factory=ScanConfig)
    signals: SignalsConfig = Field(default_factory=SignalsConfig)
    llm: LlmConfig = Field(default_factory=LlmConfig)

    def catalog_dsn(self) -> str:
        if self.storage.catalog:
            return self.storage.catalog
        return f"sqlite://{user_data_path('comicload') / 'gcd.sqlite'}"

    def catalogue_dsn(self) -> str:
        if self.storage.catalogue:
            return self.storage.catalogue
        return f"sqlite://{user_data_path('comicload') / 'comicload.sqlite'}"

    def gcd_db_path(self) -> Path:
        """`catalog sync` writes a SQLite file, so it needs a real path, not just an address."""
        return sqlite_path(self.catalog_dsn())

    def locg_state_path(self) -> Path:
        if self.locg.state_file:
            return Path(self.locg.state_file).expanduser()
        return user_config_path("comicload") / "locg_state.json"


def sqlite_path(dsn: str) -> Path:
    """The local file behind a `sqlite://` address.

    Only for work that genuinely needs a file on disk — building the GCD mirror. Everything
    else goes through the storage registry and never learns whether a file is involved.
    """
    parsed = parse_dsn(dsn)
    if parsed.scheme != "sqlite":
        raise ConfigError(
            f"'comicload catalog sync' builds a local SQLite file, but {dsn!r} is not a "
            f"sqlite:// address; point the catalog at a sqlite:// address to sync it"
        )
    return Path(parsed.target)


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


def _to_toml(config: Config) -> str:
    lines: list[str] = []
    for section, values in config.model_dump().items():
        lines.append(f"[{section}]")
        for key, value in values.items():
            if isinstance(value, bool):
                lines.append(f"{key} = {str(value).lower()}")
            elif isinstance(value, list):
                rendered = ", ".join(f'"{item}"' for item in value)
                lines.append(f"{key} = [{rendered}]")
            else:
                lines.append(f'{key} = "{value}"')
        lines.append("")
    return "\n".join(lines)


def save_config(config: Config, path: Path | None = None) -> Path:
    target = path or default_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_to_toml(config))
    target.chmod(0o600)
    return target
