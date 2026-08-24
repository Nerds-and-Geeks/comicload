from comicload.core.models import Bucket, CatalogEntry, IdentifyResult, ImportResult
from comicload.services.export import ExportService


class StubSink:
    name = "stub"

    def __init__(self):
        self.received = None

    def push(self, entries):
        self.received = list(entries)
        return ImportResult(
            total=len(entries),
            matched=len(entries),
            unmatched=0,
            destination="stub://",
            view_url="https://leagueofcomicgeeks.com/profile/me/collection",
        )


ENTRY = CatalogEntry("Marvel", "The Punisher", "The Punisher #12")


def test_only_confident_results_are_exported():
    sink = StubSink()
    results = [
        IdentifyResult("1", "a.jpg", Bucket.CONFIDENT, entry=ENTRY),
        IdentifyResult("2", "b.jpg", Bucket.AMBIGUOUS),
        IdentifyResult("3", "c.jpg", Bucket.UNRECOGNIZED),
    ]

    result = ExportService(sink).export(results)

    assert sink.received == [ENTRY]
    assert result.total == 1


def test_view_url_is_passed_through():
    result = ExportService(StubSink()).export(
        [IdentifyResult("1", "a.jpg", Bucket.CONFIDENT, entry=ENTRY)]
    )
    assert result.view_url == "https://leagueofcomicgeeks.com/profile/me/collection"


def test_exporting_nothing_still_returns_a_result():
    result = ExportService(StubSink()).export([IdentifyResult("1", "a.jpg", Bucket.UNRECOGNIZED)])
    assert result.total == 0
