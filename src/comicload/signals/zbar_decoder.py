from __future__ import annotations

import io
import os
import platform
from collections.abc import Sequence
from typing import Any

from PIL import Image, ImageOps

DecodedBarcode = tuple[str, str | None]


def setup_environment() -> None:
    """Ensure macOS Apple Silicon Homebrew libzbar path is registered in DYLD_LIBRARY_PATH."""
    if platform.system() == "Darwin" and os.path.exists("/opt/homebrew/lib/libzbar.dylib"):
        dyld_path = os.environ.get("DYLD_LIBRARY_PATH")
        if not dyld_path or "/opt/homebrew/lib" not in dyld_path:
            os.environ["DYLD_LIBRARY_PATH"] = (
                f"{dyld_path}:/opt/homebrew/lib" if dyld_path else "/opt/homebrew/lib"
            )


setup_environment()

from pyzbar import pyzbar  # noqa: E402

from comicload.signals.ean5 import decode_ean5  # noqa: E402

_MAIN_TYPES = {"EAN13", "UPCA", "EAN8", "UPCE"}


def _extract_barcodes(
    decoded_from: Image.Image, found_symbols: Sequence[Any]
) -> list[DecodedBarcode]:
    found: list[DecodedBarcode] = []
    main_symbol: Any = None
    main: str | None = None
    supplement: str | None = None
    for result in found_symbols:
        value = result.data.decode("ascii", errors="ignore")
        if len(value) == 5 and result.type in ("EAN5", "I25", ""):
            supplement = value
        elif result.type in _MAIN_TYPES:
            main, main_symbol = value, result
    if main and supplement is None and main_symbol is not None:
        supplement = _decode_addon(decoded_from, main_symbol.rect)
    if main:
        found.append((main, supplement))
    return found


def pyzbar_decoder(image_bytes: bytes) -> Sequence[DecodedBarcode]:
    """Decode UPC/EAN barcodes from cover photo bytes using pyzbar.

    Evaluates 0°, 90°, 180°, 270° rotation angles and equalized corner crops.
    Ensures EAN-5 supplement barcodes are decoded to populate candidate issue numbers.
    """
    raw_image = Image.open(io.BytesIO(image_bytes))
    image = ImageOps.exif_transpose(raw_image)

    valid_types = {"EAN13", "UPCA", "EAN5", "UPCE", "ISBN13", "EAN8"}

    found_symbols: list[Any] = []
    decoded_from: Image.Image = image
    best_extracted: Sequence[DecodedBarcode] = []

    for angle in (0, 90, 180, 270):
        oriented = image if angle == 0 else image.rotate(angle, expand=True)
        symbols = [s for s in pyzbar.decode(oriented) if s.type in valid_types or not s.type]
        if symbols:
            extracted = _extract_barcodes(oriented, symbols)
            if extracted and extracted[0][1] is not None:
                return extracted
            if not best_extracted and extracted:
                best_extracted = extracted
                found_symbols = symbols
                decoded_from = oriented

        equalized_full = ImageOps.equalize(oriented.convert("L"))
        symbols = [s for s in pyzbar.decode(equalized_full) if s.type in valid_types or not s.type]
        if symbols:
            extracted = _extract_barcodes(equalized_full, symbols)
            if extracted and extracted[0][1] is not None:
                return extracted
            if not best_extracted and extracted:
                best_extracted = extracted
                found_symbols = symbols
                decoded_from = equalized_full

        w, h = oriented.size
        crop_regions = [
            oriented.crop((int(w * 0.6), int(h * 0.6), w, h)),
            oriented.crop((0, int(h * 0.6), int(w * 0.4), h)),
            oriented.crop((0, 0, int(w * 0.4), int(h * 0.4))),
            oriented.crop((0, int(h * 0.5), w, h)),
        ]
        for region in crop_regions:
            attempts = (
                ImageOps.equalize(region.convert("L")),
                ImageOps.equalize(
                    region.resize(
                        (region.width * 3, region.height * 3), Image.Resampling.LANCZOS
                    ).convert("L")
                ),
            )
            for attempt in attempts:
                symbols = [s for s in pyzbar.decode(attempt) if s.type in valid_types or not s.type]
                if symbols:
                    extracted = _extract_barcodes(attempt, symbols)
                    if extracted and extracted[0][1] is not None:
                        return extracted
                    if not best_extracted and extracted:
                        best_extracted = extracted
                        found_symbols = symbols
                        decoded_from = attempt
                        break
            if best_extracted and best_extracted[0][1] is not None:
                break

    if best_extracted:
        return best_extracted
    return _extract_barcodes(decoded_from, found_symbols)


# How far past the main symbol the add-on can sit, in multiples of its long side.
_ADDON_REACH = 2.5


def _decode_addon(image: Image.Image, rect: Any) -> str | None:
    """Look for the EAN-5 supplement in the regions where print puts it.

    The add-on follows the main code in reading direction; covers are photographed
    at every rotation, so both sides of both axes are tried, and vertical strips are
    rotated flat before decoding.
    """
    margin = 60
    left, top = rect.left, rect.top
    right, bottom = rect.left + rect.width, rect.top + rect.height
    vertical = rect.height > rect.width
    reach = int(max(rect.width, rect.height) * _ADDON_REACH)

    if vertical:
        crops = [
            image.crop((left - margin, bottom, right + margin, min(image.height, bottom + reach))),
            image.crop((left - margin, max(0, top - reach), right + margin, top)),
        ]
        regions = [crop.rotate(-90, expand=True) for crop in crops] + [
            crop.rotate(90, expand=True) for crop in crops
        ]
    else:
        regions = [
            image.crop((right, top - margin, min(image.width, right + reach), bottom + margin)),
            image.crop((max(0, left - reach), top - margin, left, bottom + margin)),
        ]

    for region in regions:
        if region.width < 20 or region.height < 10:
            continue
        decoded = decode_ean5(region)
        if decoded:
            return decoded
    return None
