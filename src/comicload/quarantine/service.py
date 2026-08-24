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
_QUERY = re.compile(
    r"^\s*(?P<series>.+?)\s*#?\s*(?P<number>\d[\w.½]*)"
    r"(?:\s+(?P<year>(?:18|19|20)\d{2}))?\s*$"
)


def parse_query(text: str) -> tuple[Candidate, Scope] | None:
    """Turn what a person typed into a lookup candidate plus a narrowing scope."""
    match = _QUERY.match(text)
    if not match:
        return None
    candidate = Candidate(
        signal="human",
        confidence=1.0,
        series=match.group("series"),
        issue_number=match.group("number"),
    )
    year = match.group("year")
    scope = Scope(year_from=int(year), year_to=int(year)) if year else Scope()
    return candidate, scope


class ConfirmService:
    """Looks up a human's answer and records their confirmation."""

    def __init__(self, resolver: IssueResolver, repository: Repository) -> None:
        self._resolver = resolver
        self._repository = repository

    def lookup(self, text: str) -> list[Issue]:
        """Issues matching what the person typed, best first. Empty if nothing matches."""
        parsed = parse_query(text)
        if parsed is None:
            return []
        candidate, scope = parsed
        return self._resolver.resolve(candidate, scope)

    def confirm(self, result: IdentifyResult, issue: Issue) -> IdentifyResult:
        """Record the person's identification. The pixels are released — the comic is known."""
        entry = issue.to_catalog_entry()
        entry = dataclasses.replace(
            entry,
            tags=f"comicload;photo={result.filename};signal=human;conf=1.00",
        )
        confirmed = dataclasses.replace(
            result,
            bucket=Bucket.CONFIDENT,
            entry=entry,
            image=None,
        )
        self._repository.save([confirmed])
        return confirmed
