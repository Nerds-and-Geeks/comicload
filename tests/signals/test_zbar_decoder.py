"""pyzbar_decoder's symbol-selection logic, tested without touching the native
library — decode() itself is monkeypatched with fake symbols shaped like pyzbar's
own Decoded/Rect, so this runs in any environment, zbar installed or not.
"""

from dataclasses import dataclass, field
from typing import Any

import pytest

import comicload.signals.zbar_decoder as zd


@dataclass
class FakeRect:
    left: int
    top: int
    width: int
    height: int


@dataclass
class FakeSymbol:
    data: bytes
    type: str
    rect: FakeRect = field(default_factory=lambda: FakeRect(0, 0, 10, 10))


@pytest.fixture
def fake_decode(monkeypatch):
    """Replace pyzbar.decode with one that returns a fixed symbol list once,
    then nothing — matching the real function's "found on first try" shape."""
    calls: list[Any] = []

    def install(symbols):
        def decode(image):
            calls.append(image)
            return symbols if len(calls) == 1 else []

        monkeypatch.setattr(zd.pyzbar, "decode", decode)

    return install


def test_a_genuine_ean13_is_read_as_the_main_barcode(fake_decode):
    fake_decode([FakeSymbol(data=b"0761941343884", type="EAN13")])
    result = zd.pyzbar_decoder(_tiny_png())
    assert result == [("0761941343884", None)]


def test_a_spurious_other_symbology_does_not_overwrite_the_real_barcode(fake_decode):
    """Found against a real scan: pyzbar read a genuine EAN13 and, from the same
    page, a spurious Interleaved-2-of-5 code from unrelated print texture. Only
    format real comic barcodes use may become the main code."""
    fake_decode(
        [
            FakeSymbol(data=b"0761941379500", type="EAN13"),
            FakeSymbol(data=b"63461644", type="I25"),
        ]
    )
    result = zd.pyzbar_decoder(_tiny_png())
    assert result == [("0761941379500", None)]


def test_a_symbol_of_an_untrusted_type_alone_yields_nothing(fake_decode):
    fake_decode([FakeSymbol(data=b"63461644", type="I25")])
    assert zd.pyzbar_decoder(_tiny_png()) == []


def _tiny_png() -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (20, 20), (255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()
