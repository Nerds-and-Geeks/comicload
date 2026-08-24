from pathlib import Path

import pytest

from comicload.core.storage_registry import (
    open_repository,
    parse_dsn,
    register_repository,
    repository_registry,
)


@pytest.fixture(autouse=True)
def clean_registry():
    """An empty registry for these tests, with the real backends put back afterwards.

    Restoring matters: the built-in backends register once, at import time, so a bare
    clear() would leave every later test module without a 'sqlite' backend.
    """
    builtins = dict(repository_registry)
    repository_registry.clear()
    yield
    repository_registry.clear()
    repository_registry.update(builtins)


def test_parse_dsn_splits_scheme_and_target():
    dsn = parse_dsn("sqlite:///tmp/comicload.sqlite")
    assert dsn.scheme == "sqlite"
    assert dsn.target == "/tmp/comicload.sqlite"


def test_parse_dsn_handles_a_network_backend():
    dsn = parse_dsn("postgresql://user@host:5432/comicload")
    assert dsn.scheme == "postgresql"
    assert dsn.target == "user@host:5432/comicload"


def test_parse_dsn_expands_user_home():
    dsn = parse_dsn("sqlite:///~/comicload.sqlite")
    assert "~" not in dsn.target


def test_parse_dsn_rejects_a_bare_path():
    with pytest.raises(ValueError, match="sqlite:///"):
        parse_dsn("/tmp/comicload.sqlite")


def test_open_repository_dispatches_on_scheme():
    @register_repository("memory")
    class MemoryRepository:
        def __init__(self, target: str) -> None:
            self.target = target

        @classmethod
        def from_dsn(cls, dsn):
            return cls(dsn.target)

        def save(self, results):
            return None

        def pending_review(self):
            return []

        def confirmed_entries(self):
            return []

    repo = open_repository("memory://somewhere")
    assert repo.target == "somewhere"


def test_unknown_scheme_names_what_is_registered():
    with pytest.raises(KeyError, match="postgresql"):
        open_repository("postgresql://host/db")


def test_duplicate_scheme_registration_is_rejected():
    @register_repository("dupe")
    class One:
        @classmethod
        def from_dsn(cls, dsn):
            return cls()

    with pytest.raises(ValueError, match="already registered"):

        @register_repository("dupe")
        class Two:
            @classmethod
            def from_dsn(cls, dsn):
                return cls()


# --- the storage seam must stay type-checked ---------------------------------


def test_open_repository_is_typed_as_the_port_not_any():
    """Returning Any here switched off mypy at exactly the boundary the port guards."""
    import typing

    from comicload.core.ports import IssueResolver, Repository
    from comicload.core.storage_registry import open_resolver

    assert typing.get_type_hints(open_repository)["return"] is Repository
    assert typing.get_type_hints(open_resolver)["return"] is IssueResolver


def test_mypy_rejects_a_call_the_repository_port_does_not_have(tmp_path, monkeypatch):
    """The proof that type checking is really back on at the storage boundary."""
    from mypy import api

    src = Path(__file__).resolve().parent.parent.parent / "src"
    monkeypatch.setenv("MYPYPATH", str(src))

    snippet = tmp_path / "snippet.py"
    snippet.write_text(
        "from comicload.core.storage_registry import open_repository\n"
        "open_repository('sqlite:///x.db').no_such_method()\n"
    )
    stdout, _, status = api.run(["--strict", "--no-incremental", str(snippet)])

    assert status != 0, f"mypy accepted a call the Repository port does not have:\n{stdout}"
    assert "no_such_method" in stdout, stdout
