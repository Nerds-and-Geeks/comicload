from __future__ import annotations

import tomllib
from pathlib import Path

from platformdirs import user_config_path, user_data_path
from pydantic import BaseModel, Field, ValidationError

from comicload.core.errors import ConfigError


class ExportConfig(BaseModel):
    sink: str = "csv"


class LocgConfig(BaseModel):
    state_file: str = ""
    confirm_before_import: bool = True


class CatalogConfig(BaseModel):
    gcd_db: str = ""
    catalogue_db: str = ""


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
    catalog: CatalogConfig = Field(default_factory=CatalogConfig)
    scan: ScanConfig = Field(default_factory=ScanConfig)
    signals: SignalsConfig = Field(default_factory=SignalsConfig)
    llm: LlmConfig = Field(default_factory=LlmConfig)

    def gcd_db_path(self) -> Path:
        if self.catalog.gcd_db:
            return Path(self.catalog.gcd_db).expanduser()
        return user_data_path("comicload") / "gcd.sqlite"

    def catalogue_db_path(self) -> Path:
        if self.catalog.catalogue_db:
            return Path(self.catalog.catalogue_db).expanduser()
        return user_data_path("comicload") / "comicload.sqlite"

    def locg_state_path(self) -> Path:
        if self.locg.state_file:
            return Path(self.locg.state_file).expanduser()
        return user_config_path("comicload") / "locg_state.json"


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
