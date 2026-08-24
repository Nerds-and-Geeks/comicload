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
