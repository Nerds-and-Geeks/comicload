import pytest

from comicload.core.errors import ComicloadError
from comicload.core.models import Bucket, Candidate, Issue, Photo, Scope
from comicload.services.identify import IdentifyService


class StubSource:
    def __init__(self, photos):
        self._photos = photos

    def photos(self):
        return iter(self._photos)

    def count(self):
        return len(self._photos)


class StubSignal:
    def __init__(self, name, candidates):
        self.name = name
        self._candidates = candidates

    def identify(self, photo, scope):
        return list(self._candidates)


class StubResolver:
    def __init__(self, mapping):
        self._mapping = mapping

    def resolve(self, candidate, scope):
        return list(self._mapping.get(candidate.barcode, []))


class RecordingProgress:
    def __init__(self):
        self.started = None
        self.advanced = 0
        self.finished = False

    def start(self, total, label):
        self.started = (total, label)

    def advance(self, amount=1, message=None):
        self.advanced += amount

    def finish(self):
        self.finished = True


PHOTO = Photo(id="p1", data=b"x", filename="one.jpg")
ISSUE = Issue(gcd_id=1, publisher="Marvel", series="The Punisher", issue_number="12")


def test_single_high_confidence_match_is_confident():
    candidate = Candidate(signal="barcode", confidence=0.95, barcode="B1")
    service = IdentifyService(
        signals=[StubSignal("barcode", [candidate])],
        resolver=StubResolver({"B1": [ISSUE]}),
    )

    results = service.run(StubSource([PHOTO]), Scope())

    assert results[0].bucket is Bucket.CONFIDENT
    assert results[0].entry is not None
    assert results[0].entry.full_title == "The Punisher #12"


def test_multiple_matches_are_ambiguous():
    other = Issue(gcd_id=2, publisher="Marvel", series="The Punisher", issue_number="12")
    candidate = Candidate(signal="barcode", confidence=0.95, barcode="B1")
    service = IdentifyService(
        signals=[StubSignal("barcode", [candidate])],
        resolver=StubResolver({"B1": [ISSUE, other]}),
    )

    result = service.run(StubSource([PHOTO]), Scope())[0]
    assert result.bucket is Bucket.AMBIGUOUS
    assert result.entry is None


def test_low_confidence_match_is_ambiguous():
    candidate = Candidate(signal="barcode", confidence=0.4, barcode="B1")
    service = IdentifyService(
        signals=[StubSignal("barcode", [candidate])],
        resolver=StubResolver({"B1": [ISSUE]}),
    )
    assert service.run(StubSource([PHOTO]), Scope())[0].bucket is Bucket.AMBIGUOUS


def test_no_candidates_is_unrecognized():
    service = IdentifyService(signals=[StubSignal("barcode", [])], resolver=StubResolver({}))
    result = service.run(StubSource([PHOTO]), Scope())[0]
    assert result.bucket is Bucket.UNRECOGNIZED
    assert result.candidates == ()


def test_candidates_that_resolve_to_nothing_are_unrecognized():
    candidate = Candidate(signal="barcode", confidence=0.95, barcode="unknown")
    service = IdentifyService(
        signals=[StubSignal("barcode", [candidate])], resolver=StubResolver({})
    )
    assert service.run(StubSource([PHOTO]), Scope())[0].bucket is Bucket.UNRECOGNIZED


def test_failing_signal_does_not_stop_the_others():
    class Exploding:
        name = "boom"

        def identify(self, photo, scope):
            raise RuntimeError("signal crashed")

    good = Candidate(signal="barcode", confidence=0.95, barcode="B1")
    service = IdentifyService(
        signals=[Exploding(), StubSignal("barcode", [good])],
        resolver=StubResolver({"B1": [ISSUE]}),
    )
    assert service.run(StubSource([PHOTO]), Scope())[0].bucket is Bucket.CONFIDENT


def test_progress_is_reported_through_the_port():
    progress = RecordingProgress()
    service = IdentifyService(
        signals=[StubSignal("barcode", [])],
        resolver=StubResolver({}),
        progress=progress,
    )
    service.run(StubSource([PHOTO, PHOTO]), Scope())

    assert progress.started == (2, "Identifying")
    assert progress.advanced == 2
    assert progress.finished is True


def test_entry_tags_record_provenance():
    candidate = Candidate(signal="barcode", confidence=0.95, barcode="B1")
    service = IdentifyService(
        signals=[StubSignal("barcode", [candidate])],
        resolver=StubResolver({"B1": [ISSUE]}),
    )
    entry = service.run(StubSource([PHOTO]), Scope())[0].entry
    assert "barcode" in entry.tags
    assert "one.jpg" in entry.tags


# --- a swallowed signal failure must still be counted -------------------------


class Exploding:
    name = "boom"

    def identify(self, photo, scope):
        raise RuntimeError("signal crashed")


def test_swallowed_signal_failure_is_counted_on_the_result():
    service = IdentifyService(signals=[Exploding()], resolver=StubResolver({}))

    results = service.run(StubSource([PHOTO, Photo("p2", b"y", "two.jpg")]), Scope())

    assert [r.signal_failures for r in results] == [("boom",), ("boom",)]
    assert all(r.bucket is Bucket.UNRECOGNIZED for r in results)


def test_healthy_run_records_no_signal_failures():
    candidate = Candidate(signal="barcode", confidence=0.95, barcode="B1")
    service = IdentifyService(
        signals=[StubSignal("barcode", [candidate])],
        resolver=StubResolver({"B1": [ISSUE]}),
    )
    assert service.run(StubSource([PHOTO]), Scope())[0].signal_failures == ()


def test_a_deliberate_comicload_error_stops_the_run():
    """A missing library is true of every photo — it is not a per-photo failure."""

    class NoLibrary:
        name = "barcode"

        def identify(self, photo, scope):
            raise ComicloadError("zbar is not installed")

    service = IdentifyService(signals=[NoLibrary()], resolver=StubResolver({}))
    with pytest.raises(ComicloadError, match="zbar"):
        service.run(StubSource([PHOTO]), Scope())


# --- a barcode with no EAN-5 supplement must still be identifiable -------------


def test_an_exact_bare_upc_match_reaches_confident():
    """Comics without the 5-digit addon could never be auto-identified before."""
    from comicload.infra.signals.barcode import BarcodeSignal

    signal = BarcodeSignal(decoder=lambda data: [("759606084570", None)])
    service = IdentifyService(signals=[signal], resolver=StubResolver({"759606084570": [ISSUE]}))

    result = service.run(StubSource([PHOTO]), Scope())[0]

    assert result.bucket is Bucket.CONFIDENT
    assert result.entry is not None


def test_resolution_falls_back_from_the_concatenated_barcode_to_the_bare_upc():
    """The catalogue row has no supplement recorded; the scan must still match it."""
    from comicload.infra.signals.barcode import BarcodeSignal

    signal = BarcodeSignal(decoder=lambda data: [("759606084570", "00111")])
    service = IdentifyService(signals=[signal], resolver=StubResolver({"759606084570": [ISSUE]}))

    result = service.run(StubSource([PHOTO]), Scope())[0]

    assert result.bucket is Bucket.CONFIDENT
    assert result.entry is not None
    assert result.entry.notes == "1st Printing", "the supplement hint must survive the fallback"
