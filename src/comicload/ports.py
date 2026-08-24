from __future__ import annotations

from collections.abc import Iterator, Sequence
from types import TracebackType
from typing import Protocol, runtime_checkable

from comicload.models import (
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

    def resolve(self, candidate: Candidate, scope: Scope | None = None) -> list[Issue]: ...

    def close(self) -> None: ...

    def __enter__(self) -> IssueResolver: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...


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

    def clear(self) -> None: ...

    def clear_pending(self) -> None: ...


@runtime_checkable
class ProgressReporter(Protocol):
    """How long-running work reports itself. Rich in the CLI; job state on the web."""

    def start(self, total: int, label: str) -> None: ...

    def advance(self, amount: int = 1, message: str | None = None) -> None: ...

    def finish(self) -> None: ...


class NullProgressReporter:
    """No-op reporter. The default for services and the one every test uses."""

    def start(self, total: int, label: str) -> None:
        return None

    def advance(self, amount: int = 1, message: str | None = None) -> None:
        return None

    def finish(self) -> None:
        return None
