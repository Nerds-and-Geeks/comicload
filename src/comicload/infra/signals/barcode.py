from __future__ import annotations

import io
from collections.abc import Callable, Sequence
from typing import Any

from comicload.core.errors import ComicloadError
from comicload.core.models import Candidate, Photo, Scope
from comicload.core.registry import register_signal

DecodedBarcode = tuple[str, str | None]
Decoder = Callable[[bytes], Sequence[DecodedBarcode]]

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


MISSING_ZBAR = (
    "comicload cannot read barcodes because zbar, the library that does the reading, "
    "is not installed.\n"
    "Install it and scan again:\n"
    "    macOS:          brew install zbar\n"
    "    Debian/Ubuntu:  sudo apt install libzbar0"
)


def _import_pyzbar() -> Any:
    """Import pyzbar lazily, and say what to install when its native library is absent."""
    try:
        from pyzbar import pyzbar
    except (ImportError, OSError) as exc:  # zbar itself is missing, not just the wheel
        raise ComicloadError(f"{MISSING_ZBAR}\n\n({exc})") from exc
    return pyzbar


def _pyzbar_decoder(image_bytes: bytes) -> Sequence[DecodedBarcode]:
    from PIL import Image

    pyzbar = _import_pyzbar()

    image = Image.open(io.BytesIO(image_bytes))
    found: list[DecodedBarcode] = []
    main: str | None = None
    supplement: str | None = None
    for result in pyzbar.decode(image):
        value = result.data.decode("ascii", errors="ignore")
        if len(value) == 5:
            supplement = value
        elif len(value) >= 8:
            main = value
    if main:
        found.append((main, supplement))
    return found


@register_signal("barcode")
class BarcodeSignal:
    """Decodes UPC/EAN from a cover photo.

    The full barcode string is the payload; the catalogue matches it directly.
    Supplement decoding only narrows when that direct match fails.

    A photo this signal cannot read yields no candidates. A *library* it cannot load is
    a different thing entirely — that would fail on every photo in the run — so it
    raises ComicloadError naming what to install instead of returning nothing.
    """

    name = "barcode"

    def __init__(self, decoder: Decoder | None = None) -> None:
        self._decode = decoder or _pyzbar_decoder

    def identify(self, photo: Photo, scope: Scope) -> list[Candidate]:
        try:
            decoded = self._decode(photo.data)
        except ComicloadError:
            raise  # a missing library breaks every photo; do not hide it as "not read"
        except ImportError as exc:
            raise ComicloadError(f"{MISSING_ZBAR}\n\n({exc})") from exc
        except Exception:
            return []  # this one photo is corrupt or unreadable; the rest may be fine

        candidates: list[Candidate] = []
        for main, supplement in decoded:
            issue, printing = decode_supplement(supplement) if supplement else (None, None)
            candidates.append(
                Candidate(
                    signal=self.name,
                    confidence=0.95 if supplement else 0.75,
                    barcode=f"{main}{supplement}" if supplement else main,
                    issue_number=issue,
                    printing=printing,
                    evidence={"upc": main, "supplement": supplement or ""},
                )
            )
        return candidates
