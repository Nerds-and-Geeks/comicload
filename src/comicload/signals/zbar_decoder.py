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


def pyzbar_decoder(image_bytes: bytes) -> Sequence[DecodedBarcode]:
    """Decode UPC/EAN barcodes from cover photo bytes using pyzbar.

    Tries full cover image first. If raw decode yields 0 symbols (common on bagged
    comics with sleeve glare), evaluates regional corner crops with 3x upscaling
    and histogram equalization.
    """
    raw_image = Image.open(io.BytesIO(image_bytes))
    image = ImageOps.exif_transpose(raw_image)

    found_symbols: list[Any] = list(pyzbar.decode(image))
    decoded_from: Image.Image = image

    if not found_symbols:
        w, h = image.size
        crop_regions = [
            image.crop((int(w * 0.6), int(h * 0.6), w, h)),
            image.crop((0, int(h * 0.6), int(w * 0.4), h)),
            image.crop((0, 0, int(w * 0.4), int(h * 0.4))),
            image.crop((0, int(h * 0.5), w, h)),
        ]
        for region in crop_regions:
            # Equalize first without resizing — high-DPI sources decode as-is, and
            # upscaling them past zbar's sweet spot loses the read. The 3x upscale
            # remains as the second attempt for genuinely small regions.
            attempts = (
                ImageOps.equalize(region.convert("L")),
                ImageOps.equalize(
                    region.resize(
                        (region.width * 3, region.height * 3), Image.Resampling.LANCZOS
                    ).convert("L")
                ),
            )
            for attempt in attempts:
                found_symbols = list(pyzbar.decode(attempt))
                if found_symbols:
                    decoded_from = attempt
                    break
            if found_symbols:
                break

    found: list[DecodedBarcode] = []
    main_symbol: Any = None
    main: str | None = None
    supplement: str | None = None
    for result in found_symbols:
        value = result.data.decode("ascii", errors="ignore")
        if len(value) == 5:
            supplement = value
        elif len(value) >= 8:
            main, main_symbol = value, result
    if main and supplement is None and main_symbol is not None:
        # zbar cannot be made to read EAN-2/EAN-5 add-ons through pyzbar, and the
        # supplement is what tells one issue of a series from another when the
        # publisher reuses a single UPC. Decode it ourselves next to the main code.
        # rects are relative to whichever image actually decoded — full frame on
        # the first pass, a corner crop on the retry path
        supplement = _decode_addon(decoded_from, main_symbol.rect)
    if main:
        found.append((main, supplement))
    return found


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
