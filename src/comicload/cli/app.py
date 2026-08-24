from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape

import comicload.signals  # noqa: F401
from comicload.catalog.loader import load_dump
from comicload.catalog.repository import SqliteIssueResolver
from comicload.cli.progress import RichProgressReporter
from comicload.cli.render import (
    console,
    cover_lines,
    import_panel,
    summary_table,
)
from comicload.cli.wiring import get_default_barcode_decoder
from comicload.config import Config, load_config, save_config
from comicload.errors import ComicloadError
from comicload.export.csv import CsvSink, read_csv, validate_csv
from comicload.identification.service import IdentifyService
from comicload.ingestion.photos import LocalFolderPhotoSource
from comicload.models import Bucket, IdentifyResult
from comicload.quarantine.repository import SqliteRepository
from comicload.quarantine.service import ConfirmService
from comicload.signals.registry import get_signal

try:
    from comicload.infra.sinks.locg_sink import LocgPlaywrightSink as _LocgPlaywrightSink
except ImportError:
    _LocgPlaywrightSink = None

app = typer.Typer(
    help="Photograph your comics, identify them, and build your League of Comic Geeks collection.",
    no_args_is_help=True,
)
catalog_app = typer.Typer(help="Manage the local comic metadata database.")
config_app = typer.Typer(help="Set up comicload.")
app.add_typer(catalog_app, name="catalog")
app.add_typer(config_app, name="config")

DEFAULT_OUT = Path("collection.csv")


def _fail(message: str) -> typer.Exit:
    console.print(f"[red]{escape(message)}[/red]")
    return typer.Exit(code=1)


def _report_signal_failures(results: Sequence[IdentifyResult]) -> None:
    if not results:
        return
    counts = Counter(name for result in results for name in result.signal_failures)
    for name, count in sorted(counts.items()):
        if count == len(results):
            console.print(
                f"\n[red]The '{escape(name)}' signal failed on all {count} photo(s).[/red] "
                "Nothing above was really examined. Fix the error above and scan again."
            )
        else:
            console.print(
                f"\n[yellow]The '{escape(name)}' signal failed on {count} of "
                f"{len(results)} photo(s).[/yellow] Those photos were not fully examined."
            )


@app.command()
def scan(
    folder: Annotated[Path, typer.Argument(help="Folder containing your cover photos.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Where to write the CSV.")] = DEFAULT_OUT,
    db: Annotated[Path | None, typer.Option("--db", help="Path of the metadata database.")] = None,
    catalogue_db: Annotated[
        Path | None,
        typer.Option("--catalogue-db", help="Path of your catalogue of scan results."),
    ] = None,
) -> None:
    """Identify every comic photo in a folder and write an import file."""
    config = load_config()
    catalog_path = db or config.gcd_db_path()
    catalogue_path = catalogue_db or config.catalogue_db_path()

    try:
        source = LocalFolderPhotoSource(folder)
        source.count()
    except FileNotFoundError as exc:
        raise _fail(str(exc)) from exc

    try:
        signals = [
            get_signal(name, decoder=get_default_barcode_decoder())
            if name == "barcode"
            else get_signal(name)
            for name in config.signals.enabled
        ]
    except KeyError as exc:
        raise _fail(
            f"{exc.args[0]}\nEdit 'signals.enabled' in your settings "
            "(see 'comicload config show') and scan again."
        ) from exc

    try:
        with SqliteIssueResolver(catalog_path) as resolver:
            service = IdentifyService(
                signals=signals,
                resolver=resolver,
                progress=RichProgressReporter(),
            )
            results = service.run(source)
    except ComicloadError as exc:
        raise _fail(str(exc)) from exc

    repository = SqliteRepository(catalogue_path)
    repository.save(results)

    console.print(summary_table(results))
    _report_signal_failures(results)

    confirmed_entries = repository.confirmed_entries()
    result = CsvSink(out).push(confirmed_entries)
    console.print(import_panel(result))

    pending = [r for r in results if r.bucket is not Bucket.CONFIDENT]
    if pending:
        console.print(
            f"\n[yellow]Quarantined {len(pending)}.[/yellow] "
            "Run [bold]comicload review[/bold] to see them."
        )


@app.command(name="import")
def import_csv(
    file: Annotated[Path, typer.Argument(help="The CSV to check or upload.")],
    import_locg: Annotated[
        bool, typer.Option("--import-locg", help="Actually upload to League of Comic Geeks.")
    ] = False,
) -> None:
    """Check an import file, and optionally send it to League of Comic Geeks."""
    if not file.exists():
        raise _fail(f"No such file: {file}")

    problems = validate_csv(file)
    if problems:
        console.print("[red]This file is not ready to import:[/red]")
        for problem in problems[:20]:
            console.print(f"  • {escape(problem)}")
        if len(problems) > 20:
            console.print(f"  … and {len(problems) - 20} more")
        raise typer.Exit(code=1)

    entries = read_csv(file)
    console.print(f"[green]Looks good.[/green] {len(entries)} comic(s) ready to import.")

    if not import_locg:
        console.print(
            "\nNothing was uploaded. Add [bold]--import-locg[/bold] to send this to "
            "League of Comic Geeks."
        )
        return

    if _LocgPlaywrightSink is None:
        console.print(
            "[red]Uploading needs the optional browser extra.[/red]\n"
            r"Install it with: [bold]pip install 'comicload\[locg]'[/bold]"
        )
        raise typer.Exit(code=1)

    config = load_config()
    result = _LocgPlaywrightSink(config.catalogue_db_path()).push(entries)
    console.print(import_panel(result))


