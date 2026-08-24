"""Enforces the hexagonal boundaries. These tests are the reason a web adapter stays cheap.

The checker below is deliberately written as a pure function over (path, source) so it can
be tested against synthetic violations — see `test_architecture_checker.py`. A layering
test that has never been shown to reject anything is decoration, not enforcement.
"""

import ast
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "comicload"

LAYERS = (
    "domain",
    "catalog",
    "signals",
    "ingestion",
    "identification",
    "quarantine",
    "export",
    "cli",
)

# Files that live directly in src/comicload/ rather than in a domain module.
ROOT_FILES = {"__init__.py", "config.py"}

# Which comicload packages each domain module may import.
ALLOWED_INTERNAL = {
    "domain": ("comicload.domain",),
    "catalog": ("comicload.domain", "comicload.catalog", "comicload.config"),
    "signals": ("comicload.domain", "comicload.signals"),
    "ingestion": ("comicload.domain", "comicload.ingestion"),
    "identification": (
        "comicload.domain",
        "comicload.signals",
        "comicload.catalog",
        "comicload.identification",
    ),
    "quarantine": (
        "comicload.domain",
        "comicload.catalog",
        "comicload.quarantine",
        "comicload.signals",
        "comicload.config",
    ),
    "export": ("comicload.domain", "comicload.export", "comicload.signals"),
    "cli": ("comicload",),
}

# Layers that may not reach for anything outside the standard library.
STDLIB_ONLY = ("domain",)

# Layers that may not import comicload's own presentation libraries, print, or write to
# a stream directly. Presentation belongs to cli.
QUIET_LAYERS = (
    "domain",
    "catalog",
    "signals",
    "ingestion",
    "identification",
    "quarantine",
    "export",
)

PRESENTATION_PACKAGES = ("rich", "typer")

_STREAMS = ("stdout", "stderr")
_WRITE_METHODS = ("write", "writelines")


def _layer_of(relative: str) -> str | None:
    """The layer a path belongs to, or None when it belongs to none."""
    parts = relative.split("/")
    if len(parts) == 1:
        return "" if relative in ROOT_FILES else None
    return parts[0] if parts[0] in LAYERS else None


def _package_of(relative: str) -> list[str]:
    """The dotted package a module sits in, as parts. Same for a module and its package."""
    return ["comicload", *relative.split("/")[:-1]]


def _resolve_relative(relative: str, level: int, module: str | None) -> str:
    """What `from ..x import y` actually names, so relative imports cannot slip past."""
    parts = _package_of(relative)
    truncate = level - 1
    base = parts[: len(parts) - truncate] if truncate < len(parts) else ["comicload"]
    if module:
        return ".".join([*base, module])
    return ".".join(base)


def _print_aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    """Names bound to the builtin print, and names bound to the builtins module."""
    printers = {"print"}
    builtins_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "builtins":
            for alias in node.names:
                if alias.name == "print":
                    printers.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "builtins":
                    builtins_modules.add(alias.asname or "builtins")
        elif (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Name)
            and node.value.id in printers
        ):
            printers.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return printers, builtins_modules


def _is_stream_write(node: ast.Call) -> bool:
    """sys.stdout.write(...), sys.stderr.writelines(...), or the `from sys import` form."""
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr not in _WRITE_METHODS:
        return False
    target = func.value
    if isinstance(target, ast.Attribute) and target.attr in _STREAMS:
        return True
    return isinstance(target, ast.Name) and target.id in _STREAMS


def _dynamic_import_target(node: ast.Call) -> str | None:
    """The module name in `importlib.import_module("...")`, if that is what this call is."""
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    if name != "import_module" or not node.args:
        return None
    first = node.args[0]
    return first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else None


def _check_import(relative: str, layer: str, imported: str) -> list[str]:
    problems: list[str] = []
    top = imported.split(".")[0]

    if imported == "comicload" or imported.startswith("comicload."):
        if imported.startswith(f"comicload.{layer}") or imported == f"comicload.{layer}":
            return problems
        allowed = ALLOWED_INTERNAL[layer]
        if not any(imported == prefix or imported.startswith(f"{prefix}.") for prefix in allowed):
            problems.append(
                f"{relative} imports {imported}, which layer '{layer}' may not depend on"
            )
        return problems

    if layer in QUIET_LAYERS and top in PRESENTATION_PACKAGES:
        problems.append(f"{relative} imports {top}; presentation belongs in cli/")
        return problems
        return problems

    if layer in STDLIB_ONLY and top not in sys.stdlib_module_names:
        problems.append(
            f"{relative} imports {imported}, which is not in the standard library; "
            f"layer '{layer}' must stay dependency-free"
        )
    return problems


def check_source(relative: str, source: str) -> list[str]:
    """Every layering violation in one module, as messages. Empty means the module is clean.

    `relative` is the path below src/comicload, posix-style, e.g. 'services/identify.py'.
    """
    layer = _layer_of(relative)
    if layer is None:
        return [
            f"{relative} is not inside a known layer ({', '.join(LAYERS)}); every module "
            "must sit in a layer, or the layering rules simply do not apply to it"
        ]
    if layer == "":  # src/comicload/__init__.py — the package root, no rules to apply
        return []

    tree = ast.parse(source)
    problems: list[str] = []
    printers, builtins_modules = _print_aliases(tree)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                problems += _check_import(relative, layer, alias.name)
        elif isinstance(node, ast.ImportFrom):
            imported = (
                _resolve_relative(relative, node.level, node.module)
                if node.level
                else (node.module or "")
            )
            if imported:
                problems += _check_import(relative, layer, imported)
        elif isinstance(node, ast.Call):
            target = _dynamic_import_target(node)
            if target and target.split(".")[0] == "comicload" and layer in QUIET_LAYERS:
                problems.append(
                    f"{relative} imports {target} dynamically via import_module(); a "
                    "dependency the layering rules cannot see is still a dependency"
                )
            if layer not in QUIET_LAYERS:
                continue
            if isinstance(node.func, ast.Name) and node.func.id in printers:
                problems.append(f"{relative} calls {node.func.id}(); use the ProgressReporter port")
            elif (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "print"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in builtins_modules
            ):
                problems.append(f"{relative} calls builtins.print(); use the ProgressReporter port")
            elif _is_stream_write(node):
                problems.append(
                    f"{relative} writes to a standard stream directly; "
                    "use the ProgressReporter port"
                )

    return problems


def _source_files() -> list[tuple[str, str]]:
    """(relative path, source) for every module under src/comicload."""
    assert SRC.is_dir(), f"{SRC} is missing; the layering rules cannot be checked"
    files = sorted(SRC.rglob("*.py"))
    assert files, f"no modules found under {SRC}; the layering rules cannot be checked"
    return [(path.relative_to(SRC).as_posix(), path.read_text()) for path in files]


def test_every_layer_directory_exists():
    """A moved or renamed layer must fail loudly, not make these tests pass vacuously."""
    for layer in LAYERS:
        assert (SRC / layer).is_dir(), f"missing layer directory {layer}/ under {SRC}"
        assert list((SRC / layer).rglob("*.py")), f"layer {layer}/ holds no modules"


def test_every_module_belongs_to_a_layer():
    for relative, _ in _source_files():
        assert _layer_of(relative) is not None, (
            f"{relative} sits outside every layer, so no rule applies to it"
        )


def test_no_module_violates_the_layering():
    problems = [
        problem
        for relative, source in _source_files()
        for problem in check_source(relative, source)
    ]
    assert not problems, "\n".join(problems)
