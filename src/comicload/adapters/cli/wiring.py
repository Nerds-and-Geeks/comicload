"""Composition root for optional native components.

The one place the native barcode decoder is loaded. `zbar_decoder` pulls in zbar's
shared library at import time; attempting that import here — once, at wiring time —
turns a missing library into a single "install zbar" message instead of a failure
buried in a per-photo code path.
"""

from __future__ import annotations

import os
import platform

from comicload.core.errors import ComicloadError
from comicload.infra.signals.barcode import MISSING_ZBAR, Decoder


def setup_environment() -> None:
    """Put Homebrew's lib dir on the loader path so pyzbar can find libzbar on Apple Silicon."""
    if platform.system() == "Darwin" and os.path.exists("/opt/homebrew/lib/libzbar.dylib"):
        dyld_path = os.environ.get("DYLD_LIBRARY_PATH")
        if not dyld_path or "/opt/homebrew/lib" not in dyld_path:
            os.environ["DYLD_LIBRARY_PATH"] = (
                f"{dyld_path}:/opt/homebrew/lib" if dyld_path else "/opt/homebrew/lib"
            )


setup_environment()

_decoder_fn: Decoder | None
try:
    from comicload.infra.signals.zbar_decoder import pyzbar_decoder as _decoder_fn
except (ImportError, OSError):
    _decoder_fn = None


def get_default_barcode_decoder() -> Decoder:
    """Obtain the default zbar barcode decoder, raising a friendly error if zbar is missing."""
    if _decoder_fn is None:
        raise ComicloadError(MISSING_ZBAR)
    return _decoder_fn
