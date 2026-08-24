from datetime import date

from comicload.core.models import Bucket, Candidate, IdentifyResult, Issue
from comicload.services.confirm import ConfirmService, parse_query

ISSUE = Issue(
    gcd_id=7,
    publisher="DC",
    series="Superman",
    issue_number="35",
    on_sale_date=date(2026, 2, 25),
)


class StubResolver:
    def __init__(self, issues):
        self.issues = issues
        self.saw = None

    def resolve(self, candidate, scope):
        self.saw = candidate
        return list(self.issues)


class RecordingRepo:
    def __init__(self):
        self.saved = []

    def save(self, results):
        self.saved.extend(results)

    def pending_review(self):
        return []

    def confirmed_entries(self):
        return []


def test_parse_series_and_number():
    candidate = parse_query("Superman #35")
    assert candidate.series == "Superman"
    assert candidate.issue_number == "35"
    assert candidate.signal == "human"


def test_parse_tolerates_missing_hash_and_case():
    assert parse_query("superman 35").series == "superman"


def test_parse_keeps_multiword_series():
    candidate = parse_query("Alex + Ada #2")
    assert candidate.series == "Alex + Ada"
    assert candidate.issue_number == "2"


def test_parse_rejects_number_free_text():
    assert parse_query("no idea") is None


def test_lookup_routes_through_the_resolver():
    resolver = StubResolver([ISSUE])
    service = ConfirmService(resolver, RecordingRepo())
    assert service.lookup("Superman #35") == [ISSUE]
    assert resolver.saw.series == "Superman"


def test_lookup_of_unparseable_text_is_empty_not_an_error():
    assert ConfirmService(StubResolver([ISSUE]), RecordingRepo()).lookup("???") == []


def test_confirm_records_a_full_entry_and_releases_the_pixels():
    repo = RecordingRepo()
    service = ConfirmService(StubResolver([ISSUE]), repo)
    quarantined = IdentifyResult(
        "p1",
        "a.jpg",
        Bucket.UNRECOGNIZED,
        candidates=(Candidate(signal="barcode", confidence=0.3),),
        image=b"pixels",
    )

    confirmed = service.confirm(quarantined, ISSUE)

    assert confirmed.bucket is Bucket.CONFIDENT
    assert confirmed.entry.full_title == "Superman #35"
    assert confirmed.entry.release_date == date(2026, 2, 25)
    assert "signal=human" in confirmed.entry.tags
    assert confirmed.image is None
    assert repo.saved == [confirmed]
