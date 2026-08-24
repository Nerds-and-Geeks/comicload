"""A deterministic EAN-5 supplement decoder.

zbar's add-on reading is not reachable through pyzbar, and without the 5-digit
supplement DC's shared-UPC scheme leaves every issue of a series ambiguous — one
UPC covers hundreds of issues, and only the supplement's 17-digit concatenation
matches a single GCD row.

EAN-5 is 48 modules: a 01011 start guard, then five digits of seven modules each,
separated by 01 delineators. The digit parities (L or G alphabet) encode a checksum,
which doubles as validation — a misread almost never survives it.

Pure Python over PIL pixels. No numpy, no native code.
"""

from __future__ import annotations

from PIL import Image

_L_CODES = {
    "0001101": "0",
    "0011001": "1",
    "0010011": "2",
    "0111101": "3",
    "0100011": "4",
    "0110001": "5",
    "0101111": "6",
    "0111011": "7",
    "0110111": "8",
    "0001011": "9",
}
_G_CODES = {
    "0100111": "0",
    "0110011": "1",
    "0011011": "2",
    "0100001": "3",
    "0011101": "4",
    "0111001": "5",
    "0000101": "6",
    "0010001": "7",
    "0001001": "8",
    "0010111": "9",
}
_PARITY = {
    "GGLLL": 0,
    "GLGLL": 1,
    "GLLGL": 2,
    "GLLLG": 3,
    "LGGLL": 4,
    "LLGGL": 5,
    "LLLGG": 6,
    "LGLGL": 7,
    "LGLLG": 8,
    "LLGLG": 9,
}

# start guard (5) + 5 digits x 7 + 4 delineators x 2
_TOTAL_MODULES = 5 + 5 * 7 + 4 * 2


def _checksum(digits: str) -> int:
    return (sum(int(d) * 3 for d in digits[0::2]) + sum(int(d) * 9 for d in digits[1::2])) % 10


def _binarize_row(image: Image.Image, y: int) -> list[int]:
    # the image is converted to "L" before this is called, so pixels are ints
    values = [
        int(v)
        for v in (image.getpixel((x, y)) for x in range(image.width))
        if isinstance(v, int | float)
    ]
    lo, hi = min(values), max(values)
    if hi - lo < 32:  # no contrast — not a barcode row
        return []
    threshold = (lo + hi) // 2
    return [1 if v < threshold else 0 for v in values]


def _runs(bits: list[int]) -> list[tuple[int, int]]:
    """Run-length encode: [(value, length), ...]."""
    out: list[tuple[int, int]] = []
    for bit in bits:
        if out and out[-1][0] == bit:
            out[-1] = (bit, out[-1][1] + 1)
        else:
            out.append((bit, 1))
    return out


def _decode_modules(modules: str) -> str | None:
    if len(modules) < _TOTAL_MODULES or not modules.startswith("01011"):
        return None
    digits = ""
    parity = ""
    position = 5
    for index in range(5):
        if index:
            if modules[position : position + 2] != "01":
                return None
            position += 2
        chunk = modules[position : position + 7]
        position += 7
        if chunk in _L_CODES:
            digits += _L_CODES[chunk]
            parity += "L"
        elif chunk in _G_CODES:
            digits += _G_CODES[chunk]
            parity += "G"
        else:
            return None
    if _PARITY.get(parity) != _checksum(digits):
        return None
    return digits


def _try_row(bits: list[int]) -> str | None:
    runs = _runs(bits)
    # slide over every bar run as a potential start of the guard
    for start in range(len(runs)):
        if runs[start][0] != 1:
            continue
        # guard is 0-1-0-1-1: from the leading bar that is runs bar(1), space(1),
        # bar(2) — the doubled bar merges into one run of twice the module width
        window = runs[start : start + 3]
        if len(window) < 3 or [r[0] for r in window] != [1, 0, 1]:
            continue
        module = window[0][1]
        if (
            module == 0
            or abs(window[1][1] - module) > max(1, module // 2)
            or abs(window[2][1] - 2 * module) > max(1, module // 2)
        ):
            continue
        # rebuild a module string from this point using the estimated width
        modules = "0"  # the guard's leading space module
        for value, length in runs[start:]:
            count = max(1, round(length / module))
            modules += str(value) * count
            if len(modules) >= _TOTAL_MODULES + 4:
                break
        decoded = _decode_modules(modules)
        if decoded:
            return decoded
    return None


def decode_ean5(image: Image.Image) -> str | None:
    """Decode an EAN-5 supplement from an image region, or None.

    Scans several horizontal lines, right-side-up and upside-down, and only returns
    digits that survive the parity checksum.
    """
    grey = image.convert("L")
    for candidate in (grey, grey.rotate(180)):
        for fraction in (0.5, 0.35, 0.65, 0.2, 0.8):
            y = int(candidate.height * fraction)
            if not 0 <= y < candidate.height:
                continue
            bits = _binarize_row(candidate, y)
            if not bits:
                continue
            decoded = _try_row(bits)
            if decoded:
                return decoded
    return None
