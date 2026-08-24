import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAKEFILE = ROOT / "Makefile"
CONTRIBUTING = ROOT / "CONTRIBUTING.md"

REQUIRED_TARGETS = [
    "setup",
    "install",
    "test",
    "lint",
    "typecheck",
    "check",
    "run",
    "clean",
]


def _targets() -> set[str]:
    pattern = re.compile(r"^([a-zA-Z][\w-]*):", re.MULTILINE)
    return set(pattern.findall(MAKEFILE.read_text()))


def test_makefile_exists():
    assert MAKEFILE.exists()


def test_every_required_target_is_defined():
    defined = _targets()
    missing = [t for t in REQUIRED_TARGETS if t not in defined]
    assert not missing, f"Makefile is missing targets: {missing}"


def test_all_targets_are_phony():
    """Every target is a command, not a file, so none may be skipped as up-to-date."""
    text = MAKEFILE.read_text()
    phony = re.search(r"^\.PHONY:\s*(.+)$", text, re.MULTILINE)
    assert phony, "Makefile must declare .PHONY"
    declared = set(phony.group(1).split())
    missing = [t for t in REQUIRED_TARGETS if t not in declared]
    assert not missing, f"targets not declared .PHONY: {missing}"


def test_make_help_is_the_default_and_lists_targets():
    result = subprocess.run(["make", "-C", str(ROOT)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    for target in REQUIRED_TARGETS:
        assert target in result.stdout, f"'make' output does not mention '{target}'"


def test_make_lint_passes_on_current_tree():
    result = subprocess.run(
        ["make", "-C", str(ROOT), "lint"], capture_output=True, text=True, timeout=180
    )
    assert result.returncode == 0, f"make lint failed:\n{result.stdout}\n{result.stderr}"


def test_contributing_documents_setup_and_test():
    assert CONTRIBUTING.exists()
    text = CONTRIBUTING.read_text()
    assert "make setup" in text
    assert "make test" in text
    assert "zbar" in text.lower()
