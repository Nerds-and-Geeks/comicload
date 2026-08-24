"""Storage backends are selected by DSN scheme, the same way signals and sinks are
selected by name. Adding a backend requires no edit to any existing file.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar, cast

from comicload.core.ports import IssueResolver, Repository

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Dsn:
    """A parsed storage address. `scheme` picks the backend; `target` is its own business."""

    scheme: str
    target: str


class SupportsFromDsn(Protocol):
    """What a backend must offer so the registry can build it from an address alone."""

    @classmethod
    def from_dsn(cls, dsn: Dsn) -> Any: ...


repository_registry: dict[str, type] = {}
resolver_registry: dict[str, type] = {}


_WINDOWS_DRIVE = re.compile(r"^/[A-Za-z]:")


def _expand_target(target: str) -> str:
    """Undo the slash the three-slash form leaves in front of `~` and of a drive letter.

    `sqlite:///~/comicload.sqlite` and `sqlite:///C:/comics.db` are both how people write
    these addresses; without this, the first stays literal and the second becomes the
    nonexistent path `/C:/comics.db`.
    """
    if target.startswith("/~") or _WINDOWS_DRIVE.match(target):
        target = target[1:]
    return os.path.expanduser(target)


def parse_dsn(dsn: str) -> Dsn:
    if "://" not in dsn:
        raise ValueError(
            f"storage address must include a scheme, e.g. 'sqlite:///path/to.db', got {dsn!r}"
        )
    scheme, _, target = dsn.partition("://")
    if not target.strip():
        raise ValueError(
            f"storage address {dsn!r} names no location; write where the data lives, "
            "e.g. 'sqlite:///path/to.db'"
        )
    return Dsn(scheme=scheme.lower(), target=_expand_target(target))


def _register(registry: dict[str, type], scheme: str, kind: str) -> Callable[[type[T]], type[T]]:
    def decorator(cls: type[T]) -> type[T]:
        if scheme in registry:
            raise ValueError(f"{kind} backend for scheme '{scheme}' is already registered")
        registry[scheme] = cls
        return cls

    return decorator


def register_repository(scheme: str) -> Callable[[type[T]], type[T]]:
    """Register a Repository implementation under a DSN scheme."""
    return _register(repository_registry, scheme, "repository")


def register_resolver(scheme: str) -> Callable[[type[T]], type[T]]:
    """Register an IssueResolver implementation under a DSN scheme."""
    return _register(resolver_registry, scheme, "resolver")


def _open(registry: dict[str, type], dsn: str, kind: str) -> Any:
    parsed = parse_dsn(dsn)
    if parsed.scheme not in registry:
        known = ", ".join(sorted(registry)) or "none"
        raise KeyError(f"no {kind} backend registered for '{parsed.scheme}'; available: {known}")
    backend = cast(type[SupportsFromDsn], registry[parsed.scheme])
    return backend.from_dsn(parsed)


def open_repository(dsn: str) -> Repository:
    """A backend for a DSN, typed as the port it must satisfy.

    The cast is the same deal core.registry makes: the registry cannot prove a backend
    implements the Protocol, but returning Any would switch off type checking at exactly
    the seam these Protocols exist to guard.
    """
    return cast(Repository, _open(repository_registry, dsn, "repository"))


def open_resolver(dsn: str) -> IssueResolver:
    """A resolver backend for a DSN, typed as the port it must satisfy."""
    return cast(IssueResolver, _open(resolver_registry, dsn, "resolver"))


def available_repositories() -> list[str]:
    return sorted(repository_registry)


def available_resolvers() -> list[str]:
    return sorted(resolver_registry)
