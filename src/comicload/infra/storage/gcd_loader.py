"""Load the tables comicload needs from a Grand Comics Database MySQL dump into SQLite.

The mapping is **by column name**, never by position. `TABLE_MAP` names the dump table
and the columns we want out of it; where those columns sit in the dump is the dump's
business. Real GCD tables carry 20-40 columns in an order nobody promised to keep, so a
positional loader would happily read `format` as `publisher_id` and never notice.

Column names come from one of two places, in order:

  1. The column list on the INSERT statement itself
     (`INSERT INTO \\`gcd_series\\` (\\`id\\`,\\`name\\`) VALUES ...`).
  2. The `CREATE TABLE` for the same table earlier in the dump — this is what plain
     `mysqldump` output gives you, since it omits the column list from INSERTs.

If neither is available for a table we need, the load fails with a CatalogError naming
that table rather than guessing. So does a dump that names none of the columns we need,
a row whose width disagrees with its column list, and a truncated dump whose final
statement has no terminating `;`.

As a last check, the loader runs the same `issue -> series -> publisher` join the
resolver depends on. Rows loaded but nothing joining is the signature of a mapping
mismatch, and it raises rather than leaving the catalogue full of garbage that reports
plausible row counts and matches nothing.

`tests/fixtures/gcd_sample.sql` is the minimal dump shape; `gcd_realistic.sql` is a
wider one with extra and reordered columns, which a positional loader gets wrong.
"""

from __future__ import annotations

import os
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
CREATE INDEX idx_series_name ON series(name COLLATE NOCASE);
CREATE INDEX idx_issue_series ON issue(series_id);
CREATE INDEX idx_series_publisher ON series(publisher_id);
CREATE INDEX idx_publisher_name ON publisher(name COLLATE NOCASE);
"""

# Which dump table feeds which local table, and which columns we need *by name*.
# The names on the right are the dump's; their order here matches the column order of
# the matching CREATE TABLE in SCHEMA above, because that is the order we insert in.
TABLE_MAP: dict[str, tuple[str, tuple[str, ...]]] = {
    "gcd_publisher": ("publisher", ("id", "name")),
    "gcd_series": ("series", ("id", "name", "publisher_id", "year_began")),
    "gcd_issue": ("issue", ("id", "number", "series_id", "on_sale_date", "barcode")),
}

_JOIN_CHECK = """
SELECT COUNT(*)
FROM issue i
JOIN series s ON s.id = i.series_id
JOIN publisher p ON p.id = s.publisher_id
"""

_INSERT_HEADER_RE = re.compile(
    r"(?:INSERT|REPLACE)\s+(?:LOW_PRIORITY\s+|DELAYED\s+|HIGH_PRIORITY\s+|IGNORE\s+)*"
    r"INTO\s+(?:[`\"]?\w+[`\"]?\s*\.\s*)?[`\"]?(\w+)[`\"]?\s*(\([^)]*\)\s*)?VALUES\s*",
    re.IGNORECASE,
)

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+(?:TEMPORARY\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
    r"(?:[`\"]?\w+[`\"]?\s*\.\s*)?[`\"]?(\w+)[`\"]?\s*\(",
    re.IGNORECASE,
)

# The first token of a CREATE TABLE body line that introduces something other than a column.
_NOT_A_COLUMN = {
    "primary",
    "unique",
    "key",
    "index",
    "constraint",
    "foreign",
    "fulltext",
    "spatial",
    "check",
    "period",
}

_STATEMENT_STARTS = ("INSERT", "REPLACE", "CREATE TABLE", "CREATE TEMPORARY TABLE")

# MySQL's backslash escapes. Anything else after a backslash is that literal character.
_ESCAPES = {"0": "\0", "b": "\b", "n": "\n", "r": "\r", "t": "\t", "Z": "\x1a"}


def _starts_statement(text: str) -> bool:
    upper = text.lstrip().upper()
    return any(upper.startswith(start) for start in _STATEMENT_STARTS)


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


def _iter_statements(sql_path: Path) -> Iterator[str]:
    """Yield each complete CREATE TABLE / INSERT / REPLACE statement in the dump.

    The dump is streamed a line at a time — a real GCD dump is far too large to hold in
    memory — while still tolerating a statement that spans several lines. A dump that
    ends mid-statement raises rather than dropping that statement on the floor.
    """
    buffer = ""
    with sql_path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if buffer:
                buffer += line
            elif _starts_statement(line):
                buffer = line.lstrip()
            else:
                continue

            end = _statement_end(buffer)
            while end is not None:
                statement, buffer = buffer[:end], buffer[end:].lstrip()
                yield statement
                if not _starts_statement(buffer):
                    buffer = ""
                    break
                end = _statement_end(buffer)

    if buffer.strip():
        head = " ".join(buffer.split())[:80]
        raise CatalogError(
            f"{sql_path} ends in the middle of a statement (no closing ';' after "
            f"{head!r}); the download is truncated — fetch the dump again"
        )


def _split_top_level(text: str) -> list[str]:
    """Split on commas that sit outside quotes, backticks and nested parentheses."""
    parts: list[str] = []
    current = ""
    depth = 0
    in_string = False
    in_backtick = False
    escaped = False
    for char in text:
        if escaped:
            current += char
            escaped = False
        elif in_string:
            current += char
            if char == "\\":
                escaped = True
            elif char == "'":
                in_string = False
        elif in_backtick:
            current += char
            if char == "`":
                in_backtick = False
        elif char == "'":
            current += char
            in_string = True
        elif char == "`":
            current += char
            in_backtick = True
        elif char == "(":
            depth += 1
            current += char
        elif char == ")":
            depth -= 1
            current += char
        elif char == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    if current.strip():
        parts.append(current)
    return parts


_DEFINITION_NAME_RE = re.compile(r"^\s*(?:`([^`]+)`|\"([^\"]+)\"|(\w+))")


def _create_table_columns(statement: str) -> tuple[str, list[str]] | None:
    """Return (table name, column names in order) for a CREATE TABLE statement."""
    match = _CREATE_TABLE_RE.match(statement.lstrip())
    if not match:
        return None

    body = statement.lstrip()[match.end() :]
    depth = 1
    in_string = False
    escaped = False
    end = len(body)
    for index, char in enumerate(body):
        if escaped:
            escaped = False
        elif in_string:
            if char == "\\":
                escaped = True
            elif char == "'":
                in_string = False
        elif char == "'":
            in_string = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                end = index
                break

    columns: list[str] = []
    for definition in _split_top_level(body[:end]):
        name_match = _DEFINITION_NAME_RE.match(definition)
        if not name_match:
            continue
        quoted, double_quoted, bare = name_match.groups()
        if quoted or double_quoted:
            columns.append(quoted or double_quoted or "")
        elif bare and bare.lower() not in _NOT_A_COLUMN:
            columns.append(bare)
    return match.group(1), columns


def _insert_columns(raw: str | None) -> list[str] | None:
    """Column names from an INSERT's `(...)` list, or None when it has none."""
    if not raw:
        return None
    inner = raw.strip()[1:-1]
    names = [part.strip().strip("`\"' ") for part in inner.split(",")]
    names = [name for name in names if name]
    return names or None


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


