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
