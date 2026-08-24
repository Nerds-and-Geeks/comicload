from __future__ import annotations

from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TaskProgressColumn,
    TextColumn,
)


class RichProgressReporter:
    """ProgressReporter backed by Rich. A web adapter would write job state instead."""

    def __init__(self) -> None:
        self._progress: Progress | None = None
        self._task_id: TaskID | None = None

    def start(self, total: int, label: str) -> None:
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("{task.fields[detail]}"),
        )
        self._progress.start()
        self._task_id = self._progress.add_task(label, total=max(total, 1), detail="")

    def advance(self, amount: int = 1, message: str | None = None) -> None:
        if self._progress is None or self._task_id is None:
            return
        self._progress.update(self._task_id, advance=amount, detail=message or "")

    def finish(self) -> None:
        if self._progress is not None:
            self._progress.stop()
            self._progress = None
            self._task_id = None
