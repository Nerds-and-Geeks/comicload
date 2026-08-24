from __future__ import annotations

import base64
import contextlib
import io
import os
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path

from PIL import Image
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from comicload.models import Bucket, Candidate, IdentifyResult, ImportResult

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


def cover_lines(image_bytes: bytes, width: int = 42) -> str:
    """Render cover pixels for the terminal.

    iTerm2, VS Code, WezTerm, and compatible terminals get the real image inline (OSC 1337).
    Everything else gets a half-block ANSI mosaic — two pixels per character cell using ▀
    with truecolor foreground and background — which is recognisable enough to identify
    a cover in any terminal.
    """
    term = os.environ.get("TERM_PROGRAM", "")
    lc_term = os.environ.get("LC_TERMINAL", "")
    if term in ("iTerm.app", "WezTerm", "vscode") or lc_term == "iTerm2":
        payload = base64.b64encode(image_bytes).decode("ascii")
        return f"\033]1337;File=inline=1;width={width};preserveAspectRatio=1:{payload}\a"

    with Image.open(io.BytesIO(image_bytes)) as source:
        image = source.convert("RGB")
        # two pixel rows per text row; covers are ~1.5:1 tall
        height = max(2, int(width * image.height / image.width / 1.0)) & ~1
        image = image.resize((width, height))
        raw = image.tobytes()  # RGB triples, row-major
        pixels = [(raw[i], raw[i + 1], raw[i + 2]) for i in range(0, len(raw), 3)]
        rows = []
        for y in range(0, height, 2):
            cells = []
            for x in range(width):
                tr, tg, tb = pixels[y * width + x]
                br, bg_, bb = pixels[(y + 1) * width + x]
                cells.append(f"\033[38;2;{tr};{tg};{tb}m\033[48;2;{br};{bg_};{bb}m▀")
            rows.append("".join(cells) + "\033[0m")
        return "\n".join(rows)


def open_cover_image(image_bytes: bytes, filename: str) -> Path:
    """Save thumbnail to a temp file and launch OS viewer (macOS open / QuickLook).

    Returns the temp Path so the CLI can render clickable terminal links.
    """
    suffix = ".png" if image_bytes.startswith(b"\x89PNG") else ".jpg"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(image_bytes)
        tmp_path = Path(tmp.name)

    with contextlib.suppress(Exception):
        subprocess.Popen(
            ["open", str(tmp_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    return tmp_path


def item_panel(
    result: IdentifyResult, position: int, total: int, image_path: Path | None = None
) -> Panel:
    """Rich panel showing item progress, filename, detected barcodes, and clickable image link."""
    lines = [
        f"Filename:  [bold]{escape(result.filename)}[/bold]",
        f"Status:    [yellow]{escape(result.bucket.value)}[/yellow]",
    ]
    if image_path:
        url = f"file://{image_path.resolve()}"
        lines.append(f"Image:     [link={url}]📷 Click to view cover photo[/link]")
    barcodes = [c.barcode for c in result.candidates if c.barcode]
    if barcodes:
        lines.append(f"Barcode:   [cyan]{escape(barcodes[0])}[/cyan]")
    return Panel(
        "\n".join(lines),
        title=f"Quarantine Item {position} of {total}",
        border_style="cyan",
    )


def candidates_table(issues: Sequence[object]) -> Table:
    """Numbered table of issue matches with clickable GCD web links for quick [1-N] selection."""
    table = Table(title="Catalogue Matches", show_header=True, header_style="bold green")
    table.add_column("#", justify="right", style="bold yellow")
    table.add_column("Series (Year)")
    table.add_column("Issue #", justify="center")
    table.add_column("Publisher")
    table.add_column("Variant / Notes")
    table.add_column("On-Sale Date")
    table.add_column("GCD Web Link")

    for idx, issue in enumerate(issues, start=1):
        series_name = getattr(issue, "series", getattr(issue, "series_name", ""))
        series_year = getattr(issue, "series_year", None)
        series_text = f"{series_name} ({series_year})" if series_year else series_name
        issue_number = getattr(issue, "issue_number", getattr(issue, "number", ""))
        publisher = getattr(issue, "publisher", getattr(issue, "publisher_name", ""))
        variant = getattr(issue, "variant", "") or getattr(issue, "printing", "") or "—"
        on_sale = str(getattr(issue, "on_sale_date", "") or "—")

        gcd_id = getattr(issue, "gcd_id", None)
        if gcd_id:
            gcd_url = f"https://www.comics.org/issue/{gcd_id}/"
            link_cell = f"[link={gcd_url}]comics.org/issue/{gcd_id}[/link]"
        else:
            link_cell = "—"

        table.add_row(
            str(idx),
            escape(series_text),
            escape(str(issue_number)),
            escape(str(publisher)),
            escape(str(variant)),
            escape(on_sale),
            link_cell,
        )
    return table


def status_panel(
    confirmed_count: int,
    quarantine_count: int,
    catalogue_path: Path,
    catalog_path: Path,
) -> Panel:
    """Rich panel showing collection & quarantine statistics."""
    cat_size = (
        f"{catalogue_path.stat().st_size / 1024:.1f} KB" if catalogue_path.exists() else "0 KB"
    )
    gcd_size = (
        f"{catalog_path.stat().st_size / (1024 * 1024):.1f} MB" if catalog_path.exists() else "0 MB"
    )

    lines = [
        f"Collection Identified:  [bold green]{confirmed_count}[/bold green] comic(s)",
        f"Quarantine Pending:     [bold yellow]{quarantine_count}[/bold yellow] comic(s)",
        "",
        f"Collection Database:    {escape(str(catalogue_path))} ({cat_size})",
        f"Comic Metadata DB:      {escape(str(catalog_path))} ({gcd_size})",
    ]
    return Panel(
        "\n".join(lines),
        title="comicload Status & Collection Summary",
        border_style="cyan",
    )
