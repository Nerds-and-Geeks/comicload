"""The barcode signal. Pure orchestration — it never touches pyzbar or PIL.

The actual decoding lives in `zbar_decoder`, the one module allowed to import the
native stack. The composition root imports that module once at startup and injects a
decoder here; a missing native library therefore surfaces exactly once, with an
install instruction, never per photo.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from comicload.domain.errors import ComicloadError
from comicload.domain.models import Candidate, Photo, Scope
from comicload.signals.registry import register_signal

DecodedBarcode = tuple[str, str | None]
Decoder = Callable[[bytes], Sequence[DecodedBarcode]]

# A barcode is read, not guessed. The concatenated form is tried before the bare UPC
# only because it is more specific — both are exact matches when the catalogue has them.
FULL_BARCODE_CONFIDENCE = 0.95
BARE_UPC_CONFIDENCE = 0.9

MISSING_ZBAR = (
    "comicload cannot read barcodes because zbar, the library that does the reading, "
    "is not installed.\n"
    "Install it and scan again:\n"
    "    macOS:          brew install zbar\n"
    "    Debian/Ubuntu:  sudo apt install libzbar0"
)

_PRINTING_LABELS = {
    "1": "1st Printing",
    "2": "2nd Printing",
    "3": "3rd Printing",
    "4": "4th Printing",
    "5": "5th Printing",
}


def decode_supplement(supplement: str) -> tuple[str | None, str | None]:
    """Split a 5-digit EAN-5 supplement into (issue number, printing label).

    The common Marvel/DC layout is IIIVV: three issue digits, two variant digits,
    where the final digit is the printing number. This is empirical, not a
    standard, so callers must treat the result as a hint.
    """
    if len(supplement) != 5 or not supplement.isdigit():
        return (None, None)
    issue = str(int(supplement[:3]))
    printing = _PRINTING_LABELS.get(supplement[-1])
    return (issue, printing)


@register_signal("barcode")
class BarcodeSignal:
    """Turns decoded barcodes into candidates.

    A decoded barcode is a machine reading, not a guess, so both forms it can take are
    offered at high confidence and the catalogue decides between them: first the
    17-character concatenation of UPC and EAN-5 supplement, then the bare UPC as a
    fallback. That fallback matters — comics without a printed supplement (most
    pre-1990s issues, trades and one-shots) are recorded under the bare UPC, and tying
    confidence to the presence of a supplement made them impossible to identify even on
    an exact database match.

    What makes a photo CONFIDENT is the barcode resolving to exactly one issue, which
    only IdentifyService can know. The supplement's issue number and printing are
    empirical hints: they never drive the lookup, but they are what `review` shows and
    what lands in an entry's notes.

    A photo the decoder cannot read yields no candidates. A signal constructed without
    any decoder raises instead — that is a wiring failure that would silently zero out
    an entire scan.
    """

    name = "barcode"

    def __init__(self, decoder: Decoder | None = None) -> None:
        self._decode = decoder

    def identify(self, photo: Photo, scope: Scope) -> list[Candidate]:
        if self._decode is None:
            raise ComicloadError(MISSING_ZBAR)
        try:
            decoded = self._decode(photo.data)
        except Exception:
            return []

        candidates: list[Candidate] = []
        for main, supplement in decoded:
            # zbar reports UPC-A as EAN-13 with a leading zero; GCD stores the
            # 12-digit UPC-A form, so normalise before the catalogue sees it.
            if len(main) == 13 and main.startswith("0"):
                main = main[1:]
            issue, printing = decode_supplement(supplement) if supplement else (None, None)
            evidence = {"upc": main, "supplement": supplement or ""}
            if supplement:
                candidates.append(
                    Candidate(
                        signal=self.name,
                        confidence=FULL_BARCODE_CONFIDENCE,
                        barcode=f"{main}{supplement}",
                        issue_number=issue,
                        printing=printing,
                        evidence=dict(evidence),
                    )
                )
            candidates.append(
                Candidate(
                    signal=self.name,
                    confidence=BARE_UPC_CONFIDENCE,
                    barcode=main,
                    issue_number=issue,
                    printing=printing,
                    evidence=dict(evidence),
                )
            )
        return candidates
