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
    report = _rejects("services/export.py", "from ..adapters.cli.render import console\n")
    assert "comicload.adapters.cli.render" in report


def test_deeply_relative_import_across_layers_is_rejected():
    report = _rejects("infra/storage/catalogue.py", "from ...adapters.cli.render import console\n")
    assert "comicload.adapters.cli.render" in report


def test_dynamic_import_of_another_layer_is_rejected():
    report = _rejects(
        "services/export.py",
        "import importlib\nrender = importlib.import_module('comicload.adapters.cli.render')\n",
    )
    assert "import_module" in report


def test_third_party_import_in_core_is_rejected():
    report = _rejects("core/models.py", "import pydantic\n")
    assert "standard library" in report


def test_stdout_write_outside_adapters_is_rejected():
    report = _rejects("services/export.py", "import sys\nsys.stdout.write('hello')\n")
    assert "standard stream" in report


def test_stderr_write_outside_adapters_is_rejected():
    report = _rejects("infra/photos.py", "import sys\nsys.stderr.writelines(['x'])\n")
    assert "standard stream" in report


def test_imported_stream_write_is_rejected():
    report = _rejects("infra/photos.py", "from sys import stdout\nstdout.write('x')\n")
    assert "standard stream" in report


def test_aliased_print_is_rejected():
    report = _rejects("services/export.py", "from builtins import print as _echo\n_echo('hello')\n")
    assert "_echo()" in report


def test_rebound_print_is_rejected():
    report = _rejects("infra/photos.py", "_say = print\n_say('hello')\n")
    assert "_say()" in report


def test_builtins_print_attribute_is_rejected():
    report = _rejects("services/export.py", "import builtins\nbuiltins.print('hello')\n")
    assert "builtins.print()" in report


def test_a_module_outside_every_layer_is_rejected():
    report = _rejects("helpers.py", "import typer\nprint('hi')\n")
    assert "not inside a known layer" in report


# --- and the original checks still hold ------------------------------------------


def test_plain_print_outside_adapters_is_rejected():
    assert "print()" in _rejects("core/models.py", "print('hello')\n")


def test_rich_import_outside_adapters_is_rejected():
    assert "adapters" in _rejects("infra/photos.py", "from rich.console import Console\n")


def test_services_may_not_import_infra():
    report = _rejects("services/export.py", "from comicload.infra.photos import x\n")
    assert "may not depend on" in report


def test_infra_may_not_import_services():
    report = _rejects("infra/photos.py", "from comicload.services.export import x\n")
    assert "may not depend on" in report


# --- what must stay allowed ------------------------------------------------------


@pytest.mark.parametrize(
    ("relative", "source"),
    [
        ("core/models.py", "from dataclasses import dataclass\nimport sqlite3\n"),
        ("services/export.py", "from comicload.core.models import Bucket\n"),
        ("services/export.py", "from .identify import IdentifyService\n"),
        ("infra/photos.py", "import pydantic\nfrom comicload.core.models import Photo\n"),
        ("infra/storage/catalogue.py", "from ..config import load_config\n"),
        ("adapters/cli/app.py", "import typer\nfrom rich.console import Console\nprint('ok')\n"),
        ("adapters/cli/app.py", "from comicload.services.export import ExportService\n"),
        ("__init__.py", ""),
    ],
)
def test_legitimate_modules_are_accepted(relative, source):
    assert check_source(relative, source) == []
