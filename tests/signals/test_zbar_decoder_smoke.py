"""An end-to-end decode test against a real, rendered barcode image.

Every other zbar_decoder test monkeypatches pyzbar.decode() to avoid needing the
native library. That is correct for testing our own selection/filtering logic, but
it means none of those tests would have caught this file's actual failure mode —
three separate real-world regressions today, each landing because someone edited
the decode loop, ran `make check`, saw green, and never once called the function.

This test calls the real pyzbar_decoder() against real pixels. It is the only test
in the suite that would have caught any of them. Skips cleanly where zbar's native
library is not installed — everywhere else, it must pass before this file changes.
"""

from __future__ import annotations

import pytest
from PIL import Image

try:
    # Importing this module runs its own setup_environment() first, which is
    # what makes the native library findable on Apple Silicon — importing
    # pyzbar directly here, ahead of that, would fail even when it is installed.
    from comicload.signals.zbar_decoder import pyzbar_decoder
except (ImportError, OSError):
    pytest.skip("native zbar library not installed here", allow_module_level=True)

# EAN-13 "6291041500213" — a commonly used public test barcode value — rendered
# as a real bar pattern via the standard L/G/R code tables, same technique already
# used for EAN-5 in test_ean5.py's render() helper.
_L_CODES = {
    "0": "0001101",
    "1": "0011001",
    "2": "0010011",
    "3": "0111101",
    "4": "0100011",
    "5": "0110001",
    "6": "0101111",
    "7": "0111011",
    "8": "0110111",
    "9": "0001011",
}
_G_CODES = {
    "0": "0100111",
    "1": "0110011",
    "2": "0011011",
    "3": "0100001",
    "4": "0011101",
    "5": "0111001",
    "6": "0000101",
    "7": "0010001",
    "8": "0001001",
    "9": "0010111",
}
_R_CODES = {
    digit: "".join("1" if b == "0" else "0" for b in code) for digit, code in _L_CODES.items()
}
# First-digit parity pattern for EAN-13: which of the next 6 digits use L vs G.
_FIRST_DIGIT_PARITY = {
    "0": "LLLLLL",
    "1": "LLGLGG",
    "2": "LLGGLG",
    "3": "LLGGGL",
    "4": "LGLLGG",
    "5": "LGGLLG",
    "6": "LGGGLL",
    "7": "LGLGLG",
    "8": "LGLGGL",
    "9": "LGGLGL",
}


def _ean13_modules(digits: str) -> str:
    first, left, right = digits[0], digits[1:7], digits[7:]
    parity = _FIRST_DIGIT_PARITY[first]
    modules = "101"
    for digit, side in zip(left, parity, strict=True):
        modules += _L_CODES[digit] if side == "L" else _G_CODES[digit]
    modules += "01010"
    for digit in right:
        modules += _R_CODES[digit]
    modules += "101"
    return modules


def _render_ean13(
    digits: str, module_px: int = 3, height: int = 200, quiet: int = 40
) -> Image.Image:
    modules = _ean13_modules(digits)
    width = quiet * 2 + len(modules) * module_px
    image = Image.new("L", (width, height), 255)
    pixels = image.load()
    x = quiet
    for module in modules:
        if module == "1":
            for dx in range(module_px):
                for y in range(height):
                    pixels[x + dx, y] = 0
        x += module_px
    return image.convert("RGB")


def test_a_real_ean13_barcode_decodes_end_to_end(tmp_path):
    """The regression test. Renders a real barcode, saves it as the PNG bytes
    the pipeline actually passes around, and calls the real decoder function —
    no mocking anywhere in this path."""
    import io

    digits = "6291041500213"
    image = _render_ean13(digits)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")

    result = pyzbar_decoder(buffer.getvalue())

    assert result, "pyzbar_decoder found nothing on a clean, full-resolution barcode"
    main, _supplement = result[0]
    assert main == digits
