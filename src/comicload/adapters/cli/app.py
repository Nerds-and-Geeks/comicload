from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape

import comicload.infra.signals  # noqa: F401  (registers signals)
import comicload.infra.sinks  # noqa: F401  (registers sinks)
from comicload.adapters.cli.progress import RichProgressReporter
from comicload.adapters.cli.render import console, import_panel, review_table, summary_table
from comicload.core.errors import ComicloadError
from comicload.core.models import Bucket, IdentifyResult, Scope
from comicload.core.registry import get_signal
from comicload.infra.config import Config, load_config, save_config, sqlite_path
from comicload.infra.photos import LocalFolderPhotoSource
from comicload.infra.sinks.csv_sink import CsvSink, read_csv, validate_csv
from comicload.infra.storage.factory import open_repository, open_resolver
from comicload.infra.storage.gcd_loader import load_dump
from comicload.services.export import ExportService
from comicload.services.identify import IdentifyService

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
    """Print a message written for a comic collector, and stop with a non-zero status."""
    console.print(f"[red]{escape(message)}[/red]")
    return typer.Exit(code=1)


def _as_dsn(value: str) -> str:
    """Accept either a storage address or the bare file path collectors have always typed."""
    if "://" in value:
        return value
    return f"sqlite://{Path(value).expanduser()}"


def _report_signal_failures(results: Sequence[IdentifyResult]) -> None:
    """Say when a signal broke, instead of passing its silence off as 'not recognised'."""
    if not results:
        return
    counts = Counter(name for result in results for name in result.signal_failures)
    for name, count in sorted(counts.items()):
        if count == len(results):
            console.print(
                f"\n[red]The '{escape(name)}' signal failed on all {count} photo(s).[/red] "
                "Nothing above was really examined — these comics are not 'not recognised', "
                "they were never read. Fix the error above and scan again."
            )
        else:
            console.print(
                f"\n[yellow]The '{escape(name)}' signal failed on {count} of "
                f"{len(results)} photo(s).[/yellow] Those photos were not fully examined."
            )


def _scope(publisher: str | None, years: str | None) -> Scope:
    year_from = year_to = None
    if years:
        parts = years.split("-")
        if len(parts) != 2 or not all(p.strip().isdigit() for p in parts):
            raise typer.BadParameter("--years must look like 1970-1985")
        year_from, year_to = int(parts[0]), int(parts[1])
    return Scope(publisher=publisher, year_from=year_from, year_to=year_to)


@app.command()
def scan(
    folder: Annotated[Path, typer.Argument(help="Folder containing your cover photos.")],
    out: Annotated[Path, typer.Option("--out", "-o", help="Where to write the CSV.")] = DEFAULT_OUT,
    publisher: Annotated[
        str | None, typer.Option("--publisher", help="Narrow to one publisher.")
    ] = None,
    years: Annotated[
        str | None, typer.Option("--years", help="Narrow to a year range, e.g. 1970-1985.")
    ] = None,
    db: Annotated[
        str | None, typer.Option("--db", help="Path or address of the metadata database.")
    ] = None,
    catalogue_db: Annotated[
        str | None,
        typer.Option(
            "--catalogue-db", help="Path or address of your own catalogue of scan results."
        ),
    ] = None,
) -> None:
    """Identify every comic photo in a folder and write an import file."""
    config = load_config()
    catalog = _as_dsn(db) if db else config.catalog_dsn()
    catalogue = _as_dsn(catalogue_db) if catalogue_db else config.catalogue_dsn()

    try:
        source = LocalFolderPhotoSource(folder)
        source.count()
    except FileNotFoundError as exc:
        raise _fail(str(exc)) from exc

    signals = [get_signal(name) for name in config.signals.enabled]
    service = IdentifyService(
        signals=signals,
        resolver=open_resolver(catalog),
        progress=RichProgressReporter(),
    )

    try:
        results = service.run(source, _scope(publisher, years))
    except ComicloadError as exc:
        raise _fail(str(exc)) from exc

    if results:
        open_repository(catalogue).save(results)

    console.print(summary_table(results))
    _report_signal_failures(results)

    result = ExportService(CsvSink(out)).export(results)
    console.print(import_panel(result))

    pending = [r for r in results if r.bucket is not Bucket.CONFIDENT]
    if pending:
        console.print(
            f"\n[yellow]{len(pending)} photo(s) need a look.[/yellow] "
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

    try:
        from comicload.infra.sinks.locg_sink import LocgPlaywrightSink
    except ImportError:
        console.print(
            "[red]Uploading needs the optional browser extra.[/red]\n"
            r"Install it with: [bold]pip install 'comicload\[locg]'[/bold]"
        )
        raise typer.Exit(code=1) from None

    config = load_config()
    result = LocgPlaywrightSink(config.locg_state_path()).push(entries)
    console.print(import_panel(result))


@app.command()
def review(
    db: Annotated[
        str | None,
        typer.Option("--db", help="Path or address of your own catalogue of scan results."),
    ] = None,
) -> None:
    """Look at the comics comicload could not identify on its own."""
    catalogue = _as_dsn(db) if db else load_config().catalogue_dsn()
    pending = open_repository(catalogue).pending_review()

    if not pending:
        console.print(
            "[green]Nothing to review.[/green] Every photo comicload has seen was "
            "identified — run [bold]comicload scan[/bold] on a folder to add more."
        )
        return

    console.print(review_table(pending))


@catalog_app.command("sync")
def catalog_sync(
    dump: Annotated[Path, typer.Argument(help="Path to the downloaded GCD .sql dump.")],
    db: Annotated[str | None, typer.Option("--db", help="Where to build the database.")] = None,
) -> None:
    """Build the local comic metadata database from a Grand Comics Database dump."""
    try:
        target = sqlite_path(_as_dsn(db)) if db else load_config().gcd_db_path()
        counts = load_dump(dump, target)
    except ComicloadError as exc:
        raise _fail(str(exc)) from exc

    for table, count in counts.items():
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


@config_app.command("keys")
def config_keys(
    name: Annotated[str, typer.Argument(help="Which key to store, e.g. comicload/anthropic.")],
) -> None:
    """Store an API key in your system keychain. It is never written to a file."""
    from comicload.infra.secrets import KeyringSecretStore

    value = typer.prompt("Value", hide_input=True)
    KeyringSecretStore().set(name, value)
    console.print(f"[green]Saved[/green] {escape(name)} to your keychain.")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
