# comicload Phase 1 — Scan to CSV, Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working `comicload scan ./photos -o out.csv` that identifies barcoded comics against a local GCD mirror and writes a League of Comic Geeks import CSV, plus `comicload import out.csv` that validates it.

**Architecture:** Hexagonal. `core/` holds pure domain types and Protocol ports. `services/` orchestrates against ports only. `infra/` implements ports (photo source, signals, storage, sinks, secrets). `adapters/cli/` is the only place Rich or Typer appear. A future web adapter is a sibling of `adapters/cli/` reusing every service unchanged.

**Tech Stack:** Python 3.12, Typer, Rich, pydantic (config only), pyzbar + Pillow (barcode), SQLite (stdlib), keyring, pytest, ruff, mypy.

## Global Constraints

- Python 3.12+. `src/` layout. Package name `comicload`.
- **No `print()` or Rich imports in `core/`, `services/`, or `infra/`.** Progress goes through the `ProgressReporter` port. Enforced by a test in Task 3.
- **Dependencies point inward.** `core/` imports only stdlib. `services/` imports only `core/`. `infra/` imports `core/`. `adapters/` may import anything. Enforced by a test in Task 3.
- **Domain types are frozen stdlib dataclasses.** No pydantic in `core/` — pydantic is config-only.
- **Adding a signal or sink requires zero edits to existing files** — registration by decorator.
- Every task ends with a green `pytest` run and a commit.
- Secrets never written to `config.toml`. Only secret *names* go there.
- Conventional Commits. Imperative mood.
- Test fixtures live in `tests/fixtures/`. Fixture CSVs are force-added past `.gitignore`.

---

## File Structure

```
pyproject.toml
README.md
src/comicload/
  __init__.py
  core/
    __init__.py
    models.py         # frozen dataclasses: Photo, Candidate, Issue, CatalogEntry, Scope, ImportResult, IdentifyResult, Bucket
    ports.py          # Protocols: PhotoSource, Signal, IssueResolver, Sink, Repository, ProgressReporter, SecretStore
    registry.py       # register_signal / register_sink / get_signal / get_sink
    errors.py         # ComicloadError hierarchy
  services/
    __init__.py
    identify.py       # IdentifyService
    export.py         # ExportService
    catalog.py        # CatalogService
  infra/
    __init__.py
    photos.py         # LocalFolderPhotoSource
    signals/
      __init__.py
      barcode.py      # BarcodeSignal + UPC supplement fallback
    storage/
      __init__.py
      gcd_loader.py   # MySQL dump → SQLite
      gcd_repo.py     # SqliteIssueResolver
    sinks/
      __init__.py
      csv_sink.py     # CsvSink — 14 columns
    secrets.py        # KeyringSecretStore
    config.py         # pydantic config model + TOML load/save
  adapters/
    __init__.py
    cli/
      __init__.py
      app.py          # Typer app, wires everything
      progress.py     # RichProgressReporter
      render.py       # tables, panels, result rendering
tests/
  conftest.py
  fixtures/
    locg_export_header.csv
    gcd_sample.sql
    photos/
  test_architecture.py
  core/test_models.py
  core/test_registry.py
  services/test_identify.py
  services/test_export.py
  services/test_catalog.py
  infra/test_photos.py
  infra/test_barcode.py
  infra/test_gcd_loader.py
  infra/test_csv_sink.py
  infra/test_config.py
  adapters/test_cli.py
```

---

### Task 1: Project scaffold and tooling

**Files:**
- Create: `pyproject.toml`, `src/comicload/__init__.py`, `tests/conftest.py`, `tests/test_smoke.py`

**Interfaces:**
- Consumes: nothing
- Produces: `comicload.__version__` (str); a working `pytest` and `ruff` setup for all later tasks

- [ ] **Step 1: Write the failing test**

`tests/test_smoke.py`:
```python
def test_package_exposes_version():
    import comicload

    assert isinstance(comicload.__version__, str)
    assert comicload.__version__
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_smoke.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'comicload'`

- [ ] **Step 3: Write minimal implementation**

`pyproject.toml`:
```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "comicload"
version = "0.1.0"
description = "Photograph comic covers, identify them, catalogue them in League of Comic Geeks"
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
    "typer>=0.12",
    "rich>=13.7",
    "pydantic>=2.7",
    "platformdirs>=4.2",
    "keyring>=25.0",
    "pillow>=10.3",
    "pyzbar>=0.1.9",
]

[project.optional-dependencies]
locg = ["playwright>=1.44"]
dev = ["pytest>=8.2", "pytest-cov>=5.0", "ruff>=0.4", "mypy>=1.10"]

[project.scripts]
comicload = "comicload.adapters.cli.app:main"

[tool.hatch.build.targets.wheel]
packages = ["src/comicload"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.mypy]
python_version = "3.12"
strict = true
files = ["src/comicload"]
```

`src/comicload/__init__.py`:
```python
"""comicload — photograph comic covers, identify them, catalogue them."""

__version__ = "0.1.0"
```

`tests/conftest.py`:
```python
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_smoke.py -v`
Expected: PASS — 1 passed

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/comicload/__init__.py tests/conftest.py tests/test_smoke.py
git commit -m "chore: scaffold package with pytest, ruff, mypy config"
```

---

### Task 2: Core domain models

**Files:**
- Create: `src/comicload/core/__init__.py`, `src/comicload/core/models.py`, `src/comicload/core/errors.py`
- Test: `tests/core/test_models.py`

**Interfaces:**
- Consumes: nothing
- Produces: `Photo`, `Scope`, `Candidate`, `Issue`, `CatalogEntry`, `Bucket`, `IdentifyResult`, `ImportResult`, `ComicloadError`. Every later task uses these exact names and fields.

- [ ] **Step 1: Write the failing test**

`tests/core/test_models.py`:
```python
from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from comicload.core.models import (
    Bucket,
    Candidate,
    CatalogEntry,
    ImportResult,
    Issue,
    Photo,
    Scope,
)


def test_photo_is_frozen():
    photo = Photo(id="abc123", data=b"\xff\xd8", filename="a.jpg")
    with pytest.raises(FrozenInstanceError):
        photo.id = "other"  # type: ignore[misc]


def test_scope_defaults_to_unbounded():
    scope = Scope()
    assert scope.publisher is None
    assert scope.year_from is None
    assert scope.year_to is None


def test_scope_matches_year_within_range():
    scope = Scope(year_from=1970, year_to=1985)
    assert scope.includes_year(1974)
    assert not scope.includes_year(1990)
    assert scope.includes_year(None)


def test_candidate_carries_originating_signal():
    candidate = Candidate(signal="barcode", confidence=0.9, barcode="759606084570111")
    assert candidate.signal == "barcode"
    assert candidate.series is None


def test_catalog_entry_full_title_includes_printing():
    entry = CatalogEntry(
        publisher_name="Image Comics",
        series_name="Alex + Ada",
        full_title="Alex + Ada #2 2nd Printing",
        release_date=date(2013, 12, 11),
    )
    assert entry.in_collection is True
    assert entry.in_wish_list is False
    assert entry.notes == ""


def test_issue_to_catalog_entry_builds_full_title():
    issue = Issue(
        gcd_id=1,
        publisher="Image Comics",
        series="Alex + Ada",
        issue_number="2",
        on_sale_date=date(2013, 12, 11),
        printing="2nd Printing",
    )
    entry = issue.to_catalog_entry()
    assert entry.full_title == "Alex + Ada #2 2nd Printing"
    assert entry.release_date == date(2013, 12, 11)


def test_issue_to_catalog_entry_omits_printing_when_absent():
    issue = Issue(gcd_id=2, publisher="Marvel", series="The Punisher", issue_number="12")
    assert issue.to_catalog_entry().full_title == "The Punisher #12"


def test_import_result_reports_counts():
    result = ImportResult(total=10, matched=8, unmatched=2, destination="out.csv")
    assert result.view_url is None
    assert result.matched == 8


def test_bucket_values():
    assert Bucket.CONFIDENT.value == "confident"
    assert Bucket.AMBIGUOUS.value == "ambiguous"
    assert Bucket.UNRECOGNIZED.value == "unrecognized"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/core/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'comicload.core'`

- [ ] **Step 3: Write minimal implementation**

`src/comicload/core/__init__.py`:
```python
"""Pure domain layer. Imports stdlib only."""
```

`src/comicload/core/errors.py`:
```python
class ComicloadError(Exception):
    """Base class for every error this package raises deliberately."""


class ConfigError(ComicloadError):
    """Configuration is missing or invalid."""


class CatalogError(ComicloadError):
    """The local metadata catalogue is missing or unusable."""


class SinkError(ComicloadError):
    """An export destination rejected or could not accept the data."""
```

`src/comicload/core/models.py`:
```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class Photo:
    """One source image. `data` is the raw bytes so the domain never touches a filesystem."""

    id: str
    data: bytes
    filename: str


@dataclass(frozen=True, slots=True)
class Scope:
    """User-declared narrowing hint for a batch."""

    publisher: str | None = None
    year_from: int | None = None
    year_to: int | None = None

    def includes_year(self, year: int | None) -> bool:
        if year is None:
            return True
        if self.year_from is not None and year < self.year_from:
            return False
        if self.year_to is not None and year > self.year_to:
            return False
        return True


@dataclass(frozen=True, slots=True)
class Candidate:
    """A guess produced by one signal. Fields are optional because signals see different things."""

    signal: str
    confidence: float
    publisher: str | None = None
    series: str | None = None
    issue_number: str | None = None
    printing: str | None = None
    year: int | None = None
    barcode: str | None = None
    evidence: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Issue:
    """A resolved issue from the metadata catalogue."""

    gcd_id: int
    publisher: str
    series: str
    issue_number: str
    on_sale_date: date | None = None
    printing: str | None = None

    def to_catalog_entry(self) -> CatalogEntry:
        title = f"{self.series} #{self.issue_number}"
        if self.printing:
            title = f"{title} {self.printing}"
        return CatalogEntry(
            publisher_name=self.publisher,
            series_name=self.series,
            full_title=title,
            release_date=self.on_sale_date,
        )


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """One row of the League of Comic Geeks import format. Field order matches their export."""

    publisher_name: str
    series_name: str
    full_title: str
    release_date: date | None = None
    in_collection: bool = True
    in_wish_list: bool = False
    marked_read: bool = False
    my_rating: str = ""
    media_format: str = ""
    price_paid: str = ""
    date_purchased: str = ""
    condition: str = ""
    notes: str = ""
    tags: str = ""


class Bucket(StrEnum):
    CONFIDENT = "confident"
    AMBIGUOUS = "ambiguous"
    UNRECOGNIZED = "unrecognized"


@dataclass(frozen=True, slots=True)
class IdentifyResult:
    """Outcome for a single photo. Every photo produces exactly one of these."""

    photo_id: str
    filename: str
    bucket: Bucket
    entry: CatalogEntry | None = None
    candidates: tuple[Candidate, ...] = ()


@dataclass(frozen=True, slots=True)
class ImportResult:
    """Outcome of pushing entries to a sink. `view_url` is set by sinks that have one."""

    total: int
    matched: int
    unmatched: int
    destination: str
    view_url: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/core/test_models.py -v`
Expected: PASS — 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/comicload/core tests/core/test_models.py
git commit -m "feat(core): add domain models for photos, candidates, issues, entries"
```

---

### Task 3: Ports, registry, and architecture enforcement

