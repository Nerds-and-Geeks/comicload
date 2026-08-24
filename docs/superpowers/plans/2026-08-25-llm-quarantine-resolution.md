# LLM-Assisted Quarantine Resolution — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `comicload review --llm` reads what's printed on each quarantined cover with a
vision-capable Claude model, resolves it through the existing lookup/confirm path, and
auto-confirms anything that resolves to exactly one issue — then hands whatever's left to
the ordinary interactive wizard.

**Architecture:** No new `Signal`, no new resolution logic. One new function that turns
cover pixels into a short text guess (`comicload/llm/resolver.py`), threaded through
`ConfirmService.lookup()`/`confirm()`, which already do everything else — verified today
against real quarantined pages. `signal` becomes a parameter on both so the CSV `Tags`
column correctly distinguishes `signal=llm` from `signal=human`.

**Tech Stack:** `anthropic` SDK (new optional extra `comicload[llm]`), `claude-haiku-4-5`
vision, structured tool-call output for the extraction.

## Global Constraints

- Every task ends with a green `make check` and a commit. Conventional Commits.
- No `print()`/Rich/Typer outside `comicload/cli/`.
- The `anthropic` import is lazy, inside the one function that needs it — same pattern as
  `zbar_decoder.py` and `locg_sink.py`. A missing package or missing `ANTHROPIC_API_KEY`
  raises `ComicloadError` naming the fix, never a raw traceback.
- **The LLM never constructs a `CatalogEntry` or touches the database directly.** Its only
  output is a text string, passed to `ConfirmService.lookup()` exactly as if a person had
  typed it. A hallucinated series/issue can only ever produce zero or ambiguous matches —
  never a false confident import. This is the one property every test in Task 3 exists to
  prove.
- `[llm].enabled` defaults to `false`. `--llm` is an explicit flag, never automatic.
- Do not touch `BarcodeSignal`, `IdentifyService`, or the barcode/human code paths.

---

## File Structure

```
pyproject.toml                          # + [project.optional-dependencies] llm
src/comicload/
  llm/
    __init__.py
    resolver.py        # describe_cover(image_bytes, model) -> str
  quarantine/
    service.py          # parse_query/lookup/confirm gain a `signal` parameter
  config.py              # + LlmConfig
  cli/
    app.py               # review() gains --llm
tests/
  llm/
    test_resolver.py
  quarantine/
    test_confirm.py      # extended, not replaced
  test_config.py
  cli/
    test_cli.py           # extended
```

---

### Task 1: Thread `signal` through `ConfirmService`

**Files:**
- Modify: `src/comicload/quarantine/service.py`
- Test: `tests/quarantine/test_confirm.py`

**Interfaces:**
- Consumes: existing `Candidate`, `Issue`, `IdentifyResult` (unchanged)
- Produces: `parse_query(text: str, signal: str = "human") -> tuple[Candidate, Scope] | None`;
  `ConfirmService.lookup(text: str, signal: str = "human") -> list[Issue]`;
  `ConfirmService.confirm(result: IdentifyResult, issue: Issue, signal: str = "human") -> IdentifyResult`

Purely additive — every existing call site keeps working unchanged with the default.

- [ ] **Step 1: Write the failing test**

Add to `tests/quarantine/test_confirm.py`:
```python
def test_lookup_tags_the_candidate_with_the_given_signal():
    resolver = StubResolver([ISSUE])
    service = ConfirmService(resolver, RecordingRepo())
    service.lookup("Superman #35", signal="llm")
    assert resolver.saw.signal == "llm"


def test_confirm_records_which_signal_produced_the_answer():
    repo = RecordingRepo()
    service = ConfirmService(StubResolver([ISSUE]), repo)
    quarantined = IdentifyResult("p1", "a.jpg", Bucket.UNRECOGNIZED, image=b"pixels")

    confirmed = service.confirm(quarantined, ISSUE, signal="llm")

    assert "signal=llm" in confirmed.entry.tags
    assert "conf=1.00" in confirmed.entry.tags


def test_confirm_still_defaults_to_human():
    repo = RecordingRepo()
    service = ConfirmService(StubResolver([ISSUE]), repo)
    quarantined = IdentifyResult("p1", "a.jpg", Bucket.UNRECOGNIZED, image=b"pixels")

    confirmed = service.confirm(quarantined, ISSUE)

    assert "signal=human" in confirmed.entry.tags
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/quarantine/test_confirm.py -k "tags_the_candidate or records_which_signal or still_defaults" -v`
Expected: FAIL — `lookup() got an unexpected keyword argument 'signal'`

