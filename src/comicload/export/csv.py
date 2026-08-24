from __future__ import annotations

import csv
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from comicload.models import CatalogEntry, ImportResult
from comicload.signals.registry import register_sink

# Exact column order from a real League of Comic Geeks export.
COLUMNS = [
    "Publisher Name",
    "Series Name",
    "Full Title",
    "Release Date",
    "In Collection",
    "In Wish List",
    "Marked Read",
    "My Rating",
    "Media Format",
    "Price Paid",
    "Date Purchased",
    "Condition",
    "Notes",
    "Tags",
]

REQUIRED = ["Publisher Name", "Series Name", "Full Title"]


def _row(entry: CatalogEntry) -> dict[str, str]:
    return {
        "Publisher Name": entry.publisher_name,
        "Series Name": entry.series_name,
        "Full Title": entry.full_title,
        "Release Date": entry.release_date.isoformat() if entry.release_date else "",
        "In Collection": "1" if entry.in_collection else "0",
        "In Wish List": "1" if entry.in_wish_list else "0",
        "Marked Read": "1" if entry.marked_read else "0",
        "My Rating": entry.my_rating,
        "Media Format": entry.media_format,
        "Price Paid": entry.price_paid,
        "Date Purchased": entry.date_purchased,
        "Condition": entry.condition,
        "Notes": entry.notes,
        "Tags": entry.tags,
    }


def _entry(row: dict[str, str]) -> CatalogEntry:
    raw_date = (row.get("Release Date") or "").strip()
    release_date: date | None = None
    if raw_date:
        try:
            release_date = date.fromisoformat(raw_date)
        except ValueError:
            release_date = None

    return CatalogEntry(
        publisher_name=row.get("Publisher Name", ""),
        series_name=row.get("Series Name", ""),
        full_title=row.get("Full Title", ""),
        release_date=release_date,
        in_collection=row.get("In Collection") == "1",
        in_wish_list=row.get("In Wish List") == "1",
        marked_read=row.get("Marked Read") == "1",
        my_rating=row.get("My Rating", ""),
        media_format=row.get("Media Format", ""),
        price_paid=row.get("Price Paid", ""),
        date_purchased=row.get("Date Purchased", ""),
        condition=row.get("Condition", ""),
        notes=row.get("Notes", ""),
        tags=row.get("Tags", ""),
    )


@register_sink("csv")
class CsvSink:
    """Writes the League of Comic Geeks bulk-import CSV."""

    name = "csv"

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def push(self, entries: Sequence[CatalogEntry]) -> ImportResult:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS)
            writer.writeheader()
            for entry in entries:
                writer.writerow(_row(entry))
        return ImportResult(
            total=len(entries),
            matched=len(entries),
            unmatched=0,
            destination=str(self._path),
        )


def read_csv(path: Path) -> list[CatalogEntry]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return [_entry(row) for row in csv.DictReader(handle)]


def validate_csv(path: Path) -> list[str]:
    """Return a list of human-readable problems. Empty means the file is importable."""
    problems: list[str] = []
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != COLUMNS:
            problems.append(
                "This file's header doesn't match the League of Comic Geeks export format "
                f"(expected {len(COLUMNS)} columns starting with 'Publisher Name')."
            )
            return problems
        for number, row in enumerate(reader, start=2):
            for column in REQUIRED:
                if not (row.get(column) or "").strip():
                    problems.append(f"Row {number} is missing '{column}', which is required.")
            raw_date = (row.get("Release Date") or "").strip()
            if raw_date:
                try:
                    date.fromisoformat(raw_date)
                except ValueError:
                    problems.append(
                        f"Row {number} has an unreadable 'Release Date' ({raw_date!r}); "
                        "use YYYY-MM-DD."
                    )
    return problems
