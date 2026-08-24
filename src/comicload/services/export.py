from __future__ import annotations

from collections.abc import Sequence

from comicload.core.models import Bucket, CatalogEntry, IdentifyResult, ImportResult
from comicload.core.ports import Sink


class ExportService:
    """Pushes confident entries to a sink. Ambiguous and unrecognized never leave the queue."""

    def __init__(self, sink: Sink) -> None:
        self._sink = sink

    def export_entries(self, entries: Sequence[CatalogEntry]) -> ImportResult:
        """Push entries that are already known to be confident — the catalogue's own view.

        The CLI exports everything identified so far rather than only this run, because a
        sink like CsvSink writes a whole file: exporting one run's results would silently
        replace the previous box of comics with this one.
        """
        return self._sink.push(entries)

    def export(self, results: Sequence[IdentifyResult]) -> ImportResult:
        """Push just the confident results of a single run."""
        entries: list[CatalogEntry] = [
            result.entry
            for result in results
            if result.bucket is Bucket.CONFIDENT and result.entry is not None
        ]
        return self.export_entries(entries)