**Files:**
- Create: `src/comicload/core/ports.py`, `src/comicload/core/registry.py`
- Test: `tests/core/test_registry.py`, `tests/test_architecture.py`

**Interfaces:**
- Consumes: `comicload.core.models` (Task 2)
- Produces:
  - Protocols `PhotoSource`, `Signal`, `IssueResolver`, `Sink`, `Repository`, `ProgressReporter`, `SecretStore`
  - `register_signal(name)`, `register_sink(name)` decorators; `get_signal(name)`, `get_sink(name)`, `available_signals()`, `available_sinks()`
  - `NullProgressReporter` — used by every service test

The architecture test is what makes principle 6 real rather than aspirational. It fails the build if a layer reaches the wrong way.

- [ ] **Step 1: Write the failing test**

`tests/core/test_registry.py`:
```python
import pytest

from comicload.core.models import Candidate, Photo, Scope
from comicload.core.registry import (
    available_signals,
    get_signal,
    register_signal,
    signal_registry,
)


@pytest.fixture(autouse=True)
def clean_registry():
    signal_registry.clear()
    yield
    signal_registry.clear()


def test_register_and_retrieve_signal():
    @register_signal("fake")
    class FakeSignal:
        name = "fake"

        def identify(self, photo: Photo, scope: Scope) -> list[Candidate]:
            return [Candidate(signal="fake", confidence=1.0)]

    assert "fake" in available_signals()
    instance = get_signal("fake")
    assert instance.identify(Photo(id="1", data=b"", filename="a.jpg"), Scope())[0].signal == "fake"


def test_unknown_signal_raises_with_helpful_message():
    with pytest.raises(KeyError) as exc:
        get_signal("nope")
    assert "nope" in str(exc.value)


def test_duplicate_registration_is_rejected():
    @register_signal("dupe")
    class One:
        name = "dupe"

        def identify(self, photo: Photo, scope: Scope) -> list[Candidate]:
            return []

    with pytest.raises(ValueError, match="already registered"):

        @register_signal("dupe")
        class Two:
            name = "dupe"

            def identify(self, photo: Photo, scope: Scope) -> list[Candidate]:
                return []
```

`tests/test_architecture.py`:
```python
"""Enforces the hexagonal boundaries. These tests are the reason a web adapter stays cheap."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "comicload"

ALLOWED_IMPORTS = {
    "core": set(),
    "services": {"comicload.core"},
    "infra": {"comicload.core"},
}


def _module_files(layer: str) -> list[Path]:
    return sorted((SRC / layer).rglob("*.py"))


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_layers_only_import_inward():
    for layer, allowed in ALLOWED_IMPORTS.items():
        for path in _module_files(layer):
            for imported in _imported_modules(path):
                if not imported.startswith("comicload."):
                    continue
                if imported.startswith(f"comicload.{layer}"):
                    continue
                assert any(imported.startswith(prefix) for prefix in allowed), (
                    f"{path.relative_to(SRC)} imports {imported}, "
                    f"which layer '{layer}' may not depend on"
                )


def test_no_console_output_outside_adapters():
    banned = ("rich", "typer")
    for layer in ("core", "services", "infra"):
        for path in _module_files(layer):
            imports = _imported_modules(path)
            for name in banned:
                assert not any(i == name or i.startswith(f"{name}.") for i in imports), (
                    f"{path.relative_to(SRC)} imports {name}; "
                    "presentation belongs in adapters/"
                )
            source = path.read_text()
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    assert node.func.id != "print", (
                        f"{path.relative_to(SRC)} calls print(); "
                        "use the ProgressReporter port"
                    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/core/test_registry.py tests/test_architecture.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'comicload.core.registry'`

- [ ] **Step 3: Write minimal implementation**

`src/comicload/core/ports.py`:
```python
from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Protocol, runtime_checkable

from comicload.core.models import (
    Candidate,
    CatalogEntry,
    IdentifyResult,
    ImportResult,
    Issue,
    Photo,
    Scope,
)


@runtime_checkable
class PhotoSource(Protocol):
    """Where photos come from. A local folder now; an upload stream later."""

    def photos(self) -> Iterator[Photo]: ...

    def count(self) -> int: ...


@runtime_checkable
class Signal(Protocol):
    """A recognizer. Returns zero or more guesses, never raises for an unreadable photo."""

    name: str

    def identify(self, photo: Photo, scope: Scope) -> list[Candidate]: ...


@runtime_checkable
class IssueResolver(Protocol):
    """Turns a guess into concrete catalogue issues, best match first."""

    def resolve(self, candidate: Candidate, scope: Scope) -> list[Issue]: ...


@runtime_checkable
class Sink(Protocol):
    """An export destination."""

    name: str

    def push(self, entries: Sequence[CatalogEntry]) -> ImportResult: ...


@runtime_checkable
class Repository(Protocol):
    """Persistence for identification outcomes."""

    def save(self, results: Sequence[IdentifyResult]) -> None: ...

    def pending_review(self) -> list[IdentifyResult]: ...

    def confirmed_entries(self) -> list[CatalogEntry]: ...


@runtime_checkable
class ProgressReporter(Protocol):
    """How long-running work reports itself. Rich in the CLI; job state on the web."""

    def start(self, total: int, label: str) -> None: ...

    def advance(self, amount: int = 1, message: str | None = None) -> None: ...

    def finish(self) -> None: ...


@runtime_checkable
class SecretStore(Protocol):
    """Key/value secrets. Backed by the OS keychain, never by config.toml."""

    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


class NullProgressReporter:
    """No-op reporter. The default for services and the one every test uses."""

    def start(self, total: int, label: str) -> None:
        return None

    def advance(self, amount: int = 1, message: str | None = None) -> None:
        return None

    def finish(self) -> None:
        return None
```

`src/comicload/core/registry.py`:
```python
from __future__ import annotations

from typing import TypeVar

from comicload.core.ports import Signal, Sink

signal_registry: dict[str, type] = {}
sink_registry: dict[str, type] = {}

T = TypeVar("T")


def _register(registry: dict[str, type], name: str, kind: str):
    def decorator(cls: type[T]) -> type[T]:
        if name in registry:
            raise ValueError(f"{kind} '{name}' is already registered")
        registry[name] = cls
        return cls

    return decorator


def register_signal(name: str):
    """Register a Signal implementation. Adding one requires no edits to existing files."""
    return _register(signal_registry, name, "signal")


def register_sink(name: str):
    """Register a Sink implementation."""
    return _register(sink_registry, name, "sink")


def _get(registry: dict[str, type], name: str, kind: str, **kwargs: object):
    if name not in registry:
        known = ", ".join(sorted(registry)) or "none"
        raise KeyError(f"unknown {kind} '{name}'; registered: {known}")
    return registry[name](**kwargs)


def get_signal(name: str, **kwargs: object) -> Signal:
    return _get(signal_registry, name, "signal", **kwargs)


def get_sink(name: str, **kwargs: object) -> Sink:
    return _get(sink_registry, name, "sink", **kwargs)


def available_signals() -> list[str]:
    return sorted(signal_registry)


def available_sinks() -> list[str]:
    return sorted(sink_registry)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/core/test_registry.py tests/test_architecture.py -v`
Expected: PASS — 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/comicload/core/ports.py src/comicload/core/registry.py tests/core/test_registry.py tests/test_architecture.py
git commit -m "feat(core): add ports, registry, and layer-boundary enforcement tests"
```

---

### Task 4: Config and secret store

**Files:**
- Create: `src/comicload/infra/__init__.py`, `src/comicload/infra/config.py`, `src/comicload/infra/secrets.py`
- Test: `tests/infra/test_config.py`

**Interfaces:**
- Consumes: `comicload.core.errors.ConfigError`
- Produces:
  - `Config` (pydantic), with nested `ExportConfig`, `LocgConfig`, `CatalogConfig`, `ScanConfig`, `SignalsConfig`, `LlmConfig`
  - `load_config(path: Path | None = None) -> Config`
  - `save_config(config: Config, path: Path | None = None) -> Path`
  - `default_config_path() -> Path`
  - `KeyringSecretStore` implementing `SecretStore`

- [ ] **Step 1: Write the failing test**

`tests/infra/test_config.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/infra/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'comicload.infra'`

- [ ] **Step 3: Write minimal implementation**

`src/comicload/infra/__init__.py`:
```python
"""Concrete implementations of core ports."""
```

`src/comicload/infra/config.py`:
```python
from __future__ import annotations

import tomllib
from pathlib import Path

from platformdirs import user_config_path, user_data_path
from pydantic import BaseModel, Field


class ExportConfig(BaseModel):
    sink: str = "csv"


class LocgConfig(BaseModel):
    state_file: str = ""
    confirm_before_import: bool = True


class CatalogConfig(BaseModel):
    gcd_db: str = ""


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
    with target.open("rb") as handle:
        return Config.model_validate(tomllib.load(handle))


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
```

`src/comicload/infra/secrets.py`:
```python
from __future__ import annotations

SERVICE = "comicload"


class KeyringSecretStore:
    """SecretStore backed by the OS keychain.

    `backend` accepts a dict for tests so no real keychain is touched.
    """

    def __init__(self, backend: dict[str, str] | None = None) -> None:
        self._backend = backend

    def get(self, name: str) -> str | None:
        if self._backend is not None:
            return self._backend.get(name)
        import keyring

        return keyring.get_password(SERVICE, name)

    def set(self, name: str, value: str) -> None:
        if self._backend is not None:
            self._backend[name] = value
            return
        import keyring

        keyring.set_password(SERVICE, name, value)

    def delete(self, name: str) -> None:
        if self._backend is not None:
            self._backend.pop(name, None)
            return
        import keyring

        try:
            keyring.delete_password(SERVICE, name)
        except keyring.errors.PasswordDeleteError:
            return
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/infra/test_config.py -v`
Expected: PASS — 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/comicload/infra/__init__.py src/comicload/infra/config.py src/comicload/infra/secrets.py tests/infra/test_config.py
git commit -m "feat(infra): add TOML config and keychain-backed secret store"
```

---

### Task 5: Local folder photo source

**Files:**
- Create: `src/comicload/infra/photos.py`
- Test: `tests/infra/test_photos.py`

**Interfaces:**
- Consumes: `Photo` (Task 2), `PhotoSource` (Task 3)
- Produces: `LocalFolderPhotoSource(root: Path)` with `.photos()` and `.count()`; `SUPPORTED_SUFFIXES`

- [ ] **Step 1: Write the failing test**

`tests/infra/test_photos.py`:
```python
import pytest

from comicload.infra.photos import LocalFolderPhotoSource


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"first")
    (tmp_path / "b.JPEG").write_bytes(b"second")
    (tmp_path / "notes.txt").write_bytes(b"ignore me")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "c.png").write_bytes(b"third")
    return tmp_path


def test_finds_images_recursively_and_skips_other_files(folder):
    source = LocalFolderPhotoSource(folder)
    names = sorted(photo.filename for photo in source.photos())
    assert names == ["a.jpg", "b.JPEG", "c.png"]


def test_count_matches_photos(folder):
    source = LocalFolderPhotoSource(folder)
    assert source.count() == len(list(source.photos()))


def test_id_is_content_hash_so_duplicates_collide(tmp_path):
    (tmp_path / "one.jpg").write_bytes(b"same bytes")
    (tmp_path / "two.jpg").write_bytes(b"same bytes")
    ids = {photo.id for photo in LocalFolderPhotoSource(tmp_path).photos()}
    assert len(ids) == 1


def test_missing_folder_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        LocalFolderPhotoSource(tmp_path / "nope").count()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/infra/test_photos.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'comicload.infra.photos'`

