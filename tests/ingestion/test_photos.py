from pathlib import Path

import pytest

from comicload.ingestion.photos import LocalFolderPhotoSource


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"first")
    (tmp_path / "b.JPEG").write_bytes(b"second")
    (tmp_path / "notes.txt").write_bytes(b"ignore me")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "c.png").write_bytes(b"third")
    return tmp_path


def test_finds_images_recursively_and_skips_other_files(folder):
    source = LocalFolderPhotoSource(folder)
    names = sorted(photo.filename for photo in source.photos())
    assert names == ["a.jpg", "b.JPEG", "c.png"]


def test_count_matches_photos(folder):
    source = LocalFolderPhotoSource(folder)
    assert source.count() == len(list(source.photos()))


def test_id_is_content_hash_so_duplicates_collide(tmp_path):
    (tmp_path / "one.jpg").write_bytes(b"same bytes")
    (tmp_path / "two.jpg").write_bytes(b"same bytes")
    ids = {photo.id for photo in LocalFolderPhotoSource(tmp_path).photos()}
    assert len(ids) == 1


def test_missing_folder_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        LocalFolderPhotoSource(tmp_path / "nope").count()


# --- one comic, photographed once, is one photo ------------------------------


def test_byte_identical_files_yield_a_single_photo(tmp_path):
    """Two copies of one file are one comic; yielding both counted it twice."""
    (tmp_path / "one.jpg").write_bytes(b"same bytes")
    (tmp_path / "two.jpg").write_bytes(b"same bytes")
    (tmp_path / "three.jpg").write_bytes(b"different")

    photos = list(LocalFolderPhotoSource(tmp_path).photos())

    assert len(photos) == 2
    assert len({photo.id for photo in photos}) == 2


def test_the_tree_is_walked_only_once(folder, monkeypatch):
    """count() and photos() are both called on every scan; walking twice doubles the wait."""

    walks = []
    real_rglob = Path.rglob

    def counting_rglob(self, pattern, *args, **kwargs):
        walks.append(self)
        return real_rglob(self, pattern, *args, **kwargs)

    monkeypatch.setattr(Path, "rglob", counting_rglob)

    source = LocalFolderPhotoSource(folder)
    source.count()
    assert len(walks) == 1


def test_photo_source_accepts_a_single_file_target(tmp_path):
    single_file = tmp_path / "cover.jpg"
    single_file.write_bytes(b"image bytes")
    source = LocalFolderPhotoSource(single_file)
    assert source.count() == 1
    photos = list(source.photos())
    assert len(photos) == 1
    assert photos[0].filename == "cover.jpg"
