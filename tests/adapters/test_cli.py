from pathlib import Path

from typer.testing import CliRunner

import comicload.adapters.cli.app as app_module
from comicload.adapters.cli.app import app
from comicload.core.errors import ComicloadError
from comicload.core.models import Candidate
from comicload.infra.config import load_config
from comicload.infra.sinks.csv_sink import COLUMNS, read_csv
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

    result = runner.invoke(
        app,
        [
            "scan",
            str(photos),
            "--out",
            str(out),
            "--db",
            str(db),
            "--catalogue-db",
            str(tmp_path / "comicload.sqlite"),
        ],
    )

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


class _BlindSignal:
    """A signal that reads nothing — an unreadable photo, without needing a real decoder."""

    name = "barcode"

    def identify(self, photo, scope):
        return []


def _scan_one_unreadable_photo(tmp_path, monkeypatch):
    """Scan a folder holding one photo no signal can read, and return the paths used."""
    monkeypatch.setattr(app_module, "get_signal", lambda name, **kw: _BlindSignal())
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
    return result, gcd_db, catalogue_db


def test_scan_persists_results_to_the_catalogue(tmp_path, monkeypatch):
    result, _, catalogue_db = _scan_one_unreadable_photo(tmp_path, monkeypatch)

    assert result.exit_code == 0
    assert catalogue_db.exists()
    pending = SqliteRepository(catalogue_db).pending_review()
    assert [r.filename for r in pending] == ["a.jpg"]


def test_review_walks_the_quarantine_and_quits_cleanly(tmp_path, monkeypatch):
    _, gcd_db, catalogue_db = _scan_one_unreadable_photo(tmp_path, monkeypatch)

    result = runner.invoke(
        app,
        ["review", "--db", str(gcd_db), "--catalogue-db", str(catalogue_db), "--no-images"],
        input="q\n",
    )

    assert result.exit_code == 0, result.output
    assert "a.jpg" in result.output
    assert "quarantine" in result.output.lower()


def test_review_identifies_a_comic_from_typed_series_and_number(tmp_path, monkeypatch):
    _, gcd_db, catalogue_db = _scan_one_unreadable_photo(tmp_path, monkeypatch)

    result = runner.invoke(
        app,
        ["review", "--db", str(gcd_db), "--catalogue-db", str(catalogue_db), "--no-images"],
        input="The Punisher #12\n1\n",
    )

    assert result.exit_code == 0, result.output
    assert "The Punisher #12" in result.output
    assert "saved" in result.output.lower()
    assert SqliteRepository(catalogue_db).pending_review() == []
    assert len(SqliteRepository(catalogue_db).confirmed_entries()) == 1


def test_review_is_friendly_when_there_is_nothing_to_review(tmp_path):
    empty = tmp_path / "empty.sqlite"
    SqliteRepository(empty).save([])

    result = runner.invoke(app, ["review", "--catalogue-db", str(empty)])

    assert result.exit_code == 0
    assert "nothing in quarantine" in result.output.lower()


def test_review_on_a_database_that_does_not_exist_says_so(tmp_path):
    """A typo used to create the file and claim everything was identified."""
    typo = tmp_path / "typo.sqlite"

    result = runner.invoke(app, ["review", "--catalogue-db", str(typo)])

    assert result.exit_code != 0
    assert "quarantine" not in result.output.lower() or "comicload scan" in result.output
    assert "comicload scan" in result.output
    assert not typo.exists()


# --- a broken signal must not masquerade as "not recognised" ------------------


class _BrokenSignal:
    name = "barcode"

    def identify(self, photo, scope):
        raise RuntimeError("signal is broken")


def test_scan_says_so_when_a_signal_failed_on_every_photo(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "get_signal", lambda name, **kw: _BrokenSignal())
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "a.jpg").write_bytes(b"not really a jpeg")
    (photos / "b.jpg").write_bytes(b"nor is this one")
    gcd_db = tmp_path / "gcd.sqlite"
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
            str(tmp_path / "comicload.sqlite"),
        ],
    )

    assert result.exit_code == 0
    assert "failed on all 2 photo(s)" in result.stdout


