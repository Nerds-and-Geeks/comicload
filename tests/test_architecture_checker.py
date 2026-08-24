"""Proves the layering checker rejects what it claims to reject.

Every case below is a violation that was injected into the real tree and passed the
previous checker unnoticed. Running them against synthetic sources rather than by
mutating src/ keeps the proof fast, precise, and free of cleanup that can go wrong.
"""

import pytest
from test_architecture import check_source


def _rejects(relative: str, source: str) -> str:
    problems = check_source(relative, source)
    assert problems, f"{relative} was accepted but should have been rejected:\n{source}"
    return "\n".join(problems)


# --- the six violations that slipped through -------------------------------------


def test_relative_import_across_layers_is_rejected():
    """`node.module` alone never sees a relative import; `node.level` has to be resolved."""
    report = _rejects("export/csv.py", "from ..cli.render import console\n")
    assert "comicload.cli.render" in report


def test_deeply_relative_import_across_layers_is_rejected():
    report = _rejects("quarantine/repository.py", "from ...cli.render import console\n")
    assert "comicload.cli.render" in report


def test_dynamic_import_of_another_layer_is_rejected():
    report = _rejects(
        "export/csv.py",
        "import importlib\nrender = importlib.import_module('comicload.cli.render')\n",
    )
    assert "import_module" in report


def test_third_party_import_in_core_is_rejected():
    report = _rejects("domain/models.py", "import pydantic\n")
    assert "standard library" in report


def test_stdout_write_outside_adapters_is_rejected():
    report = _rejects("export/csv.py", "import sys\nsys.stdout.write('hello')\n")
    assert "standard stream" in report


def test_stderr_write_outside_adapters_is_rejected():
    report = _rejects("ingestion/photos.py", "import sys\nsys.stderr.writelines(['x'])\n")
    assert "standard stream" in report


def test_imported_stream_write_is_rejected():
    report = _rejects("ingestion/photos.py", "from sys import stdout\nstdout.write('x')\n")
    assert "standard stream" in report


def test_aliased_print_is_rejected():
    report = _rejects("export/csv.py", "from builtins import print as _echo\n_echo('hello')\n")
    assert "_echo()" in report


def test_rebound_print_is_rejected():
    report = _rejects("ingestion/photos.py", "_say = print\n_say('hello')\n")
    assert "_say()" in report


def test_builtins_print_attribute_is_rejected():
    report = _rejects("export/csv.py", "import builtins\nbuiltins.print('hello')\n")
    assert "builtins.print()" in report


def test_a_module_outside_every_layer_is_rejected():
    report = _rejects("helpers.py", "import typer\nprint('hi')\n")
    assert "not inside a known layer" in report


# --- and the original checks still hold ------------------------------------------


def test_plain_print_outside_adapters_is_rejected():
    assert "print()" in _rejects("domain/models.py", "print('hello')\n")


def test_rich_import_outside_adapters_is_rejected():
    assert "cli" in _rejects("ingestion/photos.py", "from rich.console import Console\n")


def test_catalog_may_not_import_cli():
    report = _rejects("catalog/loader.py", "from comicload.cli.app import x\n")
    assert "may not depend on" in report


def test_signals_may_not_import_cli():
    report = _rejects("signals/barcode.py", "from comicload.cli.app import x\n")
    assert "may not depend on" in report


# --- what must stay allowed ------------------------------------------------------


@pytest.mark.parametrize(
    ("relative", "source"),
    [
        ("domain/models.py", "from dataclasses import dataclass\nimport sqlite3\n"),
        ("export/csv.py", "from comicload.domain.models import Bucket\n"),
        ("ingestion/photos.py", "import pydantic\nfrom comicload.domain.models import Photo\n"),
        ("quarantine/repository.py", "from ..config import load_config\n"),
        ("cli/app.py", "import typer\nfrom rich.console import Console\nprint('ok')\n"),
        ("cli/app.py", "from comicload.export.csv import CsvSink\n"),
        ("__init__.py", ""),
        ("config.py", ""),
    ],
)
def test_legitimate_modules_are_accepted(relative, source):
    assert check_source(relative, source) == []
