from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from comicload.core.models import Bucket, Candidate, IdentifyResult, ImportResult

console = Console()

# Everything here interpolates values the user chose: filenames, folder names, series
# titles. Rich reads square brackets as markup, so `Batman [red]variant.jpg` would print
# as `Batman variant.jpg` and `a[/bold]b.jpg` would raise MarkupError. Every one of these
# values goes through escape() before it reaches a renderable.


def _best_guess(candidate: Candidate | None) -> str:
    if candidate is None:
        return "—"
    parts = [
        part
        for part in (
            candidate.series,
            f"#{candidate.issue_number}" if candidate.issue_number else None,
        )
        if part
    ]
    return escape(" ".join(parts) or candidate.barcode or "—")


def summary_table(results: Sequence[IdentifyResult]) -> Table:
    counts = {bucket: 0 for bucket in Bucket}
    for result in results:
        counts[result.bucket] += 1

    table = Table(title="Scan results", show_header=True, header_style="bold")
    table.add_column("Outcome")
    table.add_column("Count", justify="right")
    table.add_row("[green]Identified[/green]", str(counts[Bucket.CONFIDENT]))
    table.add_row("[yellow]Needs review[/yellow]", str(counts[Bucket.AMBIGUOUS]))
    table.add_row("[red]Not recognised[/red]", str(counts[Bucket.UNRECOGNIZED]))
    return table


def review_table(results: Sequence[IdentifyResult]) -> Table:
    table = Table(title="Waiting for review", show_header=True, header_style="bold")
    table.add_column("Photo")
    table.add_column("Outcome")
    table.add_column("Best guess")
    for result in results:
        best = result.candidates[0] if result.candidates else None
        table.add_row(escape(result.filename), escape(result.bucket.value), _best_guess(best))
    return table


def import_panel(result: ImportResult) -> Panel:
    lines = [
        f"Comics sent:   [bold]{result.total}[/bold]",
        f"Matched:       [green]{result.matched}[/green]",
        f"Not matched:   [yellow]{result.unmatched}[/yellow]",
        f"Destination:   {escape(result.destination)}",
    ]
    if result.view_url:
        lines.append("")
        url = escape(result.view_url)
        lines.append(f"View your collection: [link={url}]{url}[/link]")
    return Panel("\n".join(lines), title="Import complete", border_style="green")
