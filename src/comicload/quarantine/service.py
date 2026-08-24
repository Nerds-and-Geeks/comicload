"""Human identification of quarantined covers — the last resort after every signal.

Automated signals (barcode today; OCR, cover matching and LLM lookup when they land)
run first inside IdentifyService. Whatever they cannot settle waits here for a person.
The person types what they can see — "Superman #35" — and the catalogue does the rest,
so even human answers get real on-sale dates and exact titles from GCD.
"""

from __future__ import annotations

import dataclasses
import re

from comicload.models import Bucket, Candidate, IdentifyResult, Issue, Scope
from comicload.ports import IssueResolver, Repository

# "Superman #35", "superman 35 2014", "Alex + Ada #2" — series, issue, optional year
_WITH_NUMBER = re.compile(
    r"^\s*(?P<series>.+?)\s*#?\s*(?P<number>\d[\w.½]*)"
    r"(?:\s+(?P<year>(?:18|19|20)\d{2}))?\s*$"
)
# "Superman Brainiac" — no trailing number at all. Collected editions and trades
# carry issue_number "[nn]" in GCD, not a real number, so a query for one can never
# match _WITH_NUMBER; this is the fallback, tried only when that one doesn't match.
_SERIES_ONLY = re.compile(r"^\s*(?P<series>\S.*?)\s*$")


def parse_query(text: str) -> tuple[Candidate, Scope] | None:
    """Turn what a person typed into a lookup candidate plus a narrowing scope."""
    match = _WITH_NUMBER.match(text)
    if match:
        candidate = Candidate(
            signal="human",
            confidence=1.0,
            series=match.group("series"),
            issue_number=match.group("number"),
        )
        year = match.group("year")
        scope = Scope(year_from=int(year), year_to=int(year)) if year else Scope()
        return candidate, scope

    match = _SERIES_ONLY.match(text)
    if match:
        candidate = Candidate(signal="human", confidence=1.0, series=match.group("series"))
        return candidate, Scope()

    return None


class ConfirmService:
    """Looks up a human's answer and records their confirmation."""

    def __init__(self, resolver: IssueResolver, repository: Repository) -> None:
        self._resolver = resolver
        self._repository = repository

    def lookup(self, text: str) -> list[Issue]:
        """Issues matching what the person typed, best first. Empty if nothing matches.

        Collapses rows identical on every field LoCG import actually uses. A single
        issue with several variant covers is many rows in the metadata catalogue —
        one per printing — but LoCG cannot match on the variant (see the design
        notes), so a person picking an identification has nothing to tell those
        rows apart by. Rows differing only in gcd_id and printing are the same
        choice; anything else that differs (a later reprint's on-sale date, for
        instance) is a real difference and stays separate.
        """
        parsed = parse_query(text)
        if parsed is None:
            return []
        candidate, scope = parsed
        issues = self._resolver.resolve(candidate, scope)

        seen: set[tuple[str, str, str, object]] = set()
        deduped: list[Issue] = []
        for issue in issues:
            key = (issue.publisher, issue.series, issue.issue_number, issue.on_sale_date)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(issue)
        return deduped

    def confirm(
        self, result: IdentifyResult, issue: Issue, signal: str = "human"
    ) -> IdentifyResult:
        """Record an identification. The pixels are released — the comic is known."""
        entry = issue.to_catalog_entry()
        entry = dataclasses.replace(
            entry,
            tags=f"comicload;photo={result.filename};signal={signal};conf=1.00",
        )
        confirmed = dataclasses.replace(
            result,
            bucket=Bucket.CONFIDENT,
            entry=entry,
            image=None,
        )
        self._repository.save([confirmed])
        return confirmed