- [ ] **Step 3: Write minimal implementation**

`src/comicload/infra/photos.py`:
```python
from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

from comicload.core.models import Photo

SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".tif", ".tiff"}


class LocalFolderPhotoSource:
    """Reads photos from a folder tree. Photo ids are content hashes, so duplicates collapse."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def _paths(self) -> list[Path]:
        if not self._root.exists():
            raise FileNotFoundError(f"photo folder does not exist: {self._root}")
        return sorted(
            path
            for path in self._root.rglob("*")
            if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
        )

    def photos(self) -> Iterator[Photo]:
        for path in self._paths():
            data = path.read_bytes()
            yield Photo(id=hashlib.sha256(data).hexdigest(), data=data, filename=path.name)

    def count(self) -> int:
        return len(self._paths())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/infra/test_photos.py -v`
Expected: PASS — 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/comicload/infra/photos.py tests/infra/test_photos.py
git commit -m "feat(infra): add local folder photo source with content-hash ids"
```

---

### Task 6: Barcode signal

**Files:**
- Create: `src/comicload/infra/signals/__init__.py`, `src/comicload/infra/signals/barcode.py`
- Test: `tests/infra/test_barcode.py`

**Interfaces:**
- Consumes: `Photo`, `Scope`, `Candidate` (Task 2), `register_signal` (Task 3)
- Produces: `BarcodeSignal` registered as `"barcode"`; `decode_supplement(supplement: str) -> tuple[str | None, str | None]` returning `(issue_number, printing)`

**Design note:** The primary path returns the raw barcode string for the catalogue to match against GCD's barcode column. Supplement decoding is a *fallback* that only narrows — it is publisher-specific, empirically shaky, and always yields lower confidence.

`pyzbar` requires the native zbar library: `brew install zbar`. Note it in the README (Task 12).

- [ ] **Step 1: Write the failing test**

`tests/infra/test_barcode.py`:
```python
from comicload.core.models import Photo, Scope
from comicload.infra.signals.barcode import BarcodeSignal, decode_supplement


class StubDecoder:
    """Stands in for pyzbar so tests need no native library and no real images."""

    def __init__(self, results):
        self._results = results

    def __call__(self, image_bytes: bytes):
        return self._results


def test_decode_supplement_splits_issue_and_printing():
    assert decode_supplement("00111") == ("1", "1st Printing")


def test_decode_supplement_maps_second_printing():
    assert decode_supplement("01202") == ("12", "2nd Printing")


def test_decode_supplement_rejects_wrong_length():
    assert decode_supplement("123") == (None, None)


def test_decode_supplement_rejects_non_numeric():
    assert decode_supplement("abcde") == (None, None)


def test_signal_returns_candidate_carrying_full_barcode():
    signal = BarcodeSignal(decoder=StubDecoder([("759606084570", "00111")]))
    photo = Photo(id="1", data=b"fake", filename="a.jpg")

    candidates = signal.identify(photo, Scope())

    assert len(candidates) == 1
    assert candidates[0].barcode == "75960608457000111"
    assert candidates[0].issue_number == "1"
    assert candidates[0].printing == "1st Printing"
    assert candidates[0].signal == "barcode"
    assert candidates[0].confidence >= 0.9


def test_signal_handles_barcode_without_supplement():
    signal = BarcodeSignal(decoder=StubDecoder([("759606084570", None)]))
    candidates = signal.identify(Photo(id="1", data=b"x", filename="a.jpg"), Scope())

    assert candidates[0].barcode == "759606084570"
    assert candidates[0].issue_number is None
    assert candidates[0].confidence < 0.9


def test_signal_returns_empty_when_nothing_decodes():
    signal = BarcodeSignal(decoder=StubDecoder([]))
    assert signal.identify(Photo(id="1", data=b"x", filename="a.jpg"), Scope()) == []


def test_signal_never_raises_on_decoder_failure():
    def exploding(image_bytes: bytes):
        raise RuntimeError("corrupt image")

    signal = BarcodeSignal(decoder=exploding)
    assert signal.identify(Photo(id="1", data=b"x", filename="a.jpg"), Scope()) == []


def test_signal_is_registered():
    from comicload.core.registry import available_signals

    assert "barcode" in available_signals()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/infra/test_barcode.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'comicload.infra.signals'`

- [ ] **Step 3: Write minimal implementation**

`src/comicload/infra/signals/__init__.py`:
```python
"""Signal implementations. Importing this package registers them."""

from comicload.infra.signals import barcode  # noqa: F401
```

`src/comicload/infra/signals/barcode.py`:
```python
from __future__ import annotations

import io
from collections.abc import Callable, Sequence

from comicload.core.models import Candidate, Photo, Scope
from comicload.core.registry import register_signal

DecodedBarcode = tuple[str, str | None]
Decoder = Callable[[bytes], Sequence[DecodedBarcode]]

_PRINTING_LABELS = {
    "01": "1st Printing",
    "02": "2nd Printing",
    "03": "3rd Printing",
    "04": "4th Printing",
    "05": "5th Printing",
}


def decode_supplement(supplement: str) -> tuple[str | None, str | None]:
    """Split a 5-digit EAN-5 supplement into (issue number, printing label).

    The common Marvel/DC layout is IIIVV: three issue digits, two variant digits.
    This is empirical, not a standard, so callers must treat the result as a hint.
    """
    if len(supplement) != 5 or not supplement.isdigit():
        return (None, None)
    issue = str(int(supplement[:3]))
    printing = _PRINTING_LABELS.get(supplement[3:])
    return (issue, printing)


def _pyzbar_decoder(image_bytes: bytes) -> Sequence[DecodedBarcode]:
    from PIL import Image
    from pyzbar import pyzbar

    image = Image.open(io.BytesIO(image_bytes))
    found: list[DecodedBarcode] = []
    main: str | None = None
    supplement: str | None = None
    for result in pyzbar.decode(image):
        value = result.data.decode("ascii", errors="ignore")
        if len(value) == 5:
            supplement = value
        elif len(value) >= 8:
            main = value
    if main:
        found.append((main, supplement))
    return found


@register_signal("barcode")
class BarcodeSignal:
    """Decodes UPC/EAN from a cover photo.

    The full barcode string is the payload; the catalogue matches it directly.
    Supplement decoding only narrows when that direct match fails.
    """

    name = "barcode"

    def __init__(self, decoder: Decoder | None = None) -> None:
        self._decode = decoder or _pyzbar_decoder

    def identify(self, photo: Photo, scope: Scope) -> list[Candidate]:
        try:
            decoded = self._decode(photo.data)
        except Exception:
            return []

        candidates: list[Candidate] = []
        for main, supplement in decoded:
            issue, printing = decode_supplement(supplement) if supplement else (None, None)
            candidates.append(
                Candidate(
                    signal=self.name,
                    confidence=0.95 if supplement else 0.75,
                    barcode=f"{main}{supplement}" if supplement else main,
                    issue_number=issue,
                    printing=printing,
                    evidence={"upc": main, "supplement": supplement or ""},
                )
            )
        return candidates
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/infra/test_barcode.py -v`
Expected: PASS — 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/comicload/infra/signals tests/infra/test_barcode.py
git commit -m "feat(signals): add barcode signal with UPC supplement fallback"
```

---

### Task 7: GCD dump loader

**Files:**
- Create: `src/comicload/infra/storage/__init__.py`, `src/comicload/infra/storage/gcd_loader.py`
- Test: `tests/infra/test_gcd_loader.py`, `tests/fixtures/gcd_sample.sql`

**Interfaces:**
- Consumes: `CatalogError` (Task 2)
- Produces: `load_dump(sql_path: Path, db_path: Path, on_progress: Callable[[int], None] | None = None) -> dict[str, int]` returning row counts per table; `SCHEMA` (the SQLite DDL)

**Risk note — read before implementing.** This is the highest-uncertainty task in the plan. GCD publishes a MySQL dump; exact table and column names must be confirmed against the real download before finalising the mapping. **Step 0: run `head -c 200000 <dump.sql> | grep -i "CREATE TABLE"` and confirm the columns for `gcd_publisher`, `gcd_series`, `gcd_issue`.** If names differ from those below, update the extraction mapping and the fixture together — the tests are written against the fixture, so they stay honest either way.

- [ ] **Step 1: Write the failing test**

`tests/fixtures/gcd_sample.sql`:
```sql
INSERT INTO `gcd_publisher` (`id`, `name`) VALUES (1,'Marvel'),(2,'Image Comics');
INSERT INTO `gcd_series` (`id`, `name`, `publisher_id`, `year_began`) VALUES (10,'The Punisher',1,2000),(11,'Alex + Ada',2,2013);
INSERT INTO `gcd_issue` (`id`, `number`, `series_id`, `on_sale_date`, `barcode`) VALUES (100,'12',10,'2001-03-01','75960608457000111'),(101,'2',11,'2013-12-11','70985301491000211');
```

`tests/infra/test_gcd_loader.py`:
```python
import sqlite3
from pathlib import Path

import pytest

from comicload.core.errors import CatalogError
from comicload.infra.storage.gcd_loader import load_dump

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "gcd_sample.sql"


def test_load_reports_row_counts(tmp_path):
    counts = load_dump(FIXTURE, tmp_path / "gcd.sqlite")
    assert counts == {"publisher": 2, "series": 2, "issue": 2}


def test_loaded_issue_joins_series_and_publisher(tmp_path):
    db = tmp_path / "gcd.sqlite"
    load_dump(FIXTURE, db)

    conn = sqlite3.connect(db)
    row = conn.execute(
        """
        SELECT p.name, s.name, i.number, i.on_sale_date, i.barcode
        FROM issue i JOIN series s ON s.id = i.series_id
                     JOIN publisher p ON p.id = s.publisher_id
        WHERE i.barcode = '75960608457000111'
        """
    ).fetchone()
    assert row == ("Marvel", "The Punisher", "12", "2001-03-01", "75960608457000111")


def test_barcode_index_exists(tmp_path):
    db = tmp_path / "gcd.sqlite"
    load_dump(FIXTURE, db)
    conn = sqlite3.connect(db)
    indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_issue_barcode" in indexes


def test_reload_is_idempotent(tmp_path):
    db = tmp_path / "gcd.sqlite"
    load_dump(FIXTURE, db)
    counts = load_dump(FIXTURE, db)
    assert counts == {"publisher": 2, "series": 2, "issue": 2}


def test_progress_callback_is_invoked(tmp_path):
    seen: list[int] = []
    load_dump(FIXTURE, tmp_path / "gcd.sqlite", on_progress=seen.append)
    assert seen


def test_missing_dump_raises_catalog_error(tmp_path):
    with pytest.raises(CatalogError, match="not found"):
        load_dump(tmp_path / "nope.sql", tmp_path / "gcd.sqlite")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/infra/test_gcd_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'comicload.infra.storage'`

- [ ] **Step 3: Write minimal implementation**

`src/comicload/infra/storage/__init__.py`:
```python
"""Local metadata catalogue storage."""
```

