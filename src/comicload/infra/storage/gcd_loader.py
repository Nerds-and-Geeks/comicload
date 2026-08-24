"""Load the tables comicload needs from a Grand Comics Database MySQL dump into SQLite.

UNVERIFIED MAPPING — read before trusting a real load.
`TABLE_MAP` below was written against an *assumed* GCD schema: comics.org answered
HTTP 403 to automated access when this module was written, so no real dump was ever
inspected. Check these two things first, against the dump you actually downloaded:

    grep -i "CREATE TABLE" gcd_dump/*.sql

  1. The dump-side table names (`gcd_publisher`, `gcd_series`, `gcd_issue`) — they may
     carry a different prefix, or the data may live in differently named tables.
  2. That the columns we keep really are the FIRST N columns of each table, in the same
     order as the matching CREATE TABLE in `SCHEMA` below. This loader is positional:
     it takes the leading N values of every row and ignores the dump's column list.

If either is wrong, edit `TABLE_MAP` (and `SCHEMA`, if the shape changes) and update
`tests/fixtures/gcd_sample.sql` to match — the tests run against that fixture, so they
stay honest either way. A row with fewer values than the mapping expects raises
CatalogError instead of being padded with NULLs, so a stale mapping fails on the first
load rather than quietly filling the catalogue with garbage.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

from comicload.core.errors import CatalogError

SCHEMA = """
DROP TABLE IF EXISTS issue;
DROP TABLE IF EXISTS series;
DROP TABLE IF EXISTS publisher;

CREATE TABLE publisher (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE series (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    publisher_id INTEGER,
    year_began INTEGER
);
CREATE TABLE issue (
    id INTEGER PRIMARY KEY,
    number TEXT,
    series_id INTEGER,
    on_sale_date TEXT,
    barcode TEXT
);
CREATE INDEX idx_issue_barcode ON issue(barcode);
CREATE INDEX idx_series_name ON series(name);
CREATE INDEX idx_publisher_name ON publisher(name);
"""

# Which dump table feeds which local table, and how many leading columns we keep.
# The count must match the column order of the matching CREATE TABLE in SCHEMA.
# This is the one place to correct if the real dump disagrees — see the module docstring.
TABLE_MAP: dict[str, tuple[str, int]] = {
    "gcd_publisher": ("publisher", 2),  # -> publisher(id, name)
    "gcd_series": ("series", 4),  # -> series(id, name, publisher_id, year_began)
    "gcd_issue": ("issue", 5),  # -> issue(id, number, series_id, on_sale_date, barcode)
}

_INSERT_HEADER_RE = re.compile(
    r"INSERT\s+(?:LOW_PRIORITY\s+|DELAYED\s+|HIGH_PRIORITY\s+|IGNORE\s+)*"
    r"INTO\s+[`\"]?(\w+)[`\"]?\s*(?:\([^)]*\)\s*)?VALUES\s*",
    re.IGNORECASE,
)

# MySQL's backslash escapes. Anything else after a backslash is that literal character.
_ESCAPES = {"0": "\0", "b": "\b", "n": "\n", "r": "\r", "t": "\t", "Z": "\x1a"}


def _statement_end(text: str) -> int | None:
    """Index just past the `;` that terminates the statement, or None if it is incomplete.

    Semicolons inside quoted values do not end a statement, so the scan tracks strings.
    """
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
        elif in_string and char == "\\":
            escaped = True
        elif char == "'":
            in_string = not in_string
        elif not in_string and char == ";":
            return index + 1
    return None


def _iter_inserts(sql_path: Path) -> Iterator[tuple[str, str]]:
    """Yield (dump table name, VALUES clause) for each INSERT statement in the dump.

    The dump is streamed a line at a time — a real GCD dump is far too large to hold in
    memory — while still tolerating a statement that spans several lines.
    """
    buffer = ""
    with sql_path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if buffer:
                buffer += line
            elif line.lstrip().upper().startswith("INSERT"):
                buffer = line.lstrip()
            else:
                continue

            end = _statement_end(buffer)
            while end is not None:
                statement, buffer = buffer[:end], buffer[end:].lstrip()
                match = _INSERT_HEADER_RE.match(statement)
                if match:
                    yield match.group(1), statement[match.end() : -1]
                if not buffer.upper().startswith("INSERT"):
                    buffer = ""
                    break
                end = _statement_end(buffer)


def _value(raw: str, text: str, quoted: bool) -> str | None:
    """Turn one scanned value into what goes in the database."""
    if quoted:
        return text
    stripped = raw.strip()
    return None if stripped.upper() == "NULL" else stripped


def _split_tuples(blob: str) -> Iterator[list[str | None]]:
    """Yield each (...) group from a MySQL VALUES clause, respecting quotes and escapes."""
    values: list[str | None] = []
    raw = ""  # characters seen outside a quoted string
    text = ""  # characters seen inside a quoted string
    quoted = False
    in_string = False
    depth = 0
    index = 0
    length = len(blob)

    while index < length:
        char = blob[index]
        index += 1

        if in_string:
            if char == "\\" and index < length:
                nxt = blob[index]
                index += 1
                text += _ESCAPES.get(nxt, nxt)
            elif char == "'" and index < length and blob[index] == "'":
                index += 1
                text += "'"  # MySQL also escapes a quote by doubling it
            elif char == "'":
                in_string = False
            else:
                text += char
            continue

        if char == "'":
            in_string = True
            quoted = True
            continue
        if char == "(":
            depth += 1
            if depth == 1:
                values, raw, text, quoted = [], "", "", False
            continue
        if char == ")":
            depth -= 1
            if depth == 0:
                values.append(_value(raw, text, quoted))
                yield values
                values, raw, text, quoted = [], "", "", False
            continue
        if char == "," and depth == 1:
            values.append(_value(raw, text, quoted))
            raw, text, quoted = "", "", False
            continue
        if char == ";" and depth == 0:
            return
        if depth == 1:
            raw += char


def load_dump(
    sql_path: Path,
    db_path: Path,
    on_progress: Callable[[int], None] | None = None,
) -> dict[str, int]:
    """Load the tables comicload needs from a GCD MySQL dump into a local SQLite file.

    Returns the number of rows written per local table. Existing tables are dropped and
    rebuilt, so reloading the same dump is idempotent. `on_progress` is called with the
    number of rows in each batch as it lands — this layer never writes to the console.

    Raises CatalogError if the dump is missing, or if a mapped table's rows are narrower
    than TABLE_MAP expects (which means the mapping no longer matches the dump).
    """
    sql_path, db_path = Path(sql_path), Path(db_path)
    if not sql_path.exists():
        raise CatalogError(f"GCD dump not found: {sql_path}")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        counts = {local: 0 for local, _ in TABLE_MAP.values()}

        for source_table, blob in _iter_inserts(sql_path):
            if source_table not in TABLE_MAP:
                continue
            local_table, width = TABLE_MAP[source_table]

            rows: list[tuple[str | None, ...]] = []
            for row in _split_tuples(blob):
                if len(row) < width:
                    raise CatalogError(
                        f"{source_table}: TABLE_MAP expects at least {width} columns per row "
                        f"but this dump has {len(row)}; the mapping in "
                        f"comicload.infra.storage.gcd_loader is out of date for this dump"
                    )
                rows.append(tuple(row[:width]))

            if not rows:
                continue
            placeholders = ", ".join("?" * width)
            conn.executemany(f"INSERT OR REPLACE INTO {local_table} VALUES ({placeholders})", rows)
            counts[local_table] += len(rows)
            if on_progress:
                on_progress(len(rows))

        conn.commit()
        return counts
    finally:
        conn.close()
