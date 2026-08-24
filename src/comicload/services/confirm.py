"""Human identification of quarantined covers — the last resort after every signal.

Automated signals (barcode today; OCR, cover matching and LLM lookup when they land)
run first inside IdentifyService. Whatever they cannot settle waits here for a person.
The person types what they can see — "Superman #35" — and the catalogue does the rest,
so even human answers get real on-sale dates and exact titles from GCD.
"""

from __future__ import annotations

import dataclasses
import re

from comicload.core.models import Bucket, Candidate, IdentifyResult, Issue, Scope
from comicload.core.ports import IssueResolver, Repository

# "Superman #35", "superman 35", "Alex + Ada #2" — series, then a trailing number
_QUERY = re.compile(r"^\s*(?P<series>.+?)\s*#?\s*(?P<number>\d[\w.½]*)\s*$")


def parse_query(text: str) -> Candidate | None:
    """Turn what a person typed into a lookup candidate, or None if unparseable."""
    match = _QUERY.match(text)
    if not match:
        return None
    return Candidate(
        signal="human",
        confidence=1.0,
        series=match.group("series"),
        issue_number=match.group("number"),
    )


class ConfirmService:
    """Looks up a human's answer and records their confirmation."""

    def __init__(self, resolver: IssueResolver, repository: Repository) -> None:
        self._resolver = resolver
        self._repository = repository

    def lookup(self, text: str, scope: Scope | None = None) -> list[Issue]:
        """Issues matching what the person typed, best first. Empty if nothing matches."""
        candidate = parse_query(text)
        if candidate is None:
            return []
        return self._resolver.resolve(candidate, scope or Scope())

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