`src/comicload/infra/storage/gcd_loader.py`:
```python
from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

from comicload.core.errors import CatalogError

SCHEMA = """
DROP TABLE IF EXISTS issue;
DROP TABLE IF EXISTS series;
DROP TABLE IF EXISTS publisher;

CREATE TABLE publisher (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE series (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    publisher_id INTEGER,
    year_began INTEGER
);
CREATE TABLE issue (
    id INTEGER PRIMARY KEY,
    number TEXT,
    series_id INTEGER,
    on_sale_date TEXT,
    barcode TEXT
);
CREATE INDEX idx_issue_barcode ON issue(barcode);
CREATE INDEX idx_series_name ON series(name);
CREATE INDEX idx_publisher_name ON publisher(name);
"""

# Which dump table feeds which local table, and how many leading columns we keep.
TABLE_MAP = {
    "gcd_publisher": ("publisher", 2),
    "gcd_series": ("series", 4),
    "gcd_issue": ("issue", 5),
}

_INSERT_RE = re.compile(r"INSERT INTO [`\"]?(\w+)[`\"]? .*?VALUES\s*(.+);", re.IGNORECASE | re.DOTALL)


def _split_tuples(blob: str) -> Iterator[list[str | None]]:
    """Yield each (...) group from a MySQL VALUES clause, respecting quotes and escapes."""
    values: list[str | None] = []
    current = ""
    in_string = False
    escaped = False
    depth = 0

    for char in blob:
        if escaped:
            current += char
            escaped = False
            continue
        if char == "\\" and in_string:
            escaped = True
            continue
        if char == "'":
            in_string = not in_string
            continue
        if in_string:
            current += char
            continue
        if char == "(":
            depth += 1
            if depth == 1:
                values, current = [], ""
            continue
        if char == ")":
            depth -= 1
            if depth == 0:
                values.append(None if current.strip().upper() == "NULL" else current.strip())
                yield values
            continue
        if char == "," and depth == 1:
            values.append(None if current.strip().upper() == "NULL" else current.strip())
            current = ""
            continue
        if depth == 1:
            current += char


def load_dump(
    sql_path: Path,
    db_path: Path,
    on_progress: Callable[[int], None] | None = None,
) -> dict[str, int]:
    """Load the tables comicload needs from a GCD MySQL dump into a local SQLite file."""
    sql_path, db_path = Path(sql_path), Path(db_path)
    if not sql_path.exists():
        raise CatalogError(f"GCD dump not found: {sql_path}")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        counts = {local: 0 for local, _ in TABLE_MAP.values()}

        for match in _INSERT_RE.finditer(sql_path.read_text(errors="ignore")):
            source_table = match.group(1)
            if source_table not in TABLE_MAP:
                continue
            local_table, width = TABLE_MAP[source_table]
            rows = [tuple(row[:width]) for row in _split_tuples(match.group(2))]
            if not rows:
                continue
            placeholders = ", ".join("?" * width)
            conn.executemany(
                f"INSERT OR REPLACE INTO {local_table} VALUES ({placeholders})", rows
            )
            counts[local_table] += len(rows)
            if on_progress:
                on_progress(len(rows))

        conn.commit()
        return counts
    finally:
        conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/infra/test_gcd_loader.py -v`
Expected: PASS — 6 passed

- [ ] **Step 5: Commit**

```bash
git add -f tests/fixtures/gcd_sample.sql
git add src/comicload/infra/storage tests/infra/test_gcd_loader.py
git commit -m "feat(storage): load GCD MySQL dump into local SQLite mirror"
```

---

### Task 8: SQLite issue resolver

**Files:**
- Create: `src/comicload/infra/storage/gcd_repo.py`
- Test: `tests/services/test_catalog.py`

**Interfaces:**
- Consumes: `Candidate`, `Issue`, `Scope` (Task 2); `IssueResolver` (Task 3); `load_dump` (Task 7)
- Produces: `SqliteIssueResolver(db_path: Path)` implementing `IssueResolver.resolve`

- [ ] **Step 1: Write the failing test**

`tests/services/test_catalog.py`:
```python
from datetime import date
from pathlib import Path

import pytest

from comicload.core.models import Candidate, Scope
from comicload.infra.storage.gcd_loader import load_dump
from comicload.infra.storage.gcd_repo import SqliteIssueResolver

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "gcd_sample.sql"


@pytest.fixture
def resolver(tmp_path):
    db = tmp_path / "gcd.sqlite"
    load_dump(FIXTURE, db)
    return SqliteIssueResolver(db)


def test_exact_barcode_match_wins(resolver):
    candidate = Candidate(signal="barcode", confidence=0.95, barcode="75960608457000111")
    issues = resolver.resolve(candidate, Scope())

    assert len(issues) == 1
    assert issues[0].series == "The Punisher"
    assert issues[0].publisher == "Marvel"
    assert issues[0].issue_number == "12"
    assert issues[0].on_sale_date == date(2001, 3, 1)


def test_unknown_barcode_returns_nothing(resolver):
    candidate = Candidate(signal="barcode", confidence=0.95, barcode="00000000000000000")
    assert resolver.resolve(candidate, Scope()) == []


def test_series_and_issue_match_when_no_barcode(resolver):
    candidate = Candidate(
        signal="ocr", confidence=0.6, series="Alex + Ada", issue_number="2"
    )
    issues = resolver.resolve(candidate, Scope())
    assert [i.publisher for i in issues] == ["Image Comics"]


def test_scope_publisher_filters_results(resolver):
    candidate = Candidate(signal="ocr", confidence=0.6, issue_number="12")
    assert resolver.resolve(candidate, Scope(publisher="Image Comics")) == []
    assert resolver.resolve(candidate, Scope(publisher="Marvel"))


def test_candidate_with_no_usable_fields_returns_nothing(resolver):
    assert resolver.resolve(Candidate(signal="none", confidence=0.1), Scope()) == []


def test_missing_database_raises(tmp_path):
    from comicload.core.errors import CatalogError

    resolver = SqliteIssueResolver(tmp_path / "absent.sqlite")
    with pytest.raises(CatalogError, match="catalog sync"):
        resolver.resolve(Candidate(signal="barcode", confidence=1.0, barcode="1"), Scope())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/services/test_catalog.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'comicload.infra.storage.gcd_repo'`

- [ ] **Step 3: Write minimal implementation**

`src/comicload/infra/storage/gcd_repo.py`:
```python
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from comicload.core.errors import CatalogError
from comicload.core.models import Candidate, Issue, Scope

_BASE_QUERY = """
SELECT i.id, p.name, s.name, i.number, i.on_sale_date
FROM issue i
JOIN series s ON s.id = i.series_id
JOIN publisher p ON p.id = s.publisher_id
"""


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