- [ ] **Step 3: Write minimal implementation**

In `src/comicload/quarantine/service.py`:

```python
def parse_query(text: str, signal: str = "human") -> tuple[Candidate, Scope] | None:
    """Turn what a person (or the LLM step) typed into a lookup candidate plus scope."""
    match = _WITH_NUMBER.match(text)
    if match:
        candidate = Candidate(
            signal=signal,
            confidence=1.0,
            series=match.group("series"),
            issue_number=match.group("number"),
        )
        year = match.group("year")
        scope = Scope(year_from=int(year), year_to=int(year)) if year else Scope()
        return candidate, scope

    match = _SERIES_ONLY.match(text)
    if match:
        candidate = Candidate(signal=signal, confidence=1.0, series=match.group("series"))
        return candidate, Scope()

    return None
```

In `ConfirmService`:

```python
    def lookup(self, text: str, signal: str = "human") -> list[Issue]:
        """Issues matching the given text, best first. Empty if nothing matches."""
        parsed = parse_query(text, signal=signal)
        if parsed is None:
            return []
        candidate, scope = parsed
        issues = self._resolver.resolve(candidate, scope)

        seen: set[tuple[str, str, str, object]] = set()
        deduped: list[Issue] = []
        for issue in issues:
            key = (issue.publisher, issue.series, issue.issue_number, issue.on_sale_date)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(issue)
        return deduped

    def confirm(
        self, result: IdentifyResult, issue: Issue, signal: str = "human"
    ) -> IdentifyResult:
        """Record the identification. The pixels are released — the comic is known."""
        entry = issue.to_catalog_entry()
        entry = dataclasses.replace(
            entry,
            tags=f"comicload;photo={result.filename};signal={signal};conf=1.00",
        )
        confirmed = dataclasses.replace(
            result,
            bucket=Bucket.CONFIDENT,
            entry=entry,
            image=None,
        )
        self._repository.save([confirmed])
        return confirmed
```

