# Developing comicload

## Setup

One command:

```bash
make setup
```

That creates `.venv` with Python 3.12 and installs comicload plus its dev tools
in editable mode.

Barcode reading needs the native zbar library, which pip cannot install:

```bash
brew install zbar
```

The test suite does not need zbar — the barcode tests inject a stub decoder — so
you can develop and test without it. You only need it to run against real photos.

## Everyday commands

```bash
make test        # run the suite
make lint        # ruff check + format check
make typecheck   # mypy
make check       # all three — run this before committing
```

## Running the CLI locally

```bash
make run ARGS='--help'
make run ARGS='scan ./photos --out out.csv'
```

Or activate the venv and use the installed entry point:

```bash
source .venv/bin/activate
comicload --help
```

## Architecture rules

`tests/test_architecture.py` enforces the layering, and it will fail the build if
broken:

- `core/` imports stdlib only
- `services/` imports `core/` only
- `infra/` imports `core/` only
- Only `adapters/` may import Rich or Typer, or call `print()`

Everything else reports progress through the `ProgressReporter` port. This is what
keeps a future web interface a sibling of `adapters/cli/` rather than a rewrite.

## Adding a signal or a sink

Register it — do not edit existing files:

```python
@register_signal("my_signal")
class MySignal:
    name = "my_signal"

    def identify(self, photo: Photo, scope: Scope) -> list[Candidate]: ...
```

Then enable it in config under `[signals] enabled`.

## Tests

Every commit must be green. Tests live beside the layer they cover under `tests/`.
Fixtures are in `tests/fixtures/` and are force-added past `.gitignore`, which
excludes `*.csv` and `*.sqlite` by default.
