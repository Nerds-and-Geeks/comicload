"""A filename is not markup. Square brackets in one must survive to the screen."""

from io import StringIO

from rich.console import Console

from comicload.adapters.cli.render import import_panel, review_table
from comicload.core.models import Bucket, Candidate, IdentifyResult, ImportResult


def _render(renderable) -> str:
    console = Console(file=StringIO(), width=200, no_color=True, highlight=False)
    console.print(renderable)
    return console.file.getvalue()


def test_a_filename_with_square_brackets_is_shown_in_full():
    result = IdentifyResult("p1", "Batman [red]variant.jpg", Bucket.UNRECOGNIZED)

    assert "Batman [red]variant.jpg" in _render(review_table([result]))


def test_a_closing_tag_in_a_filename_does_not_raise():
    """`a[/bold]b.jpg` used to raise MarkupError and take the whole run down."""
    result = IdentifyResult("p1", "a[/bold]b.jpg", Bucket.UNRECOGNIZED)

    assert "a[/bold]b.jpg" in _render(review_table([result]))


def test_a_best_guess_with_square_brackets_is_shown_in_full():
    result = IdentifyResult(
        "p1",
        "a.jpg",
        Bucket.AMBIGUOUS,
        candidates=(Candidate(signal="barcode", confidence=0.9, series="Crisis [Deluxe]"),),
    )

    assert "Crisis [Deluxe]" in _render(review_table([result]))


def test_a_destination_path_with_square_brackets_is_shown_in_full():
    result = ImportResult(total=1, matched=1, unmatched=0, destination="/tmp/my [comics]/out.csv")

    assert "/tmp/my [comics]/out.csv" in _render(import_panel(result))


def test_a_closing_tag_in_a_destination_does_not_raise():
    result = ImportResult(total=0, matched=0, unmatched=0, destination="/tmp/a[/bold]b.csv")

    assert "/tmp/a[/bold]b.csv" in _render(import_panel(result))
