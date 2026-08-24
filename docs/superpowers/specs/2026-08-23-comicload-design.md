# comicload — Design

**Date:** 2026-08-23
**Status:** Draft, pending review

## Problem

Cataloguing a physical comic collection by hand is slow. Photograph each cover, identify the issue, and produce a file League of Comic Geeks (LoCG) can import.

## Goals

- Batch-process a folder of cover photos into identified issues
- Produce a CSV matching LoCG's bulk-import format
- Optionally drive that CSV into LoCG directly
- Run offline with no recurring cost
- Never silently guess: unrecognized and ambiguous photos go to a review queue

## Non-goals

- **Exact variant-cover identification.** LoCG's import schema has no variant column, and their docs state matching is fuzzy "particularly in regards to variants." Even a perfect variant match is unconsumable by the target. Variant text is recorded in `Notes` for the human, not used for matching.
- Multiple covers per photo. One photo, one comic.
- A GUI. CLI only.
- Mobile app.

## Constraints

- macOS, Python 3.12, CLI
- One cover per photo
- Photos may be angled, bagged, glare-affected
- Collection era is unknown — must handle pre-barcode books, not just modern

## Precision target

| Level | Required? |
|---|---|
| Series + issue number | **Floor.** Must achieve. |
| \+ printing / release date | **Target.** Maps directly onto LoCG's `Full Title` suffix + `Release Date`. |
| \+ exact variant cover | **Dropped.** Not consumable by LoCG. |

---

## Key findings that shaped this design

### LoCG export format is wider than documented

Their docs show 7 columns. An actual export has 14:

```
Publisher Name | Series Name | Full Title | Release Date | In Collection | In Wish List | Marked Read
My Rating | Media Format | Price Paid | Date Purchased | Condition | Notes | Tags
```

We emit all 14, shaped identically to their own export, so the Default preset maps cleanly. `Notes` carries variant text. `Tags` carries provenance (source photo, identifying signal, confidence, `needs-review`), making the CSV auditable and round-trippable.

`Full Title` format: `{Series} #{issue}[ {printing qualifier}]` — e.g. `Alex + Ada #2 2nd Printing`.

### Release Date cannot come from a photograph

Comics are cover-dated roughly 2–3 months ahead of on-sale date, and vintage books print only a cover month/year. LoCG wants a specific day (`2013-12-11`).

This is only obtainable from a metadata database with on-sale date data. It makes the local GCD mirror **load-bearing**, not a cost optimization. The photo supplies identity; the database supplies the date.

### GCD is the metadata source

