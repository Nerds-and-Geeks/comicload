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
    layer_dir = SRC / layer
    if not layer_dir.is_dir():
        return []
    return sorted(layer_dir.rglob("*.py"))


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
                    f"{path.relative_to(SRC)} imports {name}; presentation belongs in adapters/"
                )
            source = path.read_text()
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    assert node.func.id != "print", (
                        f"{path.relative_to(SRC)} calls print(); use the ProgressReporter port"
                    )