@app.command()
def review(
    db: Annotated[Path | None, typer.Option("--db", help="Path of the metadata database.")] = None,
    catalogue_db: Annotated[
        Path | None,
        typer.Option("--catalogue-db", help="Path of your catalogue of scan results."),
    ] = None,
    no_images: Annotated[
        bool, typer.Option("--no-images", help="Skip drawing covers in the terminal.")
    ] = False,
) -> None:
    """Identify the quarantined comics yourself — the covers are shown one by one."""
    config = load_config()
    catalogue_path = catalogue_db or config.catalogue_db_path()
    catalog_path = db or config.gcd_db_path()
    try:
        repository = SqliteRepository(catalogue_path)
        pending = repository.pending_review()
    except ComicloadError as exc:
        raise _fail(str(exc)) from exc

    if not pending:
        console.print(
            "[green]Nothing in quarantine.[/green] Every photo comicload has seen was "
            "identified — run [bold]comicload scan[/bold] on a folder to add more."
        )
        return

    try:
        service = ConfirmService(SqliteIssueResolver(catalog_path), repository)
    except ComicloadError as exc:
        raise _fail(str(exc)) from exc

    console.print(
        f"[bold]{len(pending)} comic(s) in quarantine.[/bold] "
        "Type what you can see on each cover, e.g. [bold]Superman #35[/bold] "
        "(add a year to narrow: [bold]Superman #35 2026[/bold]).\n"
    )
    identified = 0
    for position, result in enumerate(pending, start=1):
        console.rule(f"{position} of {len(pending)} — {escape(result.filename)}")
        if result.image and not no_images:
            console.print(cover_lines(result.image), highlight=False)
        hints = [c for c in result.candidates if c.barcode]
        if hints:
            console.print(f"[dim]barcode read: {escape(hints[0].barcode or '')}[/dim]")

        while True:
            answer = typer.prompt(
                "Who is this? (series #number, s=skip, q=quit)", default="s"
            ).strip()
            if answer.lower() == "q":
                console.print(f"\n[green]Identified {identified}[/green] this session.")
                return
            if answer.lower() in ("s", ""):
                break

            issues = service.lookup(answer)
            if not issues:
                console.print(
                    "[yellow]Nothing in the catalogue matches that.[/yellow] "
                    "Check the spelling, or press s to skip."
                )
                continue

            for index, issue in enumerate(issues, start=1):
                date_text = issue.on_sale_date.isoformat() if issue.on_sale_date else "date unknown"
                series_year = f" ({issue.series_year})" if issue.series_year else ""
                console.print(
                    f"  [bold]{index}[/bold]  {escape(issue.publisher)} · "
                    f"{escape(issue.series)}{series_year} #{escape(issue.issue_number)}"
                    f" · {date_text}"
                )
            choice = typer.prompt("Which one? (number, or s to search again)", default="1").strip()
            if choice.lower() == "s":
                continue
            if not choice.isdigit() or not 1 <= int(choice) <= len(issues):
                console.print("[yellow]That was not one of the numbers.[/yellow]")
                continue

            confirmed = service.confirm(result, issues[int(choice) - 1])
            assert confirmed.entry is not None
            console.print(
                f"[green]✔ {escape(confirmed.entry.full_title)}[/green] saved to your catalogue.\n"
            )
            identified += 1
            break

    console.print(
        f"\n[green]Identified {identified}[/green] of {len(pending)}. "
        "Re-run [bold]comicload scan[/bold] or export again to refresh your CSV."
    )


@catalog_app.command("sync")
def catalog_sync(
    dump: Annotated[Path, typer.Argument(help="Path to the downloaded GCD .sql dump.")],
    db: Annotated[Path | None, typer.Option("--db", help="Where to build the database.")] = None,
) -> None:
    """Build the local comic metadata database from a Grand Comics Database dump."""
    progress = RichProgressReporter()
    try:
        target = db or load_config().gcd_db_path()
        # total = dump size in bytes; the loader reports bytes consumed per statement,
        # so the bar crawls through the whole file instead of sitting at a fake 100%.
        progress.start(max(dump.stat().st_size, 1), "Reading the dump")
        try:
            counts = load_dump(
                dump,
                target,
                on_progress=lambda consumed: progress.advance(consumed, "building your database"),
            )
        finally:
            progress.finish()
    except ComicloadError as exc:
        raise _fail(str(exc)) from exc

    for table, count in sorted(counts.items()):
        console.print(f"  {table}: [bold]{count:,}[/bold] rows")
    console.print(f"[green]Database ready:[/green] {escape(str(target))}")


@config_app.command("show")
def config_show(
    path: Annotated[Path | None, typer.Option("--path", help="Config file to read.")] = None,
) -> None:
    """Show your current settings."""
    try:
        config = load_config(path)
    except ComicloadError as exc:
        raise _fail(str(exc)) from exc
    console.print_json(config.model_dump_json(indent=2))


@config_app.command("init")
def config_init(
    path: Annotated[Path | None, typer.Option("--path", help="Config file to write.")] = None,
) -> None:
    """Create a settings file with sensible defaults."""
    target = save_config(Config(), path)
    console.print(f"[green]Settings written to[/green] {escape(str(target))}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
