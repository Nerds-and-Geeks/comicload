import comicload


def test_package_exposes_version():

    assert isinstance(comicload.__version__, str)
    assert comicload.__version__
