import pytest

from comicload.core.models import Candidate, Photo, Scope
from comicload.core.registry import (
    available_signals,
    get_signal,
    register_signal,
    signal_registry,
)


@pytest.fixture(autouse=True)
def clean_registry():
    signal_registry.clear()
    yield
    signal_registry.clear()


def test_register_and_retrieve_signal():
    @register_signal("fake")
    class FakeSignal:
        name = "fake"

        def identify(self, photo: Photo, scope: Scope) -> list[Candidate]:
            return [Candidate(signal="fake", confidence=1.0)]

    assert "fake" in available_signals()
    instance = get_signal("fake")
    assert instance.identify(Photo(id="1", data=b"", filename="a.jpg"), Scope())[0].signal == "fake"


def test_unknown_signal_raises_with_helpful_message():
    with pytest.raises(KeyError) as exc:
        get_signal("nope")
    assert "nope" in str(exc.value)


def test_duplicate_registration_is_rejected():
    @register_signal("dupe")
    class One:
        name = "dupe"

        def identify(self, photo: Photo, scope: Scope) -> list[Candidate]:
            return []

    with pytest.raises(ValueError, match="already registered"):

        @register_signal("dupe")
        class Two:
            name = "dupe"

            def identify(self, photo: Photo, scope: Scope) -> list[Candidate]:
                return []
