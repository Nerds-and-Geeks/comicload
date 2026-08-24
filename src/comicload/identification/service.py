from __future__ import annotations

import dataclasses
from collections.abc import Sequence

from comicload.domain.errors import ComicloadError
from comicload.domain.models import Bucket, Candidate, IdentifyResult, Issue, Photo, Scope
from comicload.domain.ports import (
    IssueResolver,
    NullProgressReporter,
    PhotoSource,
    ProgressReporter,
    Signal,
)

CONFIDENT_THRESHOLD = 0.85


class IdentifyService:
    """Runs every signal over every photo, resolves guesses, and buckets the outcome.

    A photo is CONFIDENT only when a high-confidence candidate resolves to exactly
    one issue. Everything else goes to review. Nothing is silently guessed.
    """

    def __init__(
        self,
        signals: Sequence[Signal],
        resolver: IssueResolver,
        progress: ProgressReporter | None = None,
        confident_threshold: float = CONFIDENT_THRESHOLD,
    ) -> None:
        self._signals = list(signals)
        self._resolver = resolver
        self._progress = progress or NullProgressReporter()
        self._threshold = confident_threshold

    def _gather(
        self, photo: Photo, scope: Scope | None = None
    ) -> tuple[list[Candidate], tuple[str, ...]]:
        gathered: list[Candidate] = []
        failures: list[str] = []
        target_scope = scope or Scope()
        for signal in self._signals:
            try:
                gathered.extend(signal.identify(photo, target_scope))
            except ComicloadError:
                raise
            except Exception:  # noqa: BLE001
                failures.append(getattr(signal, "name", type(signal).__name__))
        ordered = sorted(gathered, key=lambda c: c.confidence, reverse=True)
        return ordered, tuple(failures)

    def _tags(self, photo: Photo, candidate: Candidate) -> str:
        safe_filename = photo.filename.replace(";", "%3B")
        return (
            f"comicload;photo={safe_filename};"
            f"signal={candidate.signal};conf={candidate.confidence:.2f}"
        )

    def _classify(
        self,
        photo: Photo,
        candidates: list[Candidate],
        scope: Scope | None = None,
        signal_failures: tuple[str, ...] = (),
    ) -> IdentifyResult:
        target_scope = scope or Scope()
        if not candidates:
            return IdentifyResult(
                photo_id=photo.id,
                filename=photo.filename,
                bucket=Bucket.UNRECOGNIZED,
                signal_failures=signal_failures,
                image=photo.data,
            )

        resolved_any = False
        for candidate in candidates:
            issues: list[Issue] = self._resolver.resolve(candidate, target_scope)
            if not issues:
                continue
            resolved_any = True
            if len(issues) == 1 and candidate.confidence >= self._threshold:
                issue = dataclasses.replace(issues[0], printing=candidate.printing)
                entry = issue.to_catalog_entry()
                entry = dataclasses.replace(
                    entry,
                    notes=candidate.printing or "",
                    tags=self._tags(photo, candidate),
                )
                return IdentifyResult(
                    photo_id=photo.id,
                    filename=photo.filename,
                    bucket=Bucket.CONFIDENT,
                    entry=entry,
                    candidates=tuple(candidates),
                    signal_failures=signal_failures,
                )

        bucket = Bucket.AMBIGUOUS if resolved_any else Bucket.UNRECOGNIZED
        return IdentifyResult(
            photo_id=photo.id,
            filename=photo.filename,
            bucket=bucket,
            candidates=tuple(candidates),
            signal_failures=signal_failures,
            image=photo.data,
        )

    def run(self, source: PhotoSource, scope: Scope | None = None) -> list[IdentifyResult]:
        target_scope = scope or Scope()
        self._progress.start(source.count(), "Identifying")
        results: list[IdentifyResult] = []
        try:
            for photo in source.photos():
                candidates, failures = self._gather(photo, target_scope)
                results.append(self._classify(photo, candidates, target_scope, failures))
                self._progress.advance(1, photo.filename)
        finally:
            self._progress.finish()
        return results
