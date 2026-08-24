from __future__ import annotations

import io
import os
import platform
from collections.abc import Callable, Sequence
from typing import Any

from PIL import Image, ImageOps

from comicload.core.errors import ComicloadError
from comicload.core.models import Candidate, Photo, Scope
from comicload.core.registry import register_signal

DecodedBarcode = tuple[str, str | None]
Decoder = Callable[[bytes], Sequence[DecodedBarcode]]

# A barcode is read, not guessed. The concatenated form is tried before the bare UPC
# only because it is more specific — both are exact matches when the catalogue has them.
FULL_BARCODE_CONFIDENCE = 0.95
BARE_UPC_CONFIDENCE = 0.9

_PRINTING_LABELS = {
    "1": "1st Printing",
    "2": "2nd Printing",
    "3": "3rd Printing",
    "4": "4th Printing",
    "5": "5th Printing",
}


def _setup_mac_zbar_path() -> None:
    """Ensure macOS Apple Silicon Homebrew libzbar path is registered in DYLD_LIBRARY_PATH."""
    if platform.system() == "Darwin" and os.path.exists("/opt/homebrew/lib/libzbar.dylib"):
        dyld_path = os.environ.get("DYLD_LIBRARY_PATH")
        if not dyld_path or "/opt/homebrew/lib" not in dyld_path:
            os.environ["DYLD_LIBRARY_PATH"] = (
                f"{dyld_path}:/opt/homebrew/lib" if dyld_path else "/opt/homebrew/lib"
            )


_setup_mac_zbar_path()


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

        return pyzbar
    except (ImportError, OSError) as exc:
        raise ComicloadError(f"{MISSING_ZBAR}\n\n({exc})") from exc


def _pyzbar_decoder(image_bytes: bytes) -> Sequence[DecodedBarcode]:
    pyzbar = _import_pyzbar()

    raw_image = Image.open(io.BytesIO(image_bytes))
    image = ImageOps.exif_transpose(raw_image)

    found_symbols: list[Any] = list(pyzbar.decode(image))

    if not found_symbols:
        w, h = image.size
        crop_regions = [
            image.crop((int(w * 0.6), int(h * 0.6), w, h)),
            image.crop((0, int(h * 0.6), int(w * 0.4), h)),
            image.crop((0, 0, int(w * 0.4), int(h * 0.4))),
            image.crop((0, int(h * 0.5), w, h)),
        ]
        for region in crop_regions:
            scaled = region.resize((region.width * 3, region.height * 3), Image.Resampling.LANCZOS)
            equalized = ImageOps.equalize(scaled.convert("L"))
            found_symbols = list(pyzbar.decode(equalized))
            if found_symbols:
                break

    found: list[DecodedBarcode] = []
    main: str | None = None
    supplement: str | None = None
    for result in found_symbols:
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
            evidence = {"upc": main, "supplement": supplement or ""}
            if supplement:
                candidates.append(
                    Candidate(
                        signal=self.name,
                        confidence=FULL_BARCODE_CONFIDENCE,
                        barcode=f"{main}{supplement}",
                        issue_number=issue,
                        printing=printing,
                        evidence=evidence,
                    )
                )
            candidates.append(
                Candidate(
                    signal=self.name,
                    confidence=BARE_UPC_CONFIDENCE,
                    barcode=main,
                    issue_number=issue,
                    printing=printing,
                    evidence=evidence,
                )
            )
        return candidates
