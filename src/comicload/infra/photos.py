from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

from comicload.core.errors import ComicloadError
from comicload.core.models import Photo
from comicload.infra.pdf import first_page_png

SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".tif", ".tiff", ".pdf"}


class LocalFolderPhotoSource:
    """Reads photos from a folder tree. Photo ids are content hashes, so duplicates collapse.

    The tree is walked once and remembered: `count()` and `photos()` are both called on
    every scan, and a shelf of photos is slow enough to walk without doing it twice.
    """

    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._cached: list[Path] | None = None

    def _paths(self) -> list[Path]:
        if self._cached is not None:
            return self._cached
        if not self._root.exists():
            raise FileNotFoundError(f"photo folder does not exist: {self._root}")
        self._cached = sorted(
            path
            for path in self._root.rglob("*")
            if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
        )
        return self._cached

    def photos(self) -> Iterator[Photo]:
        """Every distinct photo in the tree.

        Two byte-identical files are one comic photographed once, not two comics: they
        share a content hash, so the catalogue stores a single row for them. Yielding
        both would have counted the same comic twice in the scan summary.
        """
        seen: set[str] = set()
        for path in self._paths():
            raw = path.read_bytes()
            if path.suffix.lower() == ".pdf":
                # A PDF scan is a container: page one, rendered, is the cover. An
                # unreadable one is skipped rather than stopping the whole folder —
                # it will show up as the difference between count() and the summary.
                try:
                    data = first_page_png(raw)
                except ComicloadError:
                    continue
            else:
                data = raw
            photo_id = hashlib.sha256(data).hexdigest()
            if photo_id in seen:
                continue
            seen.add(photo_id)
            yield Photo(id=photo_id, data=data, filename=path.name)

    def count(self) -> int:
        """How many files will be read. An upper bound: identical copies collapse."""
        return len(self._paths())
