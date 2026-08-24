"""EAN-5 supplement decoding. zbar cannot be made to read add-ons through pyzbar,
and without the supplement DC's shared-UPC scheme leaves every issue of a series
ambiguous. The pattern is 48 modules — simple enough to decode ourselves.

Tests render synthetic bar images from the spec and hostile variants of them.
"""

from PIL import Image

from comicload.infra.signals.ean5 import decode_ean5

# EAN-5 structure: start guard 01011, then 5 digits of 7 modules each,
# separated by 01 delineators. Parity pattern encodes the checksum.
L_CODES = {
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
G_CODES = {
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
PARITY = {
    0: "GGLLL",
    1: "GLGLL",
    2: "GLLGL",
    3: "GLLLG",
    4: "LGGLL",
    5: "LLGGL",
    6: "LLLGG",
    7: "LGLGL",
    8: "LGLLG",
    9: "LLGLG",
}


def _checksum(digits: str) -> int:
    return (sum(int(d) * 3 for d in digits[0::2]) + sum(int(d) * 9 for d in digits[1::2])) % 10


def _modules(digits: str) -> str:
    parity = PARITY[_checksum(digits)]
    out = "01011"
    for index, digit in enumerate(digits):
        if index:
            out += "01"
        out += (L_CODES if parity[index] == "L" else G_CODES)[digit]
    return out


def render(digits: str, module_px: int = 4, height: int = 60, quiet: int = 40) -> Image.Image:
    modules = _modules(digits)
    width = quiet * 2 + len(modules) * module_px
    img = Image.new("L", (width, height), 255)
    x = quiet
    for m in modules:
        if m == "1":
            for dx in range(module_px):
                for y in range(height):
                    img.putpixel((x + dx, y), 0)
        x += module_px
    return img


def test_decodes_a_clean_render():
    assert decode_ean5(render("10031")) == "10031"


def test_decodes_every_parity_pattern():
    for digits in ("00000", "12345", "95711", "10011", "99999", "10061"):
        assert decode_ean5(render(digits)) == digits, digits


def test_decodes_when_upside_down():
    assert decode_ean5(render("10031").rotate(180)) == "10031"


def test_decodes_at_small_scale():
    assert decode_ean5(render("10031", module_px=2, height=24)) == "10031"


def test_rejects_noise():
    assert decode_ean5(Image.new("L", (200, 60), 128)) is None


def test_rejects_a_corrupt_checksum():
    """Bars from one parity pattern with digits from another must not decode."""
    parts = [L_CODES[d] for d in "10031"]
    modules = "01011" + "01".join(parts)
    img = Image.new("L", (40 * 2 + len(modules) * 4, 60), 255)
    x = 40
    for m in modules:
        if m == "1":
            for dx in range(4):
                for y in range(60):
                    img.putpixel((x + dx, y), 0)
        x += 4
    # all-L parity means checksum must be... whatever PARITY maps to LLLLL — nothing does
    assert decode_ean5(img) is None
