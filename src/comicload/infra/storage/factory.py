"""Importing this module registers every built-in storage backend."""

from __future__ import annotations

from comicload.core.storage_registry import open_repository, open_resolver
from comicload.infra.storage import catalogue, gcd_repo  # noqa: F401  (registers backends)

__all__ = ["open_repository", "open_resolver"]