class SqliteIssueResolver:
    """Resolves candidates against the local GCD mirror.

    Barcode match is exact and preferred. Series/issue match is the fallback.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        if not self._db_path.exists():
            raise CatalogError(
                f"no metadata catalogue at {self._db_path}; run 'comicload catalog sync' first"
            )
        return sqlite3.connect(self._db_path)

    def resolve(self, candidate: Candidate, scope: Scope) -> list[Issue]:
        clauses: list[str] = []
        params: list[str] = []

        if candidate.barcode:
            clauses.append("i.barcode = ?")
            params.append(candidate.barcode)
        else:
            if candidate.series:
                clauses.append("s.name = ? COLLATE NOCASE")
                params.append(candidate.series)
            if candidate.issue_number:
                clauses.append("i.number = ?")
                params.append(candidate.issue_number)

        if not clauses:
            return []

        if scope.publisher:
            clauses.append("p.name = ? COLLATE NOCASE")
            params.append(scope.publisher)

        query = f"{_BASE_QUERY} WHERE {' AND '.join(clauses)} ORDER BY i.id LIMIT 25"

        conn = self._connect()
        try:
            rows = conn.execute(query, params).fetchall()
        finally:
            conn.close()

        issues = [
            Issue(
                gcd_id=row[0],
                publisher=row[1],
                series=row[2],
                issue_number=row[3],
                on_sale_date=_parse_date(row[4]),
                printing=candidate.printing,
            )
            for row in rows
        ]
        return [i for i in issues if scope.includes_year(i.on_sale_date.year if i.on_sale_date else None)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/services/test_catalog.py -v`
Expected: PASS — 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/comicload/infra/storage/gcd_repo.py tests/services/test_catalog.py
git commit -m "feat(storage): resolve candidates against local GCD mirror"
```

---

### Task 9: IdentifyService

**Files:**
- Create: `src/comicload/services/__init__.py`, `src/comicload/services/identify.py`
- Test: `tests/services/test_identify.py`

**Interfaces:**
- Consumes: all core models and ports; `NullProgressReporter` (Task 3)
- Produces: `IdentifyService(signals, resolver, progress=None, confident_threshold=0.85)` with `.run(source: PhotoSource, scope: Scope) -> list[IdentifyResult]`

- [ ] **Step 1: Write the failing test**

`tests/services/test_identify.py`:
```python
from comicload.core.models import Bucket, Candidate, Issue, Photo, Scope
from comicload.services.identify import IdentifyService


class StubSource:
    def __init__(self, photos):
        self._photos = photos

    def photos(self):
        return iter(self._photos)

    def count(self):
        return len(self._photos)


class StubSignal:
    def __init__(self, name, candidates):
        self.name = name
        self._candidates = candidates

    def identify(self, photo, scope):
        return list(self._candidates)


class StubResolver:
    def __init__(self, mapping):
        self._mapping = mapping

    def resolve(self, candidate, scope):
        return list(self._mapping.get(candidate.barcode, []))


class RecordingProgress:
    def __init__(self):
        self.started = None
        self.advanced = 0
        self.finished = False

    def start(self, total, label):
        self.started = (total, label)

    def advance(self, amount=1, message=None):
        self.advanced += amount

    def finish(self):
        self.finished = True


PHOTO = Photo(id="p1", data=b"x", filename="one.jpg")
ISSUE = Issue(gcd_id=1, publisher="Marvel", series="The Punisher", issue_number="12")


def test_single_high_confidence_match_is_confident():
    candidate = Candidate(signal="barcode", confidence=0.95, barcode="B1")
    service = IdentifyService(
        signals=[StubSignal("barcode", [candidate])],
        resolver=StubResolver({"B1": [ISSUE]}),
    )

    results = service.run(StubSource([PHOTO]), Scope())

    assert results[0].bucket is Bucket.CONFIDENT
    assert results[0].entry is not None
    assert results[0].entry.full_title == "The Punisher #12"


def test_multiple_matches_are_ambiguous():
    other = Issue(gcd_id=2, publisher="Marvel", series="The Punisher", issue_number="12")
    candidate = Candidate(signal="barcode", confidence=0.95, barcode="B1")
    service = IdentifyService(
        signals=[StubSignal("barcode", [candidate])],
        resolver=StubResolver({"B1": [ISSUE, other]}),
    )

    result = service.run(StubSource([PHOTO]), Scope())[0]
    assert result.bucket is Bucket.AMBIGUOUS
    assert result.entry is None


def test_low_confidence_match_is_ambiguous():
    candidate = Candidate(signal="barcode", confidence=0.4, barcode="B1")
    service = IdentifyService(
        signals=[StubSignal("barcode", [candidate])],
        resolver=StubResolver({"B1": [ISSUE]}),
    )
    assert service.run(StubSource([PHOTO]), Scope())[0].bucket is Bucket.AMBIGUOUS


def test_no_candidates_is_unrecognized():
    service = IdentifyService(
        signals=[StubSignal("barcode", [])], resolver=StubResolver({})
    )
    result = service.run(StubSource([PHOTO]), Scope())[0]
    assert result.bucket is Bucket.UNRECOGNIZED
    assert result.candidates == ()


def test_candidates_that_resolve_to_nothing_are_unrecognized():
    candidate = Candidate(signal="barcode", confidence=0.95, barcode="unknown")
    service = IdentifyService(
        signals=[StubSignal("barcode", [candidate])], resolver=StubResolver({})
    )
    assert service.run(StubSource([PHOTO]), Scope())[0].bucket is Bucket.UNRECOGNIZED


def test_failing_signal_does_not_stop_the_others():
    class Exploding:
        name = "boom"

        def identify(self, photo, scope):
            raise RuntimeError("signal crashed")

    good = Candidate(signal="barcode", confidence=0.95, barcode="B1")
    service = IdentifyService(
        signals=[Exploding(), StubSignal("barcode", [good])],
        resolver=StubResolver({"B1": [ISSUE]}),
    )
    assert service.run(StubSource([PHOTO]), Scope())[0].bucket is Bucket.CONFIDENT


def test_progress_is_reported_through_the_port():
    progress = RecordingProgress()
    service = IdentifyService(
        signals=[StubSignal("barcode", [])],
        resolver=StubResolver({}),
        progress=progress,
    )
    service.run(StubSource([PHOTO, PHOTO]), Scope())

    assert progress.started == (2, "Identifying")
    assert progress.advanced == 2
    assert progress.finished is True


def test_entry_tags_record_provenance():
    candidate = Candidate(signal="barcode", confidence=0.95, barcode="B1")
    service = IdentifyService(
        signals=[StubSignal("barcode", [candidate])],
        resolver=StubResolver({"B1": [ISSUE]}),
    )
    entry = service.run(StubSource([PHOTO]), Scope())[0].entry
    assert "barcode" in entry.tags
    assert "one.jpg" in entry.tags
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/services/test_identify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'comicload.services'`

- [ ] **Step 3: Write minimal implementation**

`src/comicload/services/__init__.py`:
```python
"""Orchestration. Depends on core ports only — never on infra or adapters."""
```

`src/comicload/services/identify.py`:
```python
from __future__ import annotations

import dataclasses
from collections.abc import Sequence

from comicload.core.models import Bucket, Candidate, IdentifyResult, Issue, Photo, Scope
from comicload.core.ports import IssueResolver, PhotoSource, ProgressReporter, Signal
from comicload.core.ports import NullProgressReporter

CONFIDENT_THRESHOLD = 0.85


class IdentifyService:
    """Runs every signal over every photo, resolves guesses, and buckets the outcome.

    A photo is CONFIDENT only when a high-confidence candidate resolves to exactly
    one issue. Everything else goes to review. Nothing is silently guessed.
    """

    def __init__(
        self,
        signals: Sequence[Signal],
        resolver: IssueResolver,
        progress: ProgressReporter | None = None,
        confident_threshold: float = CONFIDENT_THRESHOLD,
    ) -> None:
        self._signals = list(signals)
        self._resolver = resolver
        self._progress = progress or NullProgressReporter()
        self._threshold = confident_threshold

    def _gather(self, photo: Photo, scope: Scope) -> list[Candidate]:
        gathered: list[Candidate] = []
        for signal in self._signals:
            try:
                gathered.extend(signal.identify(photo, scope))
            except Exception:
                continue
        return sorted(gathered, key=lambda c: c.confidence, reverse=True)

    def _tags(self, photo: Photo, candidate: Candidate) -> str:
        return f"comicload;photo={photo.filename};signal={candidate.signal};conf={candidate.confidence:.2f}"

    def _classify(
        self, photo: Photo, candidates: list[Candidate], scope: Scope
    ) -> IdentifyResult:
        if not candidates:
            return IdentifyResult(
                photo_id=photo.id, filename=photo.filename, bucket=Bucket.UNRECOGNIZED
            )

        resolved_any = False
        for candidate in candidates:
            issues: list[Issue] = self._resolver.resolve(candidate, scope)
            if not issues:
                continue
            resolved_any = True
            if len(issues) == 1 and candidate.confidence >= self._threshold:
                entry = issues[0].to_catalog_entry()
                entry = dataclasses.replace(
                    entry,
                    notes=candidate.printing or "",
                    tags=self._tags(photo, candidate),
                )
                return IdentifyResult(
                    photo_id=photo.id,
                    filename=photo.filename,
                    bucket=Bucket.CONFIDENT,
                    entry=entry,
                    candidates=tuple(candidates),
                )

        bucket = Bucket.AMBIGUOUS if resolved_any else Bucket.UNRECOGNIZED
        return IdentifyResult(
            photo_id=photo.id,
            filename=photo.filename,
            bucket=bucket,
            candidates=tuple(candidates),
        )

    def run(self, source: PhotoSource, scope: Scope) -> list[IdentifyResult]:
        self._progress.start(source.count(), "Identifying")
        results: list[IdentifyResult] = []
        try:
            for photo in source.photos():
                results.append(self._classify(photo, self._gather(photo, scope), scope))
                self._progress.advance(1, photo.filename)
        finally:
            self._progress.finish()
        return results
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/services/test_identify.py -v`
Expected: PASS — 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/comicload/services/__init__.py src/comicload/services/identify.py tests/services/test_identify.py
git commit -m "feat(services): add IdentifyService with confidence bucketing"
```

---

### Task 10: CSV sink and ExportService

**Files:**
- Create: `src/comicload/infra/sinks/__init__.py`, `src/comicload/infra/sinks/csv_sink.py`, `src/comicload/services/export.py`
- Test: `tests/infra/test_csv_sink.py`, `tests/services/test_export.py`, `tests/fixtures/locg_export_header.csv`

**Interfaces:**
- Consumes: `CatalogEntry`, `ImportResult` (Task 2); `register_sink` (Task 3)
- Produces:
  - `COLUMNS` — the 14 header strings in exact order
  - `CsvSink(path: Path)` registered as `"csv"`
  - `read_csv(path: Path) -> list[CatalogEntry]` and `validate_csv(path: Path) -> list[str]` (returns problems, empty when valid)
  - `ExportService(sink)` with `.export(results) -> ImportResult`

- [ ] **Step 1: Write the failing test**

`tests/fixtures/locg_export_header.csv`:
```csv
Publisher Name,Series Name,Full Title,Release Date,In Collection,In Wish List,Marked Read,My Rating,Media Format,Price Paid,Date Purchased,Condition,Notes,Tags
```

`tests/infra/test_csv_sink.py`:
```python
import csv
from datetime import date
from pathlib import Path

from comicload.core.models import CatalogEntry
from comicload.infra.sinks.csv_sink import COLUMNS, CsvSink, read_csv, validate_csv

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "locg_export_header.csv"

ENTRY = CatalogEntry(
    publisher_name="Image Comics",
    series_name="Alex + Ada",
    full_title="Alex + Ada #2 2nd Printing",
    release_date=date(2013, 12, 11),
)


def test_columns_match_the_real_locg_export_header():
    expected = FIXTURE.read_text().strip().split(",")
    assert COLUMNS == expected


def test_written_header_matches_fixture_exactly(tmp_path):
    out = tmp_path / "out.csv"
    CsvSink(out).push([ENTRY])
    assert out.read_text().splitlines()[0] == FIXTURE.read_text().strip()


def test_row_renders_booleans_as_zero_and_one(tmp_path):
    out = tmp_path / "out.csv"
    CsvSink(out).push([ENTRY])

    row = list(csv.DictReader(out.open()))[0]
    assert row["In Collection"] == "1"
    assert row["In Wish List"] == "0"
    assert row["Marked Read"] == "0"
    assert row["Release Date"] == "2013-12-11"


def test_missing_release_date_is_blank_not_none(tmp_path):
    out = tmp_path / "out.csv"
    CsvSink(out).push([CatalogEntry("Marvel", "The Punisher", "The Punisher #12")])
    assert list(csv.DictReader(out.open()))[0]["Release Date"] == ""


def test_push_returns_result_with_destination(tmp_path):
    out = tmp_path / "out.csv"
    result = CsvSink(out).push([ENTRY])
    assert result.total == 1
    assert result.matched == 1
    assert result.unmatched == 0
    assert result.destination == str(out)
    assert result.view_url is None


def test_roundtrip_read_csv(tmp_path):
    out = tmp_path / "out.csv"
    CsvSink(out).push([ENTRY])
    assert read_csv(out) == [ENTRY]


def test_validate_accepts_good_file(tmp_path):
    out = tmp_path / "out.csv"
    CsvSink(out).push([ENTRY])
    assert validate_csv(out) == []


def test_validate_reports_wrong_header(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("Wrong,Header\na,b\n")
    problems = validate_csv(bad)
    assert any("header" in p.lower() for p in problems)


def test_validate_reports_missing_required_field(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text(",".join(COLUMNS) + "\n" + ",".join([""] * len(COLUMNS)) + "\n")
    problems = validate_csv(bad)
    assert any("Full Title" in p for p in problems)


def test_sink_is_registered():
    from comicload.core.registry import available_sinks

    assert "csv" in available_sinks()
```

`tests/services/test_export.py`:
```python
from comicload.core.models import Bucket, CatalogEntry, IdentifyResult, ImportResult
from comicload.services.export import ExportService


class StubSink:
    name = "stub"

    def __init__(self):
        self.received = None

    def push(self, entries):
        self.received = list(entries)
        return ImportResult(
            total=len(entries),
            matched=len(entries),
            unmatched=0,
            destination="stub://",
            view_url="https://leagueofcomicgeeks.com/profile/me/collection",
        )


ENTRY = CatalogEntry("Marvel", "The Punisher", "The Punisher #12")


def test_only_confident_results_are_exported():
    sink = StubSink()
    results = [
        IdentifyResult("1", "a.jpg", Bucket.CONFIDENT, entry=ENTRY),
        IdentifyResult("2", "b.jpg", Bucket.AMBIGUOUS),
        IdentifyResult("3", "c.jpg", Bucket.UNRECOGNIZED),
    ]

    result = ExportService(sink).export(results)

    assert sink.received == [ENTRY]
    assert result.total == 1


def test_view_url_is_passed_through():
    result = ExportService(StubSink()).export(
        [IdentifyResult("1", "a.jpg", Bucket.CONFIDENT, entry=ENTRY)]
    )
    assert result.view_url == "https://leagueofcomicgeeks.com/profile/me/collection"


def test_exporting_nothing_still_returns_a_result():
    result = ExportService(StubSink()).export(
        [IdentifyResult("1", "a.jpg", Bucket.UNRECOGNIZED)]
    )
    assert result.total == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/infra/test_csv_sink.py tests/services/test_export.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'comicload.infra.sinks'`

- [ ] **Step 3: Write minimal implementation**

`src/comicload/infra/sinks/__init__.py`:
```python
"""Sink implementations. Importing this package registers them."""

from comicload.infra.sinks import csv_sink  # noqa: F401
```

`src/comicload/infra/sinks/csv_sink.py`:
```python
from __future__ import annotations

import csv
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from comicload.core.models import CatalogEntry, ImportResult
from comicload.core.registry import register_sink

# Exact column order from a real League of Comic Geeks export.
COLUMNS = [
    "Publisher Name",
    "Series Name",
    "Full Title",
    "Release Date",
    "In Collection",
    "In Wish List",
    "Marked Read",
    "My Rating",
    "Media Format",
    "Price Paid",
    "Date Purchased",
    "Condition",
    "Notes",
    "Tags",
]

REQUIRED = ["Publisher Name", "Series Name", "Full Title"]


def _row(entry: CatalogEntry) -> dict[str, str]:
    return {
        "Publisher Name": entry.publisher_name,
        "Series Name": entry.series_name,
        "Full Title": entry.full_title,
        "Release Date": entry.release_date.isoformat() if entry.release_date else "",
        "In Collection": "1" if entry.in_collection else "0",
        "In Wish List": "1" if entry.in_wish_list else "0",
        "Marked Read": "1" if entry.marked_read else "0",
        "My Rating": entry.my_rating,
        "Media Format": entry.media_format,
        "Price Paid": entry.price_paid,
        "Date Purchased": entry.date_purchased,
        "Condition": entry.condition,
        "Notes": entry.notes,
        "Tags": entry.tags,
    }


def _entry(row: dict[str, str]) -> CatalogEntry:
    raw_date = row.get("Release Date") or ""
    return CatalogEntry(
        publisher_name=row.get("Publisher Name", ""),
        series_name=row.get("Series Name", ""),
        full_title=row.get("Full Title", ""),
        release_date=date.fromisoformat(raw_date) if raw_date else None,
        in_collection=row.get("In Collection") == "1",
        in_wish_list=row.get("In Wish List") == "1",
        marked_read=row.get("Marked Read") == "1",
        my_rating=row.get("My Rating", ""),
        media_format=row.get("Media Format", ""),
        price_paid=row.get("Price Paid", ""),
        date_purchased=row.get("Date Purchased", ""),
        condition=row.get("Condition", ""),
        notes=row.get("Notes", ""),
        tags=row.get("Tags", ""),
    )


@register_sink("csv")
class CsvSink:
    """Writes the League of Comic Geeks bulk-import CSV."""

    name = "csv"

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def push(self, entries: Sequence[CatalogEntry]) -> ImportResult:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS)
            writer.writeheader()
            for entry in entries:
                writer.writerow(_row(entry))
        return ImportResult(
            total=len(entries),
            matched=len(entries),
            unmatched=0,
            destination=str(self._path),
        )


def read_csv(path: Path) -> list[CatalogEntry]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return [_entry(row) for row in csv.DictReader(handle)]


def validate_csv(path: Path) -> list[str]:
    """Return a list of human-readable problems. Empty means the file is importable."""
    problems: list[str] = []
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != COLUMNS:
            problems.append(
                "header does not match the League of Comic Geeks export format; "
                f"expected {len(COLUMNS)} columns starting with 'Publisher Name'"
            )
            return problems
        for number, row in enumerate(reader, start=2):
            for column in REQUIRED:
                if not (row.get(column) or "").strip():
                    problems.append(f"row {number}: '{column}' is empty")
            raw_date = (row.get("Release Date") or "").strip()
            if raw_date:
                try:
                    date.fromisoformat(raw_date)
                except ValueError:
                    problems.append(f"row {number}: 'Release Date' is not YYYY-MM-DD ({raw_date})")
    return problems
```

`src/comicload/services/export.py`:
```python
from __future__ import annotations

from collections.abc import Sequence

from comicload.core.models import Bucket, CatalogEntry, IdentifyResult, ImportResult
from comicload.core.ports import Sink


class ExportService:
    """Pushes confident results to a sink. Ambiguous and unrecognized never leave the queue."""

    def __init__(self, sink: Sink) -> None:
        self._sink = sink

    def export(self, results: Sequence[IdentifyResult]) -> ImportResult:
        entries: list[CatalogEntry] = [
            result.entry
            for result in results
            if result.bucket is Bucket.CONFIDENT and result.entry is not None
        ]
        return self._sink.push(entries)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/infra/test_csv_sink.py tests/services/test_export.py -v`
Expected: PASS — 13 passed

- [ ] **Step 5: Commit**

```bash
git add -f tests/fixtures/locg_export_header.csv
git add src/comicload/infra/sinks src/comicload/services/export.py tests/infra/test_csv_sink.py tests/services/test_export.py
git commit -m "feat(export): add 14-column LoCG CSV sink and ExportService"
```

---

### Task 11: CLI

**Files:**
- Create: `src/comicload/adapters/__init__.py`, `src/comicload/adapters/cli/__init__.py`, `src/comicload/adapters/cli/progress.py`, `src/comicload/adapters/cli/render.py`, `src/comicload/adapters/cli/app.py`
- Test: `tests/adapters/test_cli.py`

**Interfaces:**
- Consumes: everything above
- Produces: Typer app with commands `scan`, `import`, `review`, `catalog sync`, `config`, `config keys`; `main()` entry point; `RichProgressReporter`

- [ ] **Step 1: Write the failing test**

`tests/adapters/test_cli.py`:
```python
from pathlib import Path

from typer.testing import CliRunner

from comicload.adapters.cli.app import app
from comicload.infra.sinks.csv_sink import COLUMNS

runner = CliRunner()
FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "gcd_sample.sql"


def test_help_lists_the_main_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("scan", "import", "review", "catalog", "config"):
        assert command in result.stdout


def test_catalog_sync_loads_dump(tmp_path):
    db = tmp_path / "gcd.sqlite"
    result = runner.invoke(app, ["catalog", "sync", str(FIXTURE), "--db", str(db)])
    assert result.exit_code == 0
    assert db.exists()
    assert "issue" in result.stdout


def test_scan_on_empty_folder_writes_header_only_csv(tmp_path):
    photos = tmp_path / "photos"
    photos.mkdir()
    out = tmp_path / "out.csv"
    db = tmp_path / "gcd.sqlite"
    runner.invoke(app, ["catalog", "sync", str(FIXTURE), "--db", str(db)])

    result = runner.invoke(
        app, ["scan", str(photos), "--out", str(out), "--db", str(db)]
    )

    assert result.exit_code == 0
    assert out.read_text().strip() == ",".join(COLUMNS)


def test_scan_reports_missing_folder_clearly(tmp_path):
    result = runner.invoke(app, ["scan", str(tmp_path / "nope"), "--out", str(tmp_path / "o.csv")])
    assert result.exit_code != 0
    assert "does not exist" in result.stdout


def test_import_validates_a_good_file(tmp_path):
    good = tmp_path / "good.csv"
    good.write_text(
        ",".join(COLUMNS) + "\n" + "Marvel,The Punisher,The Punisher #12,2001-03-01,1,0,0,,,,,,,\n"
    )
    result = runner.invoke(app, ["import", str(good)])
    assert result.exit_code == 0
    assert "1" in result.stdout


def test_import_reports_validation_problems(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("Nope,Wrong\n1,2\n")
    result = runner.invoke(app, ["import", str(bad)])
    assert result.exit_code != 0
    assert "header" in result.stdout.lower()


def test_import_locg_flag_is_rejected_without_the_extra(tmp_path):
    good = tmp_path / "good.csv"
    good.write_text(
        ",".join(COLUMNS) + "\n" + "Marvel,The Punisher,The Punisher #12,2001-03-01,1,0,0,,,,,,,\n"
    )
    result = runner.invoke(app, ["import", str(good), "--import-locg"])
    assert result.exit_code != 0
    assert "comicload[locg]" in result.stdout


def test_config_show_prints_current_settings(tmp_path):
    result = runner.invoke(app, ["config", "show", "--path", str(tmp_path / "c.toml")])
    assert result.exit_code == 0
    assert "csv" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/adapters/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'comicload.adapters'`

- [ ] **Step 3: Write minimal implementation**

`src/comicload/adapters/__init__.py`:
```python
"""Interface adapters. The only layer allowed to import presentation libraries."""
```

`src/comicload/adapters/cli/__init__.py`:
```python
"""Typer + Rich command line adapter."""
```

`src/comicload/adapters/cli/progress.py`:
```python
from __future__ import annotations

from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn


class RichProgressReporter:
    """ProgressReporter backed by Rich. A web adapter would write job state instead."""

    def __init__(self) -> None:
        self._progress: Progress | None = None
        self._task_id: int | None = None

    def start(self, total: int, label: str) -> None:
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("{task.fields[detail]}"),
        )
        self._progress.start()
        self._task_id = self._progress.add_task(label, total=max(total, 1), detail="")

    def advance(self, amount: int = 1, message: str | None = None) -> None:
        if self._progress is None or self._task_id is None:
            return
        self._progress.update(self._task_id, advance=amount, detail=message or "")

    def finish(self) -> None:
        if self._progress is not None:
            self._progress.stop()
            self._progress = None
            self._task_id = None
```

`src/comicload/adapters/cli/render.py`:
```python
from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from comicload.core.models import Bucket, IdentifyResult, ImportResult

console = Console()


def summary_table(results: Sequence[IdentifyResult]) -> Table:
    counts = {bucket: 0 for bucket in Bucket}
    for result in results:
        counts[result.bucket] += 1

    table = Table(title="Scan results", show_header=True, header_style="bold")
    table.add_column("Outcome")
    table.add_column("Count", justify="right")
    table.add_row("[green]Identified[/green]", str(counts[Bucket.CONFIDENT]))
    table.add_row("[yellow]Needs review[/yellow]", str(counts[Bucket.AMBIGUOUS]))
    table.add_row("[red]Not recognised[/red]", str(counts[Bucket.UNRECOGNIZED]))
    return table


def review_table(results: Sequence[IdentifyResult]) -> Table:
    table = Table(title="Waiting for review", show_header=True, header_style="bold")
    table.add_column("Photo")
    table.add_column("Outcome")
    table.add_column("Best guess")
    for result in results:
        best = result.candidates[0] if result.candidates else None
        guess = "—"
        if best:
            guess = " ".join(
                part for part in (best.series, f"#{best.issue_number}" if best.issue_number else None) if part
            ) or (best.barcode or "—")
        table.add_row(result.filename, result.bucket.value, guess)
    return table


def import_panel(result: ImportResult) -> Panel:
    lines = [
        f"Comics sent:   [bold]{result.total}[/bold]",
        f"Matched:       [green]{result.matched}[/green]",
        f"Not matched:   [yellow]{result.unmatched}[/yellow]",
        f"Destination:   {result.destination}",
    ]
    if result.view_url:
        lines.append("")
        lines.append(f"View your collection: [link={result.view_url}]{result.view_url}[/link]")
    return Panel("\n".join(lines), title="Import complete", border_style="green")
```

`src/comicload/adapters/cli/app.py`:
```python
from __future__ import annotations

from pathlib import Path

import typer

import comicload.infra.signals  # noqa: F401  (registers signals)
import comicload.infra.sinks  # noqa: F401  (registers sinks)
from comicload.adapters.cli.progress import RichProgressReporter
from comicload.adapters.cli.render import console, import_panel, review_table, summary_table
from comicload.core.errors import ComicloadError
from comicload.core.models import Bucket, Scope
from comicload.core.registry import get_signal
from comicload.infra.config import Config, default_config_path, load_config, save_config
from comicload.infra.photos import LocalFolderPhotoSource
from comicload.infra.sinks.csv_sink import CsvSink, read_csv, validate_csv
from comicload.infra.storage.gcd_loader import load_dump
from comicload.infra.storage.gcd_repo import SqliteIssueResolver
from comicload.services.export import ExportService
from comicload.services.identify import IdentifyService

app = typer.Typer(
    help="Photograph your comics, identify them, and build your League of Comic Geeks collection.",
    no_args_is_help=True,
)
catalog_app = typer.Typer(help="Manage the local comic metadata database.")
config_app = typer.Typer(help="Set up comicload.")
app.add_typer(catalog_app, name="catalog")
app.add_typer(config_app, name="config")


def _scope(publisher: str | None, years: str | None) -> Scope:
    year_from = year_to = None
    if years:
        parts = years.split("-")
        if len(parts) != 2 or not all(p.strip().isdigit() for p in parts):
            raise typer.BadParameter("--years must look like 1970-1985")
        year_from, year_to = int(parts[0]), int(parts[1])
    return Scope(publisher=publisher, year_from=year_from, year_to=year_to)


@app.command()
def scan(
    folder: Path = typer.Argument(..., help="Folder containing your cover photos."),
    out: Path = typer.Option(Path("collection.csv"), "--out", "-o", help="Where to write the CSV."),
    publisher: str | None = typer.Option(None, "--publisher", help="Narrow to one publisher."),
    years: str | None = typer.Option(None, "--years", help="Narrow to a year range, e.g. 1970-1985."),
    db: Path | None = typer.Option(None, "--db", help="Path to the metadata database."),
) -> None:
    """Identify every comic photo in a folder and write an import file."""
    config = load_config()
    database = db or config.gcd_db_path()

    try:
        source = LocalFolderPhotoSource(folder)
        source.count()
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    signals = [get_signal(name) for name in config.signals.enabled]
    service = IdentifyService(
        signals=signals,
        resolver=SqliteIssueResolver(database),
        progress=RichProgressReporter(),
    )

    try:
        results = service.run(source, _scope(publisher, years))
    except ComicloadError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    console.print(summary_table(results))

    result = ExportService(CsvSink(out)).export(results)
    console.print(import_panel(result))

    pending = [r for r in results if r.bucket is not Bucket.CONFIDENT]
    if pending:
        console.print(
            f"\n[yellow]{len(pending)} photo(s) need a look.[/yellow] "
            "Run [bold]comicload review[/bold] to see them."
        )


@app.command(name="import")
def import_csv(
    file: Path = typer.Argument(..., help="The CSV to check or upload."),
    import_locg: bool = typer.Option(
        False, "--import-locg", help="Actually upload to League of Comic Geeks."
    ),
) -> None:
    """Check an import file, and optionally send it to League of Comic Geeks."""
    if not file.exists():
        console.print(f"[red]No such file: {file}[/red]")
        raise typer.Exit(code=1)

    problems = validate_csv(file)
    if problems:
        console.print("[red]This file is not ready to import:[/red]")
        for problem in problems[:20]:
            console.print(f"  • {problem}")
        if len(problems) > 20:
            console.print(f"  … and {len(problems) - 20} more")
        raise typer.Exit(code=1)

    entries = read_csv(file)
    console.print(f"[green]Looks good.[/green] {len(entries)} comic(s) ready to import.")

    if not import_locg:
        console.print(
            "\nNothing was uploaded. Add [bold]--import-locg[/bold] to send this to "
            "League of Comic Geeks."
        )
        return

    try:
        from comicload.infra.sinks.locg_sink import LocgPlaywrightSink
    except ImportError:
        console.print(
            "[red]Uploading needs the optional browser extra.[/red]\n"
            "Install it with: [bold]pip install 'comicload[locg]'[/bold]"
        )
        raise typer.Exit(code=1) from None

    config = load_config()
    result = LocgPlaywrightSink(config.locg_state_path()).push(entries)
    console.print(import_panel(result))


@app.command()
def review() -> None:
    """Look at the comics comicload could not identify on its own."""
    console.print(review_table([]))
    console.print(
        "[dim]Nothing is stored between runs yet — the review queue lands with the "
        "database task.[/dim]"
    )


@catalog_app.command("sync")
def catalog_sync(
    dump: Path = typer.Argument(..., help="Path to the downloaded GCD .sql dump."),
    db: Path | None = typer.Option(None, "--db", help="Where to build the database."),
) -> None:
    """Build the local comic metadata database from a Grand Comics Database dump."""
    target = db or load_config().gcd_db_path()
    try:
        counts = load_dump(dump, target)
    except ComicloadError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    for table, count in counts.items():
        console.print(f"  {table}: [bold]{count:,}[/bold] rows")
    console.print(f"[green]Database ready:[/green] {target}")


@config_app.command("show")
def config_show(
    path: Path | None = typer.Option(None, "--path", help="Config file to read."),
) -> None:
    """Show your current settings."""
    config = load_config(path)
    console.print_json(config.model_dump_json(indent=2))


@config_app.command("init")
def config_init(
    path: Path | None = typer.Option(None, "--path", help="Config file to write."),
) -> None:
    """Create a settings file with sensible defaults."""
    target = save_config(Config(), path)
    console.print(f"[green]Settings written to[/green] {target}")


@config_app.command("keys")
def config_keys(
    name: str = typer.Argument(..., help="Which key to store, e.g. comicload/anthropic."),
) -> None:
    """Store an API key in your system keychain. It is never written to a file."""
    from comicload.infra.secrets import KeyringSecretStore

    value = typer.prompt("Value", hide_input=True)
    KeyringSecretStore().set(name, value)
    console.print(f"[green]Saved[/green] {name} to your keychain.")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/adapters/test_cli.py -v`
Expected: PASS — 8 passed

Then run the whole suite: `python -m pytest -v`
Expected: PASS — all tests green

- [ ] **Step 5: Commit**

```bash
git add src/comicload/adapters tests/adapters/test_cli.py
git commit -m "feat(cli): add scan, import, review, catalog, and config commands"
```

---

### Task 12: README for non-technical readers

**Files:**
- Create: `README.md`
- Modify: none

**Interfaces:**
- Consumes: the CLI surface from Task 11
- Produces: nothing code depends on

Principle 5 requires this to read for someone who collects comics, not someone who writes Python. No jargon, task-first, every command shown with what it prints.

- [ ] **Step 1: Write the failing test**

`tests/test_readme.py`:
```python
from pathlib import Path

README = Path(__file__).resolve().parent.parent / "README.md"


def test_readme_exists():
    assert README.exists()


def test_readme_documents_every_user_facing_command():
    text = README.read_text()
    for command in (
        "comicload catalog sync",
        "comicload scan",
        "comicload import",
        "comicload review",
        "comicload config",
    ):
        assert command in text, f"README does not explain '{command}'"


def test_readme_mentions_the_zbar_prerequisite():
    assert "zbar" in README.read_text().lower()


def test_readme_avoids_unexplained_jargon():
    """Words that mean nothing to a comic collector must not appear in the README."""
    banned = ["hexagonal", "dependency injection", "protocol", "adapter pattern", "ANN index"]
    text = README.read_text().lower()
    for word in banned:
        assert word.lower() not in text, f"README uses jargon: {word}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_readme.py -v`
Expected: FAIL — `assert README.exists()` fails

- [ ] **Step 3: Write minimal implementation**

`README.md`:
````markdown
# comicload

Take photos of your comic covers. Get them into your League of Comic Geeks collection.

comicload looks at each photo, works out which comic it is, and builds a file you can
upload to League of Comic Geeks. Comics it cannot work out are set aside for you to
check, never guessed at.

## What you need first

**A Mac with Python 3.12 or newer.** Check by opening Terminal and running:

```bash
python3 --version
```

**One extra piece of software** for reading barcodes:

```bash
brew install zbar
```

If you do not have `brew`, install it from [brew.sh](https://brew.sh) first.

## Installing

```bash
pip install comicload
```

## Setting up, once

comicload identifies your comics by looking them up in a free public comic database
called the Grand Comics Database. You download it once, and after that comicload works
entirely on your own computer — no internet needed, no fees, nothing sent anywhere.

1. Make a free account at [comics.org](https://www.comics.org/) and download their data dump.
2. Point comicload at the downloaded file:

```bash
comicload catalog sync ~/Downloads/gcd_dump.sql
```

This takes a few minutes and only needs doing once. Repeat it every few months if you
want newer comics included.

## Cataloguing your comics

Put your photos in a folder — one comic per photo, the whole cover in frame.

```bash
comicload scan ~/Desktop/my-comics --out collection.csv
```

You will see a progress bar, then a summary like this:

```
      Scan results
┏━━━━━━━━━━━━━━━━┳━━━━━━━┓
┃ Outcome        ┃ Count ┃
┡━━━━━━━━━━━━━━━━╇━━━━━━━┩
│ Identified     │    47 │
│ Needs review   │     3 │
│ Not recognised │     1 │
└────────────────┴───────┘
```

It helps a lot to tell comicload roughly what it is looking at:

```bash
comicload scan ~/Desktop/my-comics --publisher Marvel --years 1970-1985
```

## Checking the ones it was unsure about

```bash
comicload review
```

This shows every comic comicload could not identify confidently, so you can sort them
out yourself.

## Getting them into League of Comic Geeks

First, check the file is good. This does not upload anything:

```bash
comicload import collection.csv
```

If it says everything looks good, upload it:

```bash
comicload import collection.csv --import-locg
```

You will be shown exactly what is about to be added and asked to confirm before
anything happens. When it finishes, you get a link straight to your collection.

Uploading needs one extra install, once:

```bash
pip install 'comicload[locg]'
comicload config locg
```

That last command opens a browser window where you log in to League of Comic Geeks
yourself. comicload never sees your password.

### Prefer to upload by hand?

You do not have to use `--import-locg` at all. `collection.csv` is a normal file you
can upload through the League of Comic Geeks website using their own Bulk Import page.

## Settings

```bash
comicload config show      # see your current settings
comicload config init      # create a settings file
comicload config keys      # store an API key safely in your keychain
```

## Taking good photos

- One comic per photo
- Whole cover in frame
- As flat-on as you can manage
- Avoid glare across the barcode — that is what comicload reads first
- Comics from before about 1975 have no barcode, so they are harder and more likely to
  need review

## Getting help

If something is not working, run the command again with the folder path in quotes, and
check the message comicload prints — it tries to say plainly what went wrong.
````

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_readme.py -v`
Expected: PASS — 4 passed

Then the whole suite: `python -m pytest -v`
Expected: PASS — all green

- [ ] **Step 5: Commit**

```bash
git add README.md tests/test_readme.py
git commit -m "docs: add README written for comic collectors, not developers"
```

---

## Self-Review

**Spec coverage.** Every Phase 1 item in the spec maps to a task: hexagonal layering (Tasks 2, 3, plus the enforcement test), config and secrets (4), photo source (5), barcode signal (6), GCD mirror (7, 8), fusion and bucketing (9), CSV export (10), CLI (11), README (12). Principle 7 (`view_url`) is carried through `ImportResult` in Task 2, passed through `ExportService` in Task 10, and rendered in `import_panel` in Task 11 — the plumbing lands in Phase 1 even though the URL is only populated by the Phase 4 LoCG sink.

**Deliberately deferred, with reasons:**
- `Repository` port is declared in Task 3 but has no implementation. `comicload review` therefore shows an empty queue and says so. Persisting the review queue is the first task of the next plan — declaring the port now keeps `IdentifyService` unchanged when it arrives.
- Preprocessing (dewarp, deglare, CLAHE) is not in Phase 1. `pyzbar` decodes acceptably from raw photos, and preprocessing earns its complexity only when OCR arrives in Phase 2. `Signal.identify` takes a raw `Photo` for exactly this reason — adding a shared preprocessing step later does not change the port.
- `comicload config locg` is stubbed in the CLI but the Playwright sink does not exist. `import --import-locg` fails with an install instruction rather than a traceback, and Task 11 tests that behaviour.

**Known risk.** Task 7 is written against an assumed GCD schema. Its Step 0 instruction is to confirm real column names before finalising, and the fixture-based tests stay valid either way because the fixture and the mapping change together.

---

## Next plans

- **Phase 4 — LoCG import.** `LocgPlaywrightSink`, `comicload config locg`, `view_url` capture, HAR-replay tests. Independent of Phases 2–3; can follow immediately.
- **Phase 2 — OCR signal.** Needs an era-spanning fixture photo set assembled first.
- **Phase 3 — cover matching.** Blocked on resolving cover-image licensing.

---

### Task 13: Developer experience — one-command setup, test, run

**Executed out of order, immediately after Task 1, so every later task benefits.**

**Files:**
- Create: `Makefile`, `CONTRIBUTING.md`
- Test: `tests/test_dx.py`

**Interfaces:**
- Consumes: `pyproject.toml` from Task 1
- Produces: `make setup`, `make install`, `make test`, `make lint`, `make typecheck`, `make check`, `make run`, `make clean`

**Why CONTRIBUTING.md and not README:** principle 5 requires the README to read for comic collectors. Developer instructions live separately so neither audience wades through the other's content.

- [ ] **Step 1: Write the failing test**

`tests/test_dx.py`:
```python
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAKEFILE = ROOT / "Makefile"
CONTRIBUTING = ROOT / "CONTRIBUTING.md"

REQUIRED_TARGETS = [
    "setup",
    "install",
    "test",
    "lint",
    "typecheck",
    "check",
    "run",
    "clean",
]


def _targets() -> set[str]:
    pattern = re.compile(r"^([a-zA-Z][\w-]*):", re.MULTILINE)
    return set(pattern.findall(MAKEFILE.read_text()))


def test_makefile_exists():
    assert MAKEFILE.exists()


def test_every_required_target_is_defined():
    defined = _targets()
    missing = [t for t in REQUIRED_TARGETS if t not in defined]
    assert not missing, f"Makefile is missing targets: {missing}"


def test_all_targets_are_phony():
    """Every target is a command, not a file, so none may be skipped as up-to-date."""
    text = MAKEFILE.read_text()
    phony = re.search(r"^\.PHONY:\s*(.+)$", text, re.MULTILINE)
    assert phony, "Makefile must declare .PHONY"
    declared = set(phony.group(1).split())
    missing = [t for t in REQUIRED_TARGETS if t not in declared]
    assert not missing, f"targets not declared .PHONY: {missing}"


def test_make_help_is_the_default_and_lists_targets():
    result = subprocess.run(
        ["make", "-C", str(ROOT)], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    for target in REQUIRED_TARGETS:
        assert target in result.stdout, f"'make' output does not mention '{target}'"


def test_make_lint_passes_on_current_tree():
    result = subprocess.run(
        ["make", "-C", str(ROOT), "lint"], capture_output=True, text=True, timeout=180
    )
    assert result.returncode == 0, f"make lint failed:\n{result.stdout}\n{result.stderr}"


def test_contributing_documents_setup_and_test():
    assert CONTRIBUTING.exists()
    text = CONTRIBUTING.read_text()
    assert "make setup" in text
    assert "make test" in text
    assert "zbar" in text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_dx.py -v`
Expected: FAIL — `assert MAKEFILE.exists()` fails

- [ ] **Step 3: Write minimal implementation**

`Makefile`:
```make
.PHONY: help setup install test lint typecheck check run clean

PYTHON := .venv/bin/python
UV := uv

help:
	@echo "comicload — development commands"
	@echo ""
	@echo "  make setup      create .venv and install everything (run this first)"
	@echo "  make install    reinstall the package in editable mode"
	@echo "  make test       run the test suite"
	@echo "  make lint       check formatting and style with ruff"
	@echo "  make typecheck  check types with mypy"
	@echo "  make check      lint + typecheck + test (run before every commit)"
	@echo "  make run        run the CLI, e.g. make run ARGS='scan ./photos'"
	@echo "  make clean      remove caches and build artifacts"

setup:
	$(UV) venv --python 3.12 .venv
	$(UV) pip install --python $(PYTHON) -e ".[dev]"
	@echo ""
	@echo "Ready. Barcode reading also needs the zbar library: brew install zbar"

install:
	$(UV) pip install --python $(PYTHON) -e ".[dev]"

test:
	$(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff check src tests
	$(PYTHON) -m ruff format --check src tests

typecheck:
	$(PYTHON) -m mypy

check: lint typecheck test

run:
	$(PYTHON) -m comicload.adapters.cli.app $(ARGS)

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache build dist *.egg-info
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} +
```

`CONTRIBUTING.md`:
````markdown
# Developing comicload

## Setup

One command:

```bash
make setup
```

That creates `.venv` with Python 3.12 and installs comicload plus its dev tools
in editable mode.

Barcode reading needs the native zbar library, which pip cannot install:

```bash
brew install zbar
```

The test suite does not need zbar — the barcode tests inject a stub decoder — so
you can develop and test without it. You only need it to run against real photos.

## Everyday commands

```bash
make test        # run the suite
make lint        # ruff check + format check
make typecheck   # mypy
make check       # all three — run this before committing
```

## Running the CLI locally

```bash
make run ARGS='--help'
make run ARGS='scan ./photos --out out.csv'
```

Or activate the venv and use the installed entry point:

```bash
source .venv/bin/activate
comicload --help
```

## Architecture rules

`tests/test_architecture.py` enforces the layering, and it will fail the build if
broken:

- `core/` imports stdlib only
- `services/` imports `core/` only
- `infra/` imports `core/` only
- Only `adapters/` may import Rich or Typer, or call `print()`

Everything else reports progress through the `ProgressReporter` port. This is what
keeps a future web interface a sibling of `adapters/cli/` rather than a rewrite.

## Adding a signal or a sink

Register it — do not edit existing files:

```python
@register_signal("my_signal")
class MySignal:
    name = "my_signal"

    def identify(self, photo: Photo, scope: Scope) -> list[Candidate]: ...
```

Then enable it in config under `[signals] enabled`.

## Tests

Every commit must be green. Tests live beside the layer they cover under `tests/`.
Fixtures are in `tests/fixtures/` and are force-added past `.gitignore`, which
excludes `*.csv` and `*.sqlite` by default.
````

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_dx.py -v`
Expected: PASS — 6 passed

Then: `make check`
Expected: lint, typecheck, and tests all pass

- [ ] **Step 5: Commit**

```bash
git add Makefile CONTRIBUTING.md tests/test_dx.py
git commit -m "chore: add Makefile and contributor guide for one-command setup"
```

---

### Task 14: Local catalogue storage

**Runs after Task 10, before Task 11, so the CLI can wire `review` properly.**

**Files:**
- Create: `src/comicload/infra/storage/catalogue.py`
- Test: `tests/infra/test_catalogue.py`

**Interfaces:**
- Consumes: `IdentifyResult`, `CatalogEntry`, `Bucket` (Task 2); `Repository` port (Task 3)
- Produces: `SqliteRepository(db_path: Path)` implementing `Repository`; `CATALOGUE_SCHEMA`

**Why a separate database from the GCD mirror.** `gcd.sqlite` is a disposable mirror —
regenerated wholesale by `catalog sync`, potentially gigabytes, safe to delete. The user's
scan results are the opposite: irreplaceable, small, worth backing up. They live in
`comicload.sqlite`, obtained from `Config.catalogue_db_path()`.

**Idempotency.** Photo ids are content hashes, so re-scanning the same folder must update
rows rather than duplicate them. `save()` upserts on `photo_id`.

**Migrations are required here and only here.** `gcd.sqlite` is regenerable — a schema change
is handled by re-running `catalog sync`. `comicload.sqlite` holds data the user cannot rebuild,
so `CREATE TABLE IF NOT EXISTS` is not a sufficient migration story: the first added column
would break existing databases. Use SQLite's built-in `PRAGMA user_version` as the schema
stamp and apply migrations stepwise. No dependency, no ORM — roughly twenty lines.

**No ORM, deliberately.** Three read-only GCD tables and one catalogue table do not justify
one, the bulk loader would bypass it regardless, and the Postgres-later path is served by
writing a new adapter behind the existing `Repository` port rather than by reconfiguring a
mapper. Revisit only if the storyline feature's arc tables introduce cross-catalog joins.

- [ ] **Step 1: Write the failing test**

`tests/infra/test_catalogue.py`:
```python
from datetime import date

import pytest

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
    conn.execute(
        "INSERT INTO scan_result VALUES ('p9', 'old.jpg', 'unrecognized', NULL, '[]')"
    )
    conn.commit()
    conn.close()

    repo = SqliteRepository(db)
    pending = repo.pending_review()

    assert [r.photo_id for r in pending] == ["p9"], "existing row was lost during migration"
    version = sqlite3.connect(db).execute("PRAGMA user_version").fetchone()[0]
    assert version == SCHEMA_VERSION
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/infra/test_catalogue.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'comicload.infra.storage.catalogue'`

- [ ] **Step 3: Write minimal implementation**

`src/comicload/infra/storage/catalogue.py`:
```python
"""The user's own catalogue: scan outcomes and the review queue.

