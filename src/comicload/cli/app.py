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
    candidates_table,
    console,
    cover_lines,
    import_panel,
    item_panel,
    open_cover_image,
    summary_table,
)
from comicload.cli.wiring import get_default_barcode_decoder
from comicload.config import Config, load_config, save_config
from comicload.errors import ComicloadError
from comicload.export.csv import CsvSink, read_csv, validate_csv
from comicload.identification.service import IdentifyService
from comicload.ingestion.photos import LocalFolderPhotoSource
from comicload.models import Bucket, IdentifyResult, Issue
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
    save_old_run: Annotated[
        bool, typer.Option("--save-old-run", help="Preserve quarantine items from previous scans.")
    ] = False,
) -> None:
    """Identify every comic photo in a folder and write an import file."""
    config = load_config()
    catalog_path = db or config.gcd_db_path()
    catalogue_path = catalogue_db or config.catalogue_db_path()

    if not catalog_path.exists() or catalog_path.stat().st_size == 0:
        # scan depends on a built comic database; it builds one instead of
        # telling the person to go run a different command first.
        last_dump = Path(config.storage.last_dump) if config.storage.last_dump else None
        if last_dump is None or not last_dump.exists():
            raise _fail(
                "There is no comic database yet, and scan does not know where your "
                "Grand Comics Database dump is.\n"
                "Run this once: comicload catalog sync <path-to-your-gcd-dump.sql>"
            )
        console.print(
            f"[dim]No comic database yet — building one from {escape(str(last_dump))}...[/dim]"
        )
        _sync_catalog(last_dump, catalog_path)
        console.print()

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
    if not save_old_run:
        repository.clear_pending()
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
    out: Annotated[
        Path, typer.Option("--out", "-o", help="Where to write the updated CSV.")
    ] = DEFAULT_OUT,
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
        f"[bold]{len(pending)} comic(s) in quarantine.[/bold]\n"
        "Covers are opening in your image viewer (and linked below).\n"
        "Type a number [1-N] to select, or type title & issue (e.g. [bold]Superman #35[/bold]).\n"
    )
    identified = 0
    for position, result in enumerate(pending, start=1):
        image_path: Path | None = None
        if result.image:
            image_path = open_cover_image(result.image, result.filename)

        console.print()
        console.print(item_panel(result, position, len(pending), image_path=image_path))

        if result.image and not no_images:
            console.print(cover_lines(result.image), highlight=False)

        current_issues: list[Issue] = []
        for cand in result.candidates:
            if cand.barcode:
                current_issues.extend(service._resolver.resolve(cand))

        while True:
            if current_issues:
                console.print(candidates_table(current_issues))
                prompt_msg = (
                    f"Select [1-{len(current_issues)}], type search (e.g. 'Superman #35'), "
                    "[s]kip, [q]uit"
                )
            else:
                prompt_msg = "Type title & issue (e.g. 'Superman #35'), [s]kip, [q]uit"

            answer = typer.prompt(prompt_msg, default="s").strip()
            if answer.lower() == "q":
                console.print(f"\n[green]Identified {identified}[/green] this session.")
                if identified > 0:
                    confirmed_entries = repository.confirmed_entries()
                    import_res = CsvSink(out).push(confirmed_entries)
                    console.print(import_panel(import_res))
                return
            if answer.lower() == "s":
                break

            if answer.isdigit() and current_issues and 1 <= int(answer) <= len(current_issues):
                selected_issue = current_issues[int(answer) - 1]
                confirmed = service.confirm(result, selected_issue)
                assert confirmed.entry is not None
                console.print(
                    f"[green]✔ Confirmed: {escape(confirmed.entry.full_title)} "
                    "saved to your catalogue.[/green]\n"
                )
                identified += 1
                break

            issues = service.lookup(answer)
            if not issues:
                console.print(
                    f"[yellow]No match found for '{escape(answer)}'.[/yellow] "
                    "Check spelling, or press s to skip."
                )
                current_issues = []
                continue

            current_issues = issues

    console.print(f"\n[green]Identified {identified}[/green] of {len(pending)}.")
    if identified > 0:
        confirmed_entries = repository.confirmed_entries()
        import_res = CsvSink(out).push(confirmed_entries)
        console.print(import_panel(import_res))


@catalog_app.command("sync")
def catalog_sync(
    dump: Annotated[Path, typer.Argument(help="Path to the downloaded GCD .sql dump.")],
    db: Annotated[Path | None, typer.Option("--db", help="Where to build the database.")] = None,
) -> None:
    """Build the local comic metadata database from a Grand Comics Database dump."""
    config = load_config()
    target = db or config.gcd_db_path()
    counts = _sync_catalog(dump, target)

    if not db:
        # Remembered so `scan` can rebuild this database on its own later — the
        # user should not have to re-run this command before every scan.
        config.storage.last_dump = str(dump)
        save_config(config)

    for table, count in sorted(counts.items()):
        console.print(f"  {table}: [bold]{count:,}[/bold] rows")
    console.print(f"[green]Database ready:[/green] {escape(str(target))}")


def _sync_catalog(dump: Path, target: Path) -> dict[str, int]:
    """Build the metadata database at `target` from `dump`, with a progress bar."""
    progress = RichProgressReporter()
    try:
        # total = dump size in bytes; the loader reports bytes consumed per statement,
        # so the bar crawls through the whole file instead of sitting at a fake 100%.
        progress.start(max(dump.stat().st_size, 1), "Reading the dump")
        try:
            return load_dump(
                dump,
                target,
                on_progress=lambda consumed: progress.advance(consumed, "building your database"),
            )
        finally:
            progress.finish()
    except ComicloadError as exc:
        raise _fail(str(exc)) from exc


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
