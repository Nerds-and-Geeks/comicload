import csv
from datetime import date
from pathlib import Path

from comicload.core.models import CatalogEntry
from comicload.core.registry import available_sinks
from comicload.infra.sinks.csv_sink import COLUMNS, CsvSink, read_csv, validate_csv

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "locg_export_header.csv"

ENTRY = CatalogEntry(
    publisher_name="Image Comics",
    series_name="Alex + Ada",
    full_title="Alex + Ada #2 2nd Printing",
    release_date=date(2013, 12, 11),
)


def test_columns_match_the_real_locg_export_header():
    expected = FIXTURE.read_text().strip().split(",")
    assert COLUMNS == expected  # noqa: SIM300 - COLUMNS is the subject under test, leads the assert


def test_written_header_matches_fixture_exactly(tmp_path):
    out = tmp_path / "out.csv"
    CsvSink(out).push([ENTRY])
    assert out.read_text().splitlines()[0] == FIXTURE.read_text().strip()


def test_row_renders_booleans_as_zero_and_one(tmp_path):
    out = tmp_path / "out.csv"
    CsvSink(out).push([ENTRY])

    row = list(csv.DictReader(out.open()))[0]
    assert row["In Collection"] == "1"
    assert row["In Wish List"] == "0"
    assert row["Marked Read"] == "0"
    assert row["Release Date"] == "2013-12-11"


def test_missing_release_date_is_blank_not_none(tmp_path):
    out = tmp_path / "out.csv"
    CsvSink(out).push([CatalogEntry("Marvel", "The Punisher", "The Punisher #12")])
    assert list(csv.DictReader(out.open()))[0]["Release Date"] == ""


def test_push_returns_result_with_destination(tmp_path):
    out = tmp_path / "out.csv"
    result = CsvSink(out).push([ENTRY])
    assert result.total == 1
    assert result.matched == 1
    assert result.unmatched == 0
    assert result.destination == str(out)
    assert result.view_url is None


def test_roundtrip_read_csv(tmp_path):
    out = tmp_path / "out.csv"
    CsvSink(out).push([ENTRY])
    assert read_csv(out) == [ENTRY]


def test_read_csv_handles_malformed_release_dates_resiliently(tmp_path):
    out = tmp_path / "malformed.csv"
    out.write_text(
        ",".join(COLUMNS) + "\nMarvel,The Punisher,The Punisher #12,NOT_A_DATE,1,0,0,,,,,,,\n"
    )
    entries = read_csv(out)
    assert len(entries) == 1
    assert entries[0].release_date is None


def test_validate_accepts_good_file(tmp_path):
    out = tmp_path / "out.csv"
    CsvSink(out).push([ENTRY])
    assert validate_csv(out) == []


def test_validate_reports_wrong_header(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("Wrong,Header\na,b\n")
    problems = validate_csv(bad)
    assert any("header" in p.lower() for p in problems)


def test_validate_reports_missing_required_field(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text(",".join(COLUMNS) + "\n" + ",".join([""] * len(COLUMNS)) + "\n")
    problems = validate_csv(bad)
    assert any("Full Title" in p for p in problems)


def test_sink_is_registered():

    assert "csv" in available_sinks()
