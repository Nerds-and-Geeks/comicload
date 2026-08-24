from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar, cast

from comicload.domain.ports import Signal, Sink

signal_registry: dict[str, type] = {}
sink_registry: dict[str, type] = {}

T = TypeVar("T")


def _register(registry: dict[str, type], name: str, kind: str) -> Callable[[type[T]], type[T]]:
    def decorator(cls: type[T]) -> type[T]:
        if name in registry:
            raise ValueError(f"{kind} '{name}' is already registered")
        registry[name] = cls
        return cls

    return decorator


def register_signal(name: str) -> Callable[[type[T]], type[T]]:
    """Register a Signal implementation. Adding one requires no edits to existing files."""
    return _register(signal_registry, name, "signal")


def register_sink(name: str) -> Callable[[type[T]], type[T]]:
    """Register a Sink implementation."""
    return _register(sink_registry, name, "sink")


def _get(registry: dict[str, type], name: str, kind: str, **kwargs: object) -> object:
    if name not in registry:
        known = ", ".join(sorted(registry)) or "none"
        raise KeyError(f"unknown {kind} '{name}'; registered: {known}")
    return registry[name](**kwargs)


def get_signal(name: str, **kwargs: object) -> Signal:
    return cast(Signal, _get(signal_registry, name, "signal", **kwargs))


def get_sink(name: str, **kwargs: object) -> Sink:
    return cast(Sink, _get(sink_registry, name, "sink", **kwargs))


def available_signals() -> list[str]:
    return sorted(signal_registry)


def available_sinks() -> list[str]:
    return sorted(sink_registry)
