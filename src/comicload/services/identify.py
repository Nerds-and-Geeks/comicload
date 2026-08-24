from __future__ import annotations

import dataclasses
from collections.abc import Sequence

from comicload.core.errors import ComicloadError
from comicload.core.models import Bucket, Candidate, IdentifyResult, Issue, Photo, Scope
from comicload.core.ports import (
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

    def _gather(self, photo: Photo, scope: Scope) -> tuple[list[Candidate], tuple[str, ...]]:
        """Every signal's guesses for one photo, plus the names of the ones that crashed.

        A crashing signal must not stop the run — but the failure is reported back rather
        than discarded, so 200 photos cannot come back as "not recognised" when the truth
        is that one signal failed 200 times.
        """
        gathered: list[Candidate] = []
        failures: list[str] = []
        for signal in self._signals:
            try:
                gathered.extend(signal.identify(photo, scope))
            except ComicloadError:
                raise  # deliberate, explained, and true of every photo: let it stop the run
            except Exception:  # noqa: BLE001 - a crashing signal must not stop the run
                failures.append(getattr(signal, "name", type(signal).__name__))
        ordered = sorted(gathered, key=lambda c: c.confidence, reverse=True)
        return ordered, tuple(failures)

    def _tags(self, photo: Photo, candidate: Candidate) -> str:
        return (
            f"comicload;photo={photo.filename};"
            f"signal={candidate.signal};conf={candidate.confidence:.2f}"
        )

    def _classify(
        self,
        photo: Photo,
        candidates: list[Candidate],
        scope: Scope,
        signal_failures: tuple[str, ...] = (),
    ) -> IdentifyResult:
        if not candidates:
            return IdentifyResult(
                photo_id=photo.id,
                filename=photo.filename,
                bucket=Bucket.UNRECOGNIZED,
                signal_failures=signal_failures,
            )

        resolved_any = False
        for candidate in candidates:
            issues: list[Issue] = self._resolver.resolve(candidate, scope)
            if not issues:
                continue
            resolved_any = True
            if len(issues) == 1 and candidate.confidence >= self._threshold:
                entry = issues[0].to_catalog_entry()
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
        )

    def run(self, source: PhotoSource, scope: Scope) -> list[IdentifyResult]:
        self._progress.start(source.count(), "Identifying")
        results: list[IdentifyResult] = []
        try:
            for photo in source.photos():
                candidates, failures = self._gather(photo, scope)
                results.append(self._classify(photo, candidates, scope, failures))
                self._progress.advance(1, photo.filename)
        finally:
            self._progress.finish()
        return results