[Data Distribution](https://docs.comics.org/wiki/Data_Distribution) — bi-weekly full dumps, free account required, **CC BY-SA 4.0**, on-sale dates included. Loaded into local SQLite: no API key, no rate limit, no per-query cost, works offline.

Open risks:
- CC BY-SA is copyleft. Personal local mirror is fine; shipping a bundled DB attaches attribution + share-alike obligations.
- **Cover image licensing is unresolved.** Data license ≠ image license; scans are likely separate. Blocks Phase 3 only.

### LoCG has no official API

| Library | Lang | Writes? | Auth | Status |
|---|---|---|---|---|
| [Himon](https://pypi.org/project/Himon/) | Python | No | undocumented client_id/secret | Archived Mar 2026 |
| [comicgeeks](https://github.com/pruizlezcano/comicgeeks) | Python | Yes | `ci_session` cookie | Community-maintained |
| [leagueofcomicgeeks](https://github.com/alistairjcbrown/leagueofcomicgeeks) | Node | Yes | username + password | Travis CI — stale tooling |

None implements bulk CSV import (it's a web form, not a list operation). None is maintained enough to depend on for a live-account write path. They serve as reference for login/session handling only.

Integration is scoped to **driving the bulk CSV import form**, not per-issue writes. This is deliberate:
- LoCG's own import preview becomes our dry-run — confirmation-before-write comes free
- One operation, not N: no rate limiting, no partial-failure state, no idempotency bookkeeping
- Their matcher does the matching

---

## Design principles

Binding constraints on all implementation work.

| # | Principle | Consequence |
|---|---|---|
| 1 | SOLID | Every extension point is an interface; concrete types depend on abstractions |
| 2 | Configurable and extensible, **not modifiable** | Open/Closed. New signals and sinks *register*; core files are never edited to add one |
| 3 | Every commit has green tests | TDD. Test and implementation land together, atomic |
| 4 | Rich CLI experience | Progress, tables, panels, colour — but confined to the CLI adapter |
| 5 | README readable by non-technical people | Plain language, no jargon, task-oriented |
| 6 | Ready to expand as a web offering | **Hexagonal architecture — see below** |
| 7 | Link to view results after LoCG import | Import returns a URL, surfaced clickable |
| 8 | Config for LLM and other API keys | Secrets via OS keychain, never in TOML |

### Principle 6 is structural

The other seven are practices that can be adopted incrementally. This one cannot be retrofitted — it determines module boundaries from the first commit.

**Ports and adapters.** The core knows nothing about CLI, filesystem, or HTTP.

```
        ┌─────────────── adapters ───────────────┐
        │  CLI (Typer+Rich)      Web (FastAPI)   │   ← interchangeable
        └────────────────┬───────────────────────┘
                         │ calls
        ┌────────────────▼───────────────────────┐
        │            services                    │   ← orchestration, pure logic
        │   IdentifyService, ExportService       │
        └────────────────┬───────────────────────┘
                         │ depends on ports (Protocols)
        ┌────────────────▼───────────────────────┐
        │  PhotoSource  Repository  Sink         │   ← interfaces
        │  ProgressReporter  Clock  SecretStore  │
        └────────────────┬───────────────────────┘
                         │ implemented by
        ┌────────────────▼───────────────────────┐
        │  LocalFolder  SQLite  CsvSink  Rich…   │   ← infrastructure
        └────────────────────────────────────────┘
```

Rules this imposes, enforced in review:

- **No `print()` in `core/` or `services/`.** Progress is emitted through a `ProgressReporter` port. The CLI adapter implements it with Rich; a web adapter implements it by writing job state. This is the single most common way a CLI-shaped codebase becomes un-webbable.
- **No filesystem paths in services.** Photos arrive via a `PhotoSource` port. Local folder now; uploaded blob later.
- **No global config singleton.** Config is constructed at the adapter boundary and injected. A web process serves many users; a module-level singleton makes that impossible.
- **Domain types are pure data.** `Candidate`, `Issue`, `CatalogEntry` have no I/O and no framework imports.
- **Long operations are job-shaped.** `IdentifyService` processes a batch and reports progress incrementally rather than blocking until done, because a web request cannot block for a 500-photo run.
- **Storage behind a repository interface.** SQLite now, Postgres later, without touching services.

### Principle 2: registration, not modification

Adding a fourth signal must require zero edits to existing files.

```python
@register_signal("barcode")
class BarcodeSignal:
    def identify(self, photo: ProcessedPhoto, scope: Scope) -> list[Candidate]: ...
```

The registry is populated by import side-effect and read by config. Enabling or ordering signals is a config change, not a code change. Same mechanism for sinks.

### Principle 8: secrets

Keys never live in `config.toml`. A `SecretStore` port backed by the OS keychain (`keyring` — macOS Keychain) with an explicit encrypted-file fallback for headless use. The TOML holds only the *name* of the secret to look up.

This covers the LLM fallback adapter (Phase 4) and any future metadata API key.

---

## Architecture

### Core abstraction

Every recognizer implements one interface:

```python
Signal.identify(photo: ProcessedPhoto, scope: Scope) -> list[Candidate]
```

`Candidate` = issue identity + confidence + originating signal. Barcode, OCR, and cover-match are three implementations. Fusion merges candidate lists and scores agreement. Nothing else knows how any signal works.

This is what makes phasing work: each phase adds a `Signal`, and nothing upstream or downstream changes.

### Three signals, all voting

Because collection era is unknown, no single signal can be primary.

| Signal | Strength | Weakness |
|---|---|---|
| **Barcode** | Near-exact when present. GCD stores barcodes, so the decoded string is matched **directly against GCD's barcode column** — no scheme decoding on the happy path. Free, no ML. | Absent pre-~1975. Falls back to UPC-supplement decoding (publisher-specific, low confidence) when the exact barcode is not in GCD. |
| **OCR** | Works on any era. Reads masthead, issue number, price/date corner, publisher mark. | Struggles on distressed/arced/art-occluded logos. |
| **Cover match** | Works when text fails entirely. | Needs a scoped image index; licensing unresolved. |

A vintage book: OCR reads "25¢, Nov 1974, Marvel corner box" → narrows to a window; cover match picks within it. A modern book: barcode alone ends it.

### Scope hint

A global cover index is infeasible — GCD has ~2M+ issue records, and the wall is bandwidth to download that many scans, not storage (CLIP embeddings are ~2KB each).

The user declares what they are photographing:

```bash
comicload import ./photos --publisher marvel --years 1970-1985
```

Corpus shrinks from ~2M to ~20k issues. Index builds once, caches, grows as scopes are added. A wrong scope degrades to "no match, needs review" — never a silent wrong answer.

This is ergonomically honest: nobody photographs a random comic from all of history. You catalogue a longbox, and a longbox has a theme.

### Modules

Layered per principle 6. Dependencies point inward only — `core` imports nothing below it.

```
comicload/
  core/           # pure domain — no I/O, no frameworks
    models.py       Candidate, Issue, CatalogEntry, Scope, ImportResult
    ports.py        Protocols: PhotoSource, Repository, Sink, Signal,
                    ProgressReporter, SecretStore, Clock
    registry.py     @register_signal / @register_sink
  services/       # orchestration — depends only on core.ports
    identify.py     IdentifyService: photos → catalogued + review queue
    export.py       ExportService: entries → Sink → ImportResult
    catalog.py      CatalogService: GCD sync, resolve, on-sale dates
  infra/          # concrete implementations of ports
    photos/         LocalFolderPhotoSource
    preprocess/     OpenCV pipeline steps
    signals/        barcode, ocr, cover
    storage/        SQLiteRepository, GCD loader
    sinks/          CsvSink, LocgPlaywrightSink
    secrets/        KeyringSecretStore, EncryptedFileSecretStore
  adapters/
    cli/            Typer + Rich — the only place print/console lives
                    (web/ would be a sibling, not a rewrite)
```

| Component | Job | Layer |
|---|---|---|
| `models` | pure data types | core |
| `ports` | every interface in the system | core |
| `registry` | signal/sink registration by decorator | core |
| `IdentifyService` | run signals, fuse, bucket by confidence | services |
| `ExportService` | entries → sink, return `ImportResult` | services |
| `CatalogService` | GCD dump → SQLite, fuzzy resolve, on-sale dates | services |
| `preprocess` | quad detect, dewarp, deglare, CLAHE | infra |
| `signals.barcode` | pyzbar + per-publisher UPC-supplement decoder | infra |
| `signals.ocr` | PaddleOCR over 4 cover regions | infra |
| `signals.cover` | CLIP embed + scoped ANN index | infra |
| `CsvSink` | 14-column LoCG CSV writer | infra |
| `LocgPlaywrightSink` | drives bulk import, returns `view_url` | infra |
| `cli` | Typer commands, Rich rendering, review TUI | adapters |

### Data flow

```
photos/ → ingest → preprocess ─┬→ barcode ─┐
                               ├→ ocr ─────┼→ fuse → catalog resolve → confident? ─┬→ catalogue.db
                               └→ cover ───┘                                       └→ review TUI ─┘
                                                                                          ↓
                                                                                  export.csv → locg.csv
                                                                                          ↓
                                                                          export.locg (optional) → LoCG
```

---

## LoCG integration

### Why Playwright, not raw HTTP

Cookie capture requires a browser regardless. Once Playwright is present, driving the import form with it beats hand-rolling `httpx`:

| | Playwright | httpx reimplementation |
|---|---|---|
| CSRF tokens, multi-step upload state | handled — it is a real browser | must reverse-engineer each |
| Breaks when | selectors change | any form or token change |
| Speed | slow | fast |
| Failure diagnosis | screenshot | opaque HTTP error |

Import runs once per batch. At that frequency robustness beats speed, and multi-step form flows with CSRF are the worst case for raw HTTP scraping.

Installed as an optional extra — `pip install comicload[locg]` — so base install stays light for CSV-only use.

### Auth flow

`comicload config locg`:
1. Launches headed Playwright at the LoCG login page
2. User logs in themselves, in a real browser, on the real site
3. We poll for authenticated session, save Playwright `storage_state` to `~/.config/comicload/locg_state.json`, mode 0600
4. Close browser

The user's password never passes through our code. This is why the cookie/storage-state model is preferred over the Node library's username+password approach: a password in a dotfile is a password duplicated somewhere it can leak, and it does not expire on its own.

### Import flow

`--import-locg`:
1. Replay stored `storage_state` headless
2. Navigate to bulk import, upload the generated CSV, select Default preset
3. **Screenshot the preview stage, print summary, wait for explicit user confirmation**
4. On confirm, submit. On decline, abort with the CSV left on disk.
5. **Capture the resulting collection URL and return it.** `ImportResult` carries `matched`, `unmatched`, and `view_url`. The CLI adapter renders it as a clickable link; a web adapter would render it as an anchor. The URL is data returned by the service, not something the sink prints — same reason as the no-`print()` rule.

Session expiry is expected and detected: if replay lands on a login page, abort with "run `comicload config locg` again" rather than failing obscurely.

### Risks, stated plainly

- Unofficial and reverse-engineered. Breaks without notice when LoCG changes their site.
- Automating a logged-in account is a grey area under their terms. User's account, user's decision.
- CSV export is the default and always works without any of this.

---

## Configuration

`~/.config/comicload/config.toml`, written only by `comicload config`, read by everything:

```toml
[export]
sink = "csv"                  # csv | locg

[export.locg]
state_file = "~/.config/comicload/locg_state.json"   # 0600
confirm_before_import = true

[catalog]
gcd_db = "~/.local/share/comicload/gcd.sqlite"

[scan]
default_publisher = ""
default_years = ""

[signals]
enabled = ["barcode"]         # order = vote order; add "ocr", "cover" as phases land

[llm]
enabled = false               # optional Phase 4 fallback for the hard tail
provider = "anthropic"
model = "claude-haiku-4-5"
secret_name = "comicload/anthropic"   # NAME only — value lives in the keychain
```

**Secrets are never in this file.** `[llm].secret_name` is a lookup key into the `SecretStore` port, backed by the macOS Keychain via `keyring`. `comicload config keys` sets values interactively without echoing them. The LoCG session lives in `locg_state.json` at 0600 because Playwright owns that format; everything else goes to the keychain.

---

## CLI

Two verbs, two directions. `scan` produces a CSV from photos; `import` consumes a CSV into LoCG.

```bash
# setup
comicload config                         # interactive settings
comicload config locg                    # headed browser login, store session
comicload config keys                    # API keys → OS keychain
comicload catalog sync <dump-path>       # load GCD dump into local SQLite

# photos → CSV
comicload scan ./photos \
    --publisher marvel \
    --years 1970-1985 \
    --out collection.csv
comicload review                         # work the ambiguous/unrecognized queue

# CSV → LoCG
comicload import collection.csv                 # validate + preview, no upload
comicload import collection.csv --import-locg   # upload, then print view URL
```

`import` without `--import-locg` validates the CSV against the 14-column schema and prints what would be pushed. The flag makes it real. This keeps the destructive path explicitly opt-in and gives a local dry-run that needs no network or session.

---

## Error handling

Failure is the normal case, not an exception. Every photo lands in exactly one bucket:

| Bucket | Behavior |
|---|---|
| **Confident** | Auto-catalogued, tagged with signal + confidence |
| **Ambiguous** | Review queue, ranked candidates shown |
| **Unrecognized** | Review queue, raw signal output shown |

Nothing is silently dropped. Nothing is silently guessed. A signal that raises degrades to "produced no candidates" — the other two still vote.

---

## Testing

Split honestly by what can actually be tested.

| Component | Approach | Coverage ceiling |
|---|---|---|
| `export.csv` | Golden-file against the real 14-column export | Thorough |
| `signals.*` | Pure functions over a fixture photo set | Thorough |
| `catalog` | Trimmed GCD subset fixture | Thorough |
| `fuse` | Synthetic candidate lists, scoring assertions | Thorough |
| `export.locg` driver | Local mock of the import form, or Playwright HAR replay | Logic only |
| Live LoCG import | Manual dry-run smoke check | **Untestable in CI** |

The last row is not a gap to close. Nobody can unit-test someone else's website. Stating the ceiling is more useful than claiming coverage that cannot exist.

A fixture photo set spanning eras (modern barcoded, bronze-age, pre-barcode) is a prerequisite for meaningful signal tests and should be assembled before Phase 2.

---

## Phasing

Each phase adds one `Signal` or one sink behind an existing interface.

**Phase 1 — end-to-end, no ML**
`ingest` + `preprocess` + `signals.barcode` + `catalog` + `export.csv` + `config`
Ships working value for modern books immediately. Proves the CSV shape against a real LoCG import.

**Phase 2 — OCR**
`signals.ocr`. Covers barcode-less books. Requires the era-spanning fixture set.

**Phase 3 — cover match**
`signals.cover`. Covers OCR-hostile books. **Blocked on resolving cover-image licensing.**

**Phase 4 — LoCG direct import**
`export.locg` + `comicload config locg`. Independent of Phases 2–3; can land any time after Phase 1.

Phase 1 is deliberately the whole pipeline at reduced accuracy rather than a fragment at full accuracy — it exercises every interface and validates the export format, which is the assumption most expensive to get wrong.

---

## Planned: storyline completion

`comicload storylines collection.csv --out wishlist.csv`

Reads an existing collection CSV and reports which issues are missing to **complete story
arcs** — including arcs that span multiple series, e.g. a story running from Superman into
Action Comics.

### Why numeric run gaps are the wrong model

A crossover is invisible to run-gap analysis. Owning Superman #12 and Action Comics #45
tells you nothing about them being one story; the issue numbers share no relationship.

Arc membership is the correct model, and it handles cross-series arcs for free — an arc's
issue list spans series because the arc is not defined by series.

Numeric run gaps remain a *secondary* output (cheap, useful for completing a single title),
but they are not the feature.

### GCD cannot supply this — a second catalog is required

GCD is issue-level metadata: barcodes, on-sale dates, indicia. It is the right source for
*identifying* a comic and the wrong source for storylines. Its story records do not give
reliable cross-series arc membership.

Storyline completion therefore introduces a second metadata source with different
characteristics from GCD — networked, rate-limited, API-key-bearing.

**Comic Vine** is the arc source. Metron is explicitly out of scope — the `StoryArcCatalog`
port exists so a second source can be added later if coverage proves inadequate, but building
two now is speculative.

| | Comic Vine |
|---|---|
| Auth | API key, via `SecretStore` → OS keychain |
| Limit | ~200 requests/hour |
| Coverage | Broad for modern material, thin for older |

**Consequence of the 200/hr cap:** an initial arc sync for a large collection is slow — a
2,000-issue collection is roughly a 10-hour first run. Required mitigations:
- Cache arcs permanently in local SQLite. Arc definitions are effectively immutable; never
  re-query a known arc.
- Sync only arcs touching series the user actually owns, not the whole database.
- Run as a background job, never blocking a command.

### LLM-assisted arc discovery

Comic Vine's coverage is thin for older material, where arc membership is often documented in
prose (wikis, reading-order sites) rather than any structured API. An LLM with web search
closes that gap.

**The LLM proposes; GCD verifies.** This is the governing rule:

1. LLM returns a proposed issue list for an arc
2. **Every proposed issue is looked up in the local GCD mirror**
3. Issues with no GCD match are discarded, not surfaced

The LLM is never an authority on what exists — only a hypothesis generator about what belongs
together. Verification is free because GCD is local, and a hallucinated issue fails closed
rather than landing in the user's wishlist.

Results are cached permanently alongside Comic Vine's, behind the same `StoryArcCatalog` port,
so the source of an arc definition is invisible to callers.

**Surface fit:** per-query LLM latency is wrong for a CLI. The services layer is already
job-shaped (principle 6), so a web adapter runs arc discovery asynchronously without any
service change. In the CLI this is an explicit opt-in command that populates the cache, not
something that runs during `storylines`.

### Accepted costs

**This command is not offline.** Every other comicload command runs with no network. Arc
data cannot. Mitigation is caching arcs into the same local SQLite and refreshing by delta,
making it network-once rather than network-per-query — but the offline property is genuinely
broken for this one feature and is stated rather than hidden.

**The join between catalogs is the hard part.** Three vocabularies are in play: the CSV
carries LoCG's strings, GCD has its own, Metron a third. Linking them is the real work.

- **Modern comics: barcode is the join key.** GCD and Metron both carry barcodes, so the
  link is exact and reuses the Phase 1 barcode signal.
- **Pre-barcode comics:** fuzzy series + issue matching, with the same weakness as elsewhere
  in the system. Unjoined issues are reported as unresolved rather than silently skipped.

### Architecture

New port, kept separate from `IssueResolver` so each interface stays single-purpose:

```python
class StoryArcCatalog(Protocol):
    def arcs_for_issue(self, issue: Issue) -> list[StoryArc]: ...
    def issues_in_arc(self, arc_id: str) -> list[Issue]: ...
```

Implementations: `ComicVineArcCatalog`, `LlmArcCatalog` (proposals verified against GCD),
and a `CachedArcCatalog` decorator holding the local SQLite cache. Callers cannot tell which
source produced an arc. API keys go through the existing `SecretStore`
port into the OS keychain — the mechanism already specified under principle 8.

Output reuses `CsvSink` unchanged: LoCG's format carries an `In Wish List` column, so the
must-buy list is the same 14-column CSV with `In Collection=0, In Wish List=1`, importable
back into LoCG as a real wishlist.

### Dependency order

Needs the CSV format, the GCD mirror, and the barcode signal — all Phase 1. Does **not**
need OCR or cover matching. Can ship after Phase 1, ahead of Phases 2-3.

### Open sub-questions

- Comic Vine's `story_arc` endpoint shape, and whether an issue record carries arc membership
  directly or requires a second call per arc, must be confirmed before implementation — it
  determines how many requests a sync costs against the 200/hr cap.
- Arc coverage for older material is thin in Comic Vine, which is what LLM-assisted discovery
  is for. Expected behaviour when an
  owned issue belongs to no known arc: report as "no arc data", never as "complete".

---

## Open questions

1. **Cover image licensing and bulk-fetch policy.** Blocks Phase 3. `docs.comics.org` returned 403 to automated fetch; needs manual review of their terms.
2. **Publisher/series naming alignment.** GCD strings will not match LoCG's vocabulary by default (LoCG uses `Marvel Knights`, an imprint, and `The Punisher` with article). A normalization table is needed. The user's LoCG collection is currently empty, so it cannot serve as ground-truth vocabulary — the table must be built from GCD and refined against import failures.
3. **UPC supplement schemes per publisher.** Marvel and DC differ. Needs documenting from real barcodes during Phase 1.
4. **CLI naming** — `import`/`--import-locg` vs `scan`/`--push-locg`.