(Delete the old bodies of `lookup`/`confirm` being replaced; the dedup block inside
`lookup` is unchanged from what's already there — only the `signal` threading is new.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/quarantine/test_confirm.py -v`
Expected: PASS — all tests in the file, old and new

- [ ] **Step 5: Commit**

```bash
git add src/comicload/quarantine/service.py tests/quarantine/test_confirm.py
git commit -m "feat(quarantine): thread signal through lookup/confirm for provenance"
```

---

### Task 2: `LlmConfig`

**Files:**
- Modify: `src/comicload/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `LlmConfig(enabled: bool = False, model: str = "claude-haiku-4-5")`;
  `Config.llm: LlmConfig`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:
```python
def test_llm_defaults_to_disabled():
    config = Config()
    assert config.llm.enabled is False
    assert config.llm.model == "claude-haiku-4-5"


def test_llm_settings_round_trip(tmp_path):
    path = tmp_path / "config.toml"
    config = Config()
    config.llm.enabled = True
    config.llm.model = "claude-opus-5"
    save_config(config, path)

    loaded = load_config(path)
    assert loaded.llm.enabled is True
    assert loaded.llm.model == "claude-opus-5"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_config.py -k llm -v`
Expected: FAIL — `Config` has no attribute `llm`

- [ ] **Step 3: Write minimal implementation**

In `src/comicload/config.py`, add:

```python
class LlmConfig(BaseModel):
    enabled: bool = False
    model: str = "claude-haiku-4-5"
    # No API-key field: ANTHROPIC_API_KEY via the SDK's own standard resolution.
    # This project deliberately has no secret-storage mechanism — one env var
    # does not justify reintroducing the keyring plumbing removed earlier.
```

Add `llm: LlmConfig = Field(default_factory=LlmConfig)` to `Config`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: PASS — all tests

- [ ] **Step 5: Commit**

```bash
git add src/comicload/config.py tests/test_config.py
git commit -m "feat(config): add [llm] settings, disabled by default"
```

---

### Task 3: `describe_cover` — the only new logic in this feature

**Files:**
- Create: `src/comicload/llm/__init__.py`, `src/comicload/llm/resolver.py`
- Test: `tests/llm/test_resolver.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: nothing from the rest of the codebase except `ComicloadError`
- Produces: `describe_cover(image_bytes: bytes, model: str) -> str` — a short text guess
  like `"Superman #27"` or `"Superman Brainiac"`, in exactly the format
  `parse_query` already accepts. Raises `ComicloadError` if the package is missing, the
  API key is missing, or the call fails.

This is the only task with real new logic, and the one place a wrong implementation could
matter — not because a bad extraction is dangerous (it just fails to resolve, per the
governing rule), but because a broken *call* (bad prompt shape, wrong content-block
format) silently produces nothing useful for every photo. Test the request construction
and error handling; do not test against the real API in the suite.

- [ ] **Step 1: Write the failing test**

`tests/llm/test_resolver.py`:
```python
from unittest.mock import MagicMock, patch

import pytest

from comicload.errors import ComicloadError
from comicload.llm.resolver import describe_cover


def _fake_response(text: str) -> MagicMock:
    response = MagicMock()
    tool_use = MagicMock()
    tool_use.type = "tool_use"
    tool_use.input = {"guess": text}
    response.content = [tool_use]
    return response


def test_describe_cover_returns_the_extracted_guess():
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _fake_response("Superman #27")

    with patch("comicload.llm.resolver._client", return_value=fake_client):
        result = describe_cover(b"fake png bytes", model="claude-haiku-4-5")

    assert result == "Superman #27"


def test_describe_cover_sends_the_image_as_a_content_block():
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _fake_response("Supergirl #5")

    with patch("comicload.llm.resolver._client", return_value=fake_client):
        describe_cover(b"fake png bytes", model="claude-haiku-4-5")

    call_kwargs = fake_client.messages.create.call_args.kwargs
    assert call_kwargs["model"] == "claude-haiku-4-5"
    content = call_kwargs["messages"][0]["content"]
    image_blocks = [b for b in content if b.get("type") == "image"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["source"]["type"] == "base64"


def test_describe_cover_raises_comicload_error_when_the_package_is_missing():
    with patch("comicload.llm.resolver._client", side_effect=ImportError("no anthropic")):
        with pytest.raises(ComicloadError, match="anthropic"):
            describe_cover(b"x", model="claude-haiku-4-5")


def test_describe_cover_raises_comicload_error_on_api_failure():
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = RuntimeError("network blew up")

    with patch("comicload.llm.resolver._client", return_value=fake_client):
        with pytest.raises(ComicloadError, match="network blew up"):
            describe_cover(b"x", model="claude-haiku-4-5")


def test_describe_cover_raises_when_no_tool_call_came_back():
    fake_client = MagicMock()
    response = MagicMock()
    response.content = []
    fake_client.messages.create.return_value = response

    with patch("comicload.llm.resolver._client", return_value=fake_client):
        with pytest.raises(ComicloadError, match="did not return"):
            describe_cover(b"x", model="claude-haiku-4-5")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/llm/test_resolver.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'comicload.llm'`

- [ ] **Step 3: Write minimal implementation**

`src/comicload/llm/__init__.py`:
```python
"""Optional LLM-assisted quarantine resolution. Requires the `comicload[llm]` extra."""
```

`src/comicload/llm/resolver.py`:
```python
"""Read what's printed on a quarantined cover, as plain text.

The result is never trusted directly — it is only ever fed into
`ConfirmService.lookup()`, exactly as if a person had typed it. A hallucinated
series or issue number can only ever produce zero or ambiguous matches there;
it never reaches CatalogEntry construction. See the design doc's "LLM proposes,
the catalogue verifies" section.
"""

from __future__ import annotations

import base64
from typing import Any

from comicload.errors import ComicloadError

_MISSING_PACKAGE = (
    "comicload cannot use the LLM step because the 'anthropic' package is not "
    "installed.\nInstall it with: pip install 'comicload[llm]'"
)

_PROMPT = (
    "This is the cover of a comic book, collected edition, or trade paperback. "
    "Read the series title and issue number exactly as printed on the cover. "
    "Call the report_cover tool with your best single guess, formatted as either "
    "'Series Name #12' when there is a visible issue number, or just 'Series Name' "
    "for a collected edition or trade with no issue number on the cover."
)

_TOOL = {
    "name": "report_cover",
    "description": "Report what is printed on the comic cover.",
    "input_schema": {
        "type": "object",
        "properties": {
            "guess": {
                "type": "string",
                "description": "'Series Name #12' or 'Series Name' with no number",
            }
        },
        "required": ["guess"],
    },
}


def _client() -> Any:
    import anthropic

    return anthropic.Anthropic()


def describe_cover(image_bytes: bytes, model: str) -> str:
    """Ask the model what's printed on this cover. Raises ComicloadError on any failure."""
    try:
        client = _client()
    except ImportError as exc:
        raise ComicloadError(_MISSING_PACKAGE) from exc

    encoded = base64.b64encode(image_bytes).decode("ascii")
    try:
        response = client.messages.create(
            model=model,
            max_tokens=256,
            tools=[_TOOL],
            tool_choice={"type": "tool", "name": "report_cover"},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": encoded,
                            },
                        },
                        {"type": "text", "text": _PROMPT},
                    ],
                }
            ],
        )
    except Exception as exc:
        raise ComicloadError(f"the LLM step failed: {exc}") from exc

    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            guess = block.input.get("guess")
            if guess:
                return str(guess)

    raise ComicloadError("the LLM did not return a usable answer for this cover")
```

Add to `pyproject.toml`, alongside the existing `[project.optional-dependencies]` block
(next to `locg`):

```toml
llm = ["anthropic>=0.40"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/llm/test_resolver.py -v`
Expected: PASS — all 5 tests

- [ ] **Step 5: Commit**

```bash
git add src/comicload/llm pyproject.toml tests/llm/test_resolver.py
git commit -m "feat(llm): add describe_cover, the only new logic this feature needs"
```

---

### Task 4: `comicload review --llm`

**Files:**
- Modify: `src/comicload/cli/app.py`
- Test: `tests/cli/test_cli.py`

**Interfaces:**
- Consumes: `describe_cover` (Task 3), `ConfirmService.lookup`/`confirm` with `signal="llm"`
  (Task 1), `config.llm` (Task 2)
- Produces: `--llm` flag on the `review` command

Before the existing interactive loop, if `--llm` is set and `config.llm.enabled` is true:
walk every pending item with an image, call `describe_cover`, `lookup()` the result with
`signal="llm"`, and `confirm()` if there is exactly one match. Print a one-line result per
photo. Anything not resolved this way falls through to the existing interactive loop
unchanged — this composes with the wizard, it does not replace it.

- [ ] **Step 1: Write the failing test**

Add to `tests/cli/test_cli.py`:
```python
def test_review_llm_confirms_exact_matches_and_leaves_the_rest_for_the_wizard(
    tmp_path, monkeypatch
):
    _, gcd_db, catalogue_db = _scan_one_unreadable_photo(tmp_path, monkeypatch)

    config = load_config(tmp_path / "missing.toml")
    config.llm.enabled = True
    monkeypatch.setattr(app_module, "load_config", lambda *a, **kw: config)
    monkeypatch.setattr(
        app_module, "describe_cover", lambda image_bytes, model: "The Punisher #12"
    )

    result = runner.invoke(
        app,
        ["review", "--db", str(gcd_db), "--catalogue-db", str(catalogue_db), "--llm"],
        input="q\n",
    )

    assert result.exit_code == 0, result.output
    assert "The Punisher #12" in result.output
    assert SqliteRepository(catalogue_db).pending_review() == []


def test_review_llm_leaves_unresolved_photos_for_the_interactive_wizard(
    tmp_path, monkeypatch
):
    _, gcd_db, catalogue_db = _scan_one_unreadable_photo(tmp_path, monkeypatch)

    config = load_config(tmp_path / "missing.toml")
    config.llm.enabled = True
    monkeypatch.setattr(app_module, "load_config", lambda *a, **kw: config)
    monkeypatch.setattr(
        app_module, "describe_cover", lambda image_bytes, model: "Nonexistent Series #99"
    )

    result = runner.invoke(
        app,
        ["review", "--db", str(gcd_db), "--catalogue-db", str(catalogue_db), "--llm"],
        input="q\n",
    )

    assert result.exit_code == 0, result.output
    assert len(SqliteRepository(catalogue_db).pending_review()) == 1


def test_review_llm_requires_it_to_be_enabled_in_config(tmp_path, monkeypatch):
    _, gcd_db, catalogue_db = _scan_one_unreadable_photo(tmp_path, monkeypatch)

    result = runner.invoke(
        app,
        ["review", "--db", str(gcd_db), "--catalogue-db", str(catalogue_db), "--llm"],
        input="q\n",
    )

    assert result.exit_code != 0
    assert "llm" in result.output.lower()
    assert "enabled" in result.output.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/cli/test_cli.py -k review_llm -v`
Expected: FAIL — `No such option: --llm`

- [ ] **Step 3: Write minimal implementation**

In `src/comicload/cli/app.py`, add the import:
```python
from comicload.llm.resolver import describe_cover
```

Add a `--llm` parameter to `review()`'s signature:
```python
    use_llm: Annotated[
        bool, typer.Option("--llm", help="Try the LLM step on the quarantine first.")
    ] = False,
```

Immediately after `pending = repository.pending_review()` and its empty-queue check,
before the `service = ConfirmService(...)` construction that already exists, insert:

```python
    if use_llm:
        if not config.llm.enabled:
            raise _fail(
                "The LLM step is not enabled. Turn it on with 'llm.enabled = true' "
                "in your settings (see 'comicload config show')."
            )
        console.print("[dim]Asking the LLM about each quarantined cover...[/dim]")
        still_pending = []
        for result in pending:
            if not result.image:
                still_pending.append(result)
                continue
            try:
                guess = describe_cover(result.image, model=config.llm.model)
            except ComicloadError as exc:
                console.print(f"  [yellow]{escape(result.filename)}: {exc}[/yellow]")
                still_pending.append(result)
                continue
            matches = service.lookup(guess, signal="llm")
            if len(matches) == 1:
                confirmed = service.confirm(result, matches[0], signal="llm")
                assert confirmed.entry is not None
                console.print(
                    f"  [green]✔ {escape(result.filename)}[/green] -> "
                    f"{escape(confirmed.entry.full_title)}"
                )
            else:
                console.print(f"  {escape(result.filename)}: read '{escape(guess)}', "
                               f"{len(matches)} match(es) — left for you")
                still_pending.append(result)
        pending = still_pending
        if not pending:
            console.print("\n[green]The LLM step resolved everything.[/green]")
            return
        console.print(f"\n[bold]{len(pending)}[/bold] left for the wizard.\n")
```

`service` here must be constructed before this block, not after — move the existing
`try: service = ConfirmService(...)` block up to sit directly above this new block,
ahead of both the LLM pass and the existing interactive loop, since both now use it.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/cli/test_cli.py -k review_llm -v`
Expected: PASS — all 3 tests

Then the full suite: `make check`
Expected: all green

- [ ] **Step 5: Commit**

```bash
git add src/comicload/cli/app.py tests/cli/test_cli.py
git commit -m "feat(cli): add --llm to review, composes with the interactive wizard"
```

---

## Self-Review

**Spec coverage.** Every design decision in the spec addition maps to a task: reuse over
new Signal (the whole plan's shape), propose/verify discipline (Task 3's docstring +
Task 1's tests proving `confirm()` never sees raw LLM output), provenance tagging (Task
1), opt-in-only config (Task 2 + Task 4's enabled-check test), the optional extra (Task
3's `pyproject.toml` change).

**Deliberately deferred, with reasons already stated in the spec:** no batching, no
retry/backoff beyond the SDK default, no cost-control machinery beyond "only runs on the
quarantine, not every photo" (which the architecture itself provides, not extra code).

**What no task in this plan touches:** `BarcodeSignal`, `IdentifyService`,
`SqliteIssueResolver`, the barcode/human parsing paths' existing behavior. Verified by
Task 1's `test_confirm_still_defaults_to_human` and the fact that every new parameter
defaults to preserving current behavior exactly.

**Risk concentrated in one place, as designed.** Task 3 is the only task with logic that
could be subtly wrong (request shape, tool-call parsing) and is also the only task fully
isolated from the database and from every other test in the suite — a bug there fails
loudly in its own tests, and even a passing-but-wrong implementation can only ever
produce bad *text*, which Task 1's tests already prove cannot become a bad *entry*.
