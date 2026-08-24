from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

from comicload.core.models import Photo

SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".tif", ".tiff"}


class LocalFolderPhotoSource:
    """Reads photos from a folder tree. Photo ids are content hashes, so duplicates collapse."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def _paths(self) -> list[Path]:
        if not self._root.exists():
            raise FileNotFoundError(f"photo folder does not exist: {self._root}")
        return sorted(
            path
            for path in self._root.rglob("*")
            if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
        )

    def photos(self) -> Iterator[Photo]:
        for path in self._paths():
            data = path.read_bytes()
            yield Photo(id=hashlib.sha256(data).hexdigest(), data=data, filename=path.name)

    def count(self) -> int:
        return len(self._paths())
