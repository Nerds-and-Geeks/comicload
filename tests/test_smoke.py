def test_package_exposes_version():
    import comicload

    assert isinstance(comicload.__version__, str)
    assert comicload.__version__