Deliberately a separate database from the GCD mirror. `gcd.sqlite` is a disposable
mirror rebuilt by `catalog sync`; this file holds the user's irreplaceable results.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from comicload.core.models import Bucket, Candidate, CatalogEntry, IdentifyResult

SCHEMA_VERSION = 1

# Index = target version. MIGRATIONS[0] takes an empty or pre-versioned database to
# version 1. To evolve the schema, append a new script and bump SCHEMA_VERSION —
# never edit an existing entry, since users have already run it.
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
)


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring a database up to SCHEMA_VERSION, preserving existing rows.

    The user's catalogue cannot be regenerated, so schema changes must migrate
    rather than recreate. SQLite's user_version pragma is the stamp.
    """
    current: int = conn.execute("PRAGMA user_version").fetchone()[0]
    for version in range(current, SCHEMA_VERSION):
        conn.executescript(MIGRATIONS[version])
    if current < SCHEMA_VERSION:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()


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

    def _connect(self) -> sqlite3.Connection:
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
            )
            for result in results
        ]
        conn = self._connect()
        try:
            conn.executemany(
                """
                INSERT INTO scan_result (photo_id, filename, bucket, entry, candidates)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(photo_id) DO UPDATE SET
                    filename   = excluded.filename,
                    bucket     = excluded.bucket,
                    entry      = excluded.entry,
                    candidates = excluded.candidates
                """,
                rows,
            )
            conn.commit()
        finally:
            conn.close()

    def _select(self, where: str, params: Sequence[str]) -> list[IdentifyResult]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT photo_id, filename, bucket, entry, candidates "
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
            )
            for row in rows
        ]

    def pending_review(self) -> list[IdentifyResult]:
        return self._select("bucket != ?", [Bucket.CONFIDENT.value])

    def confirmed_entries(self) -> list[CatalogEntry]:
        results = self._select("bucket = ?", [Bucket.CONFIDENT.value])
        return [r.entry for r in results if r.entry is not None]
```

Add to `src/comicload/infra/config.py`, inside `CatalogConfig`:

```python
class CatalogConfig(BaseModel):
    gcd_db: str = ""
    catalogue_db: str = ""
```

and to `Config`:

```python
    def catalogue_db_path(self) -> Path:
        if self.catalog.catalogue_db:
            return Path(self.catalog.catalogue_db).expanduser()
        return user_data_path("comicload") / "comicload.sqlite"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/infra/test_catalogue.py -v`
Expected: PASS — 8 passed

Then `make check`
Expected: all green

- [ ] **Step 5: Commit**

```bash
git add src/comicload/infra/storage/catalogue.py src/comicload/infra/config.py tests/infra/test_catalogue.py
git commit -m "feat(storage): persist scan results and review queue in local catalogue"
```
