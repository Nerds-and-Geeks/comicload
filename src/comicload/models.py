from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class Photo:
    """One source image. `data` is the raw bytes so the domain never touches a filesystem."""

    id: str
    data: bytes
    filename: str


@dataclass(frozen=True, slots=True)
class Scope:
    """User-declared narrowing hint for a batch."""

    publisher: str | None = None
    year_from: int | None = None
    year_to: int | None = None

    def includes_year(self, year: int | None) -> bool:
        if year is None:
            return True
        if self.year_from is not None and year < self.year_from:
            return False
        return not (self.year_to is not None and year > self.year_to)


@dataclass(frozen=True, slots=True)
class Candidate:
    """A guess produced by one signal. Fields are optional because signals see different things."""

    signal: str
    confidence: float
    publisher: str | None = None
    series: str | None = None
    issue_number: str | None = None
    printing: str | None = None
    year: int | None = None
    barcode: str | None = None
    evidence: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Issue:
    """A resolved issue from the metadata catalogue."""

    gcd_id: int
    publisher: str
    series: str
    issue_number: str
    on_sale_date: date | None = None
    printing: str | None = None
    series_year: int | None = None
    """Year the series began — how collectors tell Superman (1939) from Superman (2023)."""
    variant: str | None = None

    @property
    def gcd_url(self) -> str:
        return f"https://www.comics.org/issue/{self.gcd_id}/"

    def to_catalog_entry(self) -> CatalogEntry:
        # Clean issue number: strip dual legacy parens like "20 (863)" -> "20"
        num = self.issue_number.split("(")[0].strip() if self.issue_number else ""

        # Format full_title for League of Comic Geeks CSV importer:
        # 1. Unnumbered trades/graphic novels ([nn]) omit issue number
        # 2. Omit "1st Printing" printing suffixes from full_title (breaks LoCG matchers)
        title = self.series if not num or num == "[nn]" else f"{self.series} #{num}"

        return CatalogEntry(
            publisher_name=self.publisher,
            series_name=self.series,
            full_title=title,
            release_date=self.on_sale_date,
        )


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """One row of the League of Comic Geeks import format. Field order matches their export."""

    publisher_name: str
    series_name: str
    full_title: str
    release_date: date | None = None
    in_collection: bool = True
    in_wish_list: bool = False
    marked_read: bool = False
    my_rating: str = ""
    media_format: str = ""
    price_paid: str = ""
    date_purchased: str = ""
    condition: str = ""
    notes: str = ""
    tags: str = ""


class Bucket(StrEnum):
    CONFIDENT = "confident"
    AMBIGUOUS = "ambiguous"
    UNRECOGNIZED = "unrecognized"


@dataclass(frozen=True, slots=True)
class IdentifyResult:
    """Outcome for a single photo. Every photo produces exactly one of these."""

    photo_id: str
    filename: str
    bucket: Bucket
    entry: CatalogEntry | None = None
    candidates: tuple[Candidate, ...] = ()
    signal_failures: tuple[str, ...] = ()
    """Signals that crashed on this photo. A run must not stop, but it must not lie either:
    a signal that failed on every photo is a broken install, not a shelf of odd comics."""

    image: bytes | None = None
    """The cover pixels, kept only while the photo is quarantined. Review sessions run
    days later, long after the source folder may be gone — the catalogue must be able
    to show the cover it is asking about. Cleared once the comic is identified."""


@dataclass(frozen=True, slots=True)
class ImportResult:
    """Outcome of pushing entries to a sink. `view_url` is set by sinks that have one."""

    total: int
    matched: int
    unmatched: int
    destination: str
    view_url: str | None = None
