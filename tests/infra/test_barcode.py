import pytest

from comicload.core.errors import ComicloadError
from comicload.core.models import Photo, Scope
from comicload.infra.signals.barcode import BarcodeSignal, decode_supplement


class StubDecoder:
    """Stands in for pyzbar so tests need no native library and no real images."""

    def __init__(self, results):
        self._results = results

    def __call__(self, image_bytes: bytes):
        return self._results


def test_decode_supplement_splits_issue_and_printing():
    assert decode_supplement("00111") == ("1", "1st Printing")


def test_decode_supplement_maps_second_printing():
    assert decode_supplement("01202") == ("12", "2nd Printing")


def test_decode_supplement_rejects_wrong_length():
    assert decode_supplement("123") == (None, None)


def test_decode_supplement_rejects_non_numeric():
    assert decode_supplement("abcde") == (None, None)


def test_signal_returns_candidate_carrying_full_barcode():
    signal = BarcodeSignal(decoder=StubDecoder([("759606084570", "00111")]))
    photo = Photo(id="1", data=b"fake", filename="a.jpg")

    candidates = signal.identify(photo, Scope())

    assert candidates[0].barcode == "75960608457000111"
    assert candidates[0].issue_number == "1"
    assert candidates[0].printing == "1st Printing"
    assert candidates[0].signal == "barcode"
    assert candidates[0].confidence >= 0.9


def test_signal_also_offers_the_bare_upc_so_resolution_can_fall_back():
    """Most catalogue rows carry the bare UPC; the concatenation alone would miss them."""
    signal = BarcodeSignal(decoder=StubDecoder([("759606084570", "00111")]))

    candidates = signal.identify(Photo(id="1", data=b"fake", filename="a.jpg"), Scope())

    assert [c.barcode for c in candidates] == ["75960608457000111", "759606084570"]
    assert all(c.confidence >= 0.9 for c in candidates)


def test_barcode_without_a_supplement_is_still_an_exact_reading():
    """No EAN-5 addon is normal for pre-1990s issues, trades and one-shots."""
    signal = BarcodeSignal(decoder=StubDecoder([("759606084570", None)]))
    candidates = signal.identify(Photo(id="1", data=b"x", filename="a.jpg"), Scope())

    assert [c.barcode for c in candidates] == ["759606084570"]
    assert candidates[0].issue_number is None
    assert candidates[0].confidence >= 0.85, (
        "a perfect barcode read could never reach CONFIDENT without a supplement"
    )


def test_signal_returns_empty_when_nothing_decodes():
    signal = BarcodeSignal(decoder=StubDecoder([]))
    assert signal.identify(Photo(id="1", data=b"x", filename="a.jpg"), Scope()) == []


def test_signal_never_raises_on_decoder_failure():
    def exploding(image_bytes: bytes):
        raise RuntimeError("corrupt image")

    signal = BarcodeSignal(decoder=exploding)
    assert signal.identify(Photo(id="1", data=b"x", filename="a.jpg"), Scope()) == []


def test_signal_is_registered():
    from comicload.core.registry import available_signals

    assert "barcode" in available_signals()


# --- a missing native library is not "this photo could not be read" ------------


def test_a_signal_wired_without_a_decoder_names_what_to_install():
    """decoder=None means the native library never loaded — a wiring failure that
    would otherwise silently zero out an entire scan."""
    signal = BarcodeSignal()
    with pytest.raises(ComicloadError, match="brew install zbar"):
        signal.identify(Photo(id="1", data=b"x", filename="a.jpg"), Scope())


def test_an_unreadable_photo_still_yields_no_candidates_rather_than_raising():
    """The per-photo case must keep working: one corrupt file cannot stop a whole box."""

    def corrupt(image_bytes: bytes):
        raise OSError("cannot identify image file")

    signal = BarcodeSignal(decoder=corrupt)
    assert signal.identify(Photo(id="1", data=b"x", filename="a.jpg"), Scope()) == []