def test_scan_reports_a_missing_native_library_instead_of_a_clean_tally(tmp_path, monkeypatch):
    """No zbar means no photo was examined; the run must say so, not report 0 recognised."""

    class _NoZbar:
        name = "barcode"

        def identify(self, photo, scope):
            raise ComicloadError("comicload cannot read barcodes ... brew install zbar")

    monkeypatch.setattr(app_module, "get_signal", lambda name, **kw: _NoZbar())
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "a.jpg").write_bytes(b"not really a jpeg")
    gcd_db = tmp_path / "gcd.sqlite"
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
            str(tmp_path / "comicload.sqlite"),
        ],
    )

    assert result.exit_code != 0
    assert "brew install zbar" in result.stdout


# --- the CSV must not lose the last box of comics ----------------------------


class _CataloguedSignal:
    """Recognises the two issues in the sample dump, one per filename."""

    name = "barcode"
    BARCODES = {
        "punisher.jpg": "75960608457000111",
        "alexada.jpg": "70985301491000211",
    }

    def identify(self, photo, scope):
        barcode = self.BARCODES.get(photo.filename)
        if barcode is None:
            return []
        return [Candidate(signal="barcode", confidence=0.95, barcode=barcode)]


def test_scanning_a_second_folder_keeps_the_first_folder_in_the_csv(tmp_path, monkeypatch):
    """CsvSink opens 'w': a second scan used to overwrite the first box of comics."""
    monkeypatch.setattr(app_module, "get_signal", lambda name, **kw: _CataloguedSignal())
    gcd_db = tmp_path / "gcd.sqlite"
    catalogue_db = tmp_path / "comicload.sqlite"
    out = tmp_path / "collection.csv"
    runner.invoke(app, ["catalog", "sync", str(FIXTURE), "--db", str(gcd_db)])

    for folder_name, photo_name in (("box_a", "punisher.jpg"), ("box_b", "alexada.jpg")):
        folder = tmp_path / folder_name
        folder.mkdir()
        (folder / photo_name).write_bytes(photo_name.encode())
        result = runner.invoke(
            app,
            [
                "scan",
                str(folder),
                "--out",
                str(out),
                "--db",
                str(gcd_db),
                "--catalogue-db",
                str(catalogue_db),
            ],
        )
        assert result.exit_code == 0, result.stdout

    titles = {entry.full_title for entry in read_csv(out)}
    assert titles == {"The Punisher #12", "Alex + Ada #2"}


def test_an_unknown_signal_name_is_a_message_not_a_traceback(tmp_path, monkeypatch):
    config = load_config(tmp_path / "missing.toml")
    config.signals.enabled = ["telepathy"]
    monkeypatch.setattr(app_module, "load_config", lambda *args, **kwargs: config)
    photos = tmp_path / "photos"
    photos.mkdir()

    result = runner.invoke(app, ["scan", str(photos), "--out", str(tmp_path / "out.csv")])

    assert result.exit_code != 0
    assert "telepathy" in result.stdout
    assert "signals.enabled" in result.stdout


def test_scan_wires_a_real_decoder_into_the_barcode_signal(tmp_path, monkeypatch):
    """A bare get_signal("barcode") leaves decoder=None: every test passes on stubs
    while every production scan fails on every photo. The CLI must inject the decoder."""
    seen = {}

    def fake_decoder_factory():
        def decoder(image_bytes):
            seen["called"] = True
            return []

        return decoder

    monkeypatch.setattr(app_module, "get_default_barcode_decoder", fake_decoder_factory)

    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "a.jpg").write_bytes(b"fake image bytes")
    db = tmp_path / "gcd.sqlite"
    runner.invoke(app, ["catalog", "sync", str(FIXTURE), "--db", str(db)])

    result = runner.invoke(
        app, ["scan", str(photos), "--out", str(tmp_path / "o.csv"), "--db", str(db)]
    )

    assert result.exit_code == 0, result.output
    assert seen.get("called"), "scan ran without the wired decoder ever being used"
