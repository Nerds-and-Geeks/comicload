from __future__ import annotations

from collections.abc import Sequence

from comicload.core.models import Bucket, CatalogEntry, IdentifyResult, ImportResult
from comicload.core.ports import Sink


class ExportService:
    """Pushes confident results to a sink. Ambiguous and unrecognized never leave the queue."""

    def __init__(self, sink: Sink) -> None:
        self._sink = sink

    def export(self, results: Sequence[IdentifyResult]) -> ImportResult:
        entries: list[CatalogEntry] = [
            result.entry
            for result in results
            if result.bucket is Bucket.CONFIDENT and result.entry is not None
        ]
        return self._sink.push(entries)
