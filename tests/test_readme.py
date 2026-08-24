from pathlib import Path

README = Path(__file__).resolve().parent.parent / "README.md"


def test_readme_exists():
    assert README.exists()


def test_readme_documents_every_user_facing_command():
    text = README.read_text()
    for command in (
        "comicload catalog sync",
        "comicload scan",
        "comicload import",
        "comicload review",
        "comicload config",
    ):
        assert command in text, f"README does not explain '{command}'"


def test_readme_mentions_the_zbar_prerequisite():
    assert "zbar" in README.read_text().lower()


def test_readme_avoids_unexplained_jargon():
    """Words that mean nothing to a comic collector must not appear in the README."""
    banned = ["hexagonal", "dependency injection", "protocol", "adapter pattern", "ANN index"]
    text = README.read_text().lower()
    for word in banned:
        assert word.lower() not in text, f"README uses jargon: {word}"