def _positions(source_table: str, columns: list[str], wanted: tuple[str, ...]) -> list[int]:
    """Where each wanted column sits in this dump's column order."""
    index = {name: position for position, name in enumerate(columns)}
    missing = [name for name in wanted if name not in index]
    if missing:
        raise CatalogError(
            f"{source_table}: this dump has no column(s) named {', '.join(missing)} "
            f"(it has: {', '.join(columns) or 'none'}); the mapping in "
            f"comicload.infra.storage.gcd_loader is out of date for this dump"
        )
    return [index[name] for name in wanted]


def _check_join(conn: sqlite3.Connection, counts: dict[str, int]) -> None:
    """A load that produced rows but no joinable rows means the columns were misread."""
    if not any(counts.values()):
        raise CatalogError(
            "the dump contained no comic data comicload recognises — no publisher, "
            "series, or issue rows were found; is this really a GCD data dump?"
        )
    if not all(counts[table] for table in ("publisher", "series", "issue")):
        return
    joined: int = conn.execute(_JOIN_CHECK).fetchone()[0]
    if joined == 0:
        raise CatalogError(
            "loaded "
            + ", ".join(f"{count:,} {table}" for table, count in counts.items())
            + " rows, but not one issue joins to a series and a publisher; the column "
            "names in comicload.infra.storage.gcd_loader do not match this dump"
        )


def load_dump(
    sql_path: Path,
    db_path: Path,
    on_progress: Callable[[int], None] | None = None,
) -> dict[str, int]:
    """Load the tables comicload needs from a GCD MySQL dump into a local SQLite file.

    Returns the number of rows written per local table. Existing tables are dropped and
    rebuilt, so reloading the same dump is idempotent. `on_progress` is called with the
    number of rows in each batch as it lands — this layer never writes to the console.

    Raises CatalogError if the dump is missing or truncated, if a table we need names
    none of the columns we need, or if the loaded rows do not join — all of which mean
    the mapping no longer matches the dump.
    """
    sql_path, db_path = Path(sql_path), Path(db_path)
    if not sql_path.exists():
        raise CatalogError(f"GCD dump not found: {sql_path}")

    # Build into a scratch file and swap it in only on success, so a failed sync can
    # never leave behind an empty or half-loaded mirror in place of a working one.
    db_path.parent.mkdir(parents=True, exist_ok=True)
    scratch = db_path.with_name(db_path.name + ".syncing")
    conn = sqlite3.connect(scratch)
    try:
        conn.executescript(SCHEMA)
        counts = {local: 0 for local, _ in TABLE_MAP.values()}
        declared: dict[str, list[str]] = {}

        for statement in _iter_statements(sql_path):
            created = _create_table_columns(statement)
            if created is not None:
                declared[created[0]] = created[1]
                continue

            match = _INSERT_HEADER_RE.match(statement)
            if not match:
                continue
            source_table = match.group(1)
            if source_table not in TABLE_MAP:
                continue
            local_table, wanted = TABLE_MAP[source_table]

            columns = _insert_columns(match.group(2)) or declared.get(source_table)
            if not columns:
                raise CatalogError(
                    f"{source_table}: this INSERT names no columns and the dump holds no "
                    f"CREATE TABLE for {source_table}, so comicload cannot tell which value "
                    "is which; re-export the dump with its CREATE TABLE statements"
                )
            positions = _positions(source_table, columns, wanted)

            rows: list[tuple[str | None, ...]] = []
            for row in _split_tuples(statement[match.end() : -1]):
                if len(row) != len(columns):
                    raise CatalogError(
                        f"{source_table}: a row has {len(row)} values but the column list "
                        f"declares {len(columns)}; this dump cannot be read reliably"
                    )
                rows.append(tuple(row[position] for position in positions))

            if not rows:
                continue
            placeholders = ", ".join("?" * len(wanted))
            conn.executemany(f"INSERT OR REPLACE INTO {local_table} VALUES ({placeholders})", rows)
            counts[local_table] += len(rows)
            if on_progress:
                on_progress(len(rows))

        _check_join(conn, counts)
        conn.commit()
    except BaseException:
        conn.close()
        scratch.unlink(missing_ok=True)
        raise
    else:
        conn.close()
        os.replace(scratch, db_path)
        return counts
