import pytest

from comicload.infra.photos import LocalFolderPhotoSource


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
