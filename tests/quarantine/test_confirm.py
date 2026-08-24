from datetime import date

from comicload.models import Bucket, Candidate, IdentifyResult, Issue
from comicload.quarantine.service import ConfirmService, parse_query

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
    candidate, scope = parse_query("Superman #35")
    assert candidate.series == "Superman"
    assert candidate.issue_number == "35"
    assert candidate.signal == "human"
    assert scope.year_from is None


def test_parse_tolerates_missing_hash_and_case():
    candidate, _ = parse_query("superman 35")
    assert candidate.series == "superman"


def test_parse_keeps_multiword_series():
    candidate, _ = parse_query("Alex + Ada #2")
    assert candidate.series == "Alex + Ada"
    assert candidate.issue_number == "2"


def test_parse_trailing_year_narrows_the_scope():
    candidate, scope = parse_query("Superman #28 2025")
    assert candidate.issue_number == "28"
    assert scope.year_from == 2025
    assert scope.year_to == 2025


def test_parse_accepts_number_free_text_as_a_series_search():
    """'no idea' is a legitimate (if unlikely to match) series-only query now —
    only truly empty input is rejected. A bad guess should return zero results
    from lookup(), not be refused before it even reaches the catalogue."""
    candidate, _ = parse_query("no idea")
    assert candidate.series == "no idea"
    assert candidate.issue_number is None


def test_parse_rejects_empty_input():
    assert parse_query("") is None
    assert parse_query("   ") is None


def test_lookup_routes_through_the_resolver():
    resolver = StubResolver([ISSUE])
    service = ConfirmService(resolver, RecordingRepo())
    assert service.lookup("Superman #35") == [ISSUE]
    assert resolver.saw.series == "Superman"


def test_lookup_of_empty_text_is_empty_not_an_error():
    assert ConfirmService(StubResolver([ISSUE]), RecordingRepo()).lookup("   ") == []


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


def test_confirm_accepts_custom_signal_provenance():
    repo = RecordingRepo()
    service = ConfirmService(StubResolver([ISSUE]), repo)
    quarantined = IdentifyResult("p1", "a.jpg", Bucket.UNRECOGNIZED, image=b"pixels")

    confirmed = service.confirm(quarantined, ISSUE, signal="llm")

    assert confirmed.bucket is Bucket.CONFIDENT
    assert "signal=llm" in confirmed.entry.tags


def test_lookup_collapses_indistinguishable_variant_covers():
    """Found against real data: 'Superman #27' resolved to 25 GCD rows, one per
    variant cover printing — same publisher/series/issue/date on every one, distinct
    only by a barcode digit we don't display or track. LoCG can't match on the
    variant anyway (design decision from day one), so showing 25 identical-looking
    lines and asking a person to guess between them is worse than useless."""
    variants = [
        Issue(
            gcd_id=n,
            publisher="DC",
            series="Superman",
            issue_number="27",
            on_sale_date=date(2025, 6, 25),
            series_year=2023,
        )
        for n in range(100, 108)
    ]
    resolver = StubResolver(variants)
    service = ConfirmService(resolver, RecordingRepo())

    results = service.lookup("superman 27")

    assert len(results) == 1
    assert results[0].series == "Superman"


def test_lookup_keeps_genuinely_different_issues_separate():
    """Same series+issue text can legitimately mean different real comics — a
    reprint with a different on-sale date, or (rarer) two different publishers
    using the same series name. Only rows identical on every LoCG-relevant field
    collapse; anything that actually differs must still be shown."""
    same_run = Issue(
        gcd_id=1,
        publisher="DC",
        series="Superman",
        issue_number="27",
        on_sale_date=date(2025, 6, 25),
        series_year=2023,
    )
    reprint = Issue(
        gcd_id=2,
        publisher="DC",
        series="Superman",
        issue_number="27",
        on_sale_date=date(2025, 7, 30),
        series_year=2023,
    )
    resolver = StubResolver([same_run, same_run, reprint])
    service = ConfirmService(resolver, RecordingRepo())

    results = service.lookup("superman 27")

    assert len(results) == 2
    assert {r.on_sale_date for r in results} == {date(2025, 6, 25), date(2025, 7, 30)}


def test_lookup_preserves_order_of_first_occurrence():
    older = Issue(
        gcd_id=1,
        publisher="DC",
        series="Superman",
        issue_number="27",
        on_sale_date=date(2025, 6, 25),
    )
    newer = Issue(
        gcd_id=2,
        publisher="DC",
        series="Superman",
        issue_number="27",
        on_sale_date=date(2025, 7, 30),
    )
    resolver = StubResolver([older, older, newer])
    service = ConfirmService(resolver, RecordingRepo())

    results = service.lookup("superman 27")

    assert [r.gcd_id for r in results] == [1, 2]


def test_parse_accepts_a_series_alone_no_issue_number():
    """Collected editions carry issue_number '[nn]' in GCD, not a real number.
    Typing 'Superman Brainiac' must not require a trailing digit that doesn't exist."""
    candidate, scope = parse_query("Superman Brainiac")
    assert candidate.series == "Superman Brainiac"
    assert candidate.issue_number is None
    assert scope.year_from is None


def test_parse_still_prefers_a_trailing_number_when_present():
    candidate, _ = parse_query("Superman #35")
    assert candidate.series == "Superman"
    assert candidate.issue_number == "35"


def test_lookup_finds_a_collected_edition_by_title_alone():
    edition = Issue(
        gcd_id=99,
        publisher="DC",
        series="Superman: Brainiac",
        issue_number="[nn]",
        on_sale_date=date(2023, 11, 7),
    )
    resolver = StubResolver([edition])
    service = ConfirmService(resolver, RecordingRepo())

    results = service.lookup("Superman Brainiac")

    assert results == [edition]
    assert resolver.saw.issue_number is None
