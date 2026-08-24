from __future__ import annotations

import keyring
import keyring.errors

SERVICE = "comicload"


class KeyringSecretStore:
    """SecretStore backed by the OS keychain.

    `backend` accepts a dict for tests so no real keychain is touched.
    """

    def __init__(self, backend: dict[str, str] | None = None) -> None:
        self._backend = backend

    def get(self, name: str) -> str | None:
        if self._backend is not None:
            return self._backend.get(name)

        return keyring.get_password(SERVICE, name)

    def set(self, name: str, value: str) -> None:
        if self._backend is not None:
            self._backend[name] = value
            return

        keyring.set_password(SERVICE, name, value)

    def delete(self, name: str) -> None:
        if self._backend is not None:
            self._backend.pop(name, None)
            return

        try:
            keyring.delete_password(SERVICE, name)
        except keyring.errors.PasswordDeleteError:
            return
