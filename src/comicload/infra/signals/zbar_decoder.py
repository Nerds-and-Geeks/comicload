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


def pyzbar_decoder(image_bytes: bytes) -> Sequence[DecodedBarcode]:
    """Decode UPC/EAN barcodes from cover photo bytes using pyzbar.

    Tries full cover image first. If raw decode yields 0 symbols (common on bagged
    comics with sleeve glare), evaluates regional corner crops with 3x upscaling
    and histogram equalization.
    """
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
