from pathlib import Path

from typer.testing import CliRunner

from comicload.adapters.cli.app import app
from comicload.infra.sinks.csv_sink import COLUMNS
from comicload.infra.storage.catalogue import SqliteRepository

runner = CliRunner()
FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "gcd_sample.sql"

GOOD_ROW = "Marvel,The Punisher,The Punisher #12,2001-03-01,1,0,0,,,,,,,\n"


def _good_csv(path: Path) -> Path:
    path.write_text(",".join(COLUMNS) + "\n" + GOOD_ROW)
    return path


def test_help_lists_the_main_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("scan", "import", "review", "catalog", "config"):
        assert command in result.stdout


def test_catalog_sync_loads_dump(tmp_path):
    db = tmp_path / "gcd.sqlite"
    result = runner.invoke(app, ["catalog", "sync", str(FIXTURE), "--db", str(db)])
    assert result.exit_code == 0
    assert db.exists()
    assert "issue" in result.stdout


def test_scan_on_empty_folder_writes_header_only_csv(tmp_path):
    photos = tmp_path / "photos"
    photos.mkdir()
    out = tmp_path / "out.csv"
    db = tmp_path / "gcd.sqlite"
    runner.invoke(app, ["catalog", "sync", str(FIXTURE), "--db", str(db)])

    result = runner.invoke(app, ["scan", str(photos), "--out", str(out), "--db", str(db)])

    assert result.exit_code == 0
    assert out.read_text().strip() == ",".join(COLUMNS)


def test_scan_reports_missing_folder_clearly(tmp_path):
    result = runner.invoke(app, ["scan", str(tmp_path / "nope"), "--out", str(tmp_path / "o.csv")])
    assert result.exit_code != 0
    assert "does not exist" in result.stdout


def test_import_validates_a_good_file(tmp_path):
    good = _good_csv(tmp_path / "good.csv")
    result = runner.invoke(app, ["import", str(good)])
    assert result.exit_code == 0
    assert "1" in result.stdout


def test_import_reports_validation_problems(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("Nope,Wrong\n1,2\n")
    result = runner.invoke(app, ["import", str(bad)])
    assert result.exit_code != 0
    assert "header" in result.stdout.lower()


def test_import_locg_flag_is_rejected_without_the_extra(tmp_path):
    good = _good_csv(tmp_path / "good.csv")
    result = runner.invoke(app, ["import", str(good), "--import-locg"])
    assert result.exit_code != 0
    assert "comicload[locg]" in result.stdout


def test_config_show_prints_current_settings(tmp_path):
    result = runner.invoke(app, ["config", "show", "--path", str(tmp_path / "c.toml")])
    assert result.exit_code == 0
    assert "csv" in result.stdout


# --- beyond the brief: the review queue now has a database behind it (Task 14) ---


def _scan_one_unreadable_photo(tmp_path):
    """Scan a folder holding one photo no signal can read, and return the paths used."""
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "a.jpg").write_bytes(b"not really a jpeg")
    gcd_db = tmp_path / "gcd.sqlite"
    catalogue_db = tmp_path / "comicload.sqlite"
    runner.invoke(app, ["catalog", "sync", str(FIXTURE), "--db", str(gcd_db)])

    result = runner.invoke(
        app,
        [
            "scan",
            str(photos),
            "--out",
            str(tmp_path / "out.csv"),
            "--db",
            str(gcd_db),
            "--catalogue-db",
            str(catalogue_db),
        ],
    )
    return result, catalogue_db


def test_scan_persists_results_to_the_catalogue(tmp_path):
    result, catalogue_db = _scan_one_unreadable_photo(tmp_path)

    assert result.exit_code == 0
    assert catalogue_db.exists()
    pending = SqliteRepository(catalogue_db).pending_review()
    assert [r.filename for r in pending] == ["a.jpg"]


def test_review_shows_what_scan_persisted(tmp_path):
    _, catalogue_db = _scan_one_unreadable_photo(tmp_path)

    result = runner.invoke(app, ["review", "--db", str(catalogue_db)])

    assert result.exit_code == 0
    assert "a.jpg" in result.stdout
    assert "unrecognized" in result.stdout


def test_review_is_friendly_when_there_is_nothing_to_review(tmp_path):
    result = runner.invoke(app, ["review", "--db", str(tmp_path / "empty.sqlite")])

    assert result.exit_code == 0
    assert "nothing to review" in result.stdout.lower()
