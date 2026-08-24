"""A SQL fragment and the value it binds, kept together.

Hand-built SQL here used to be two parallel lists — `clauses` and `params` — held in
step by nothing but care. Appending a clause and forgetting its value shifts every later
value onto the wrong `?`, and SQLite does not complain about a query that is merely
wrong; it answers it. `Predicate` makes that drift unrepresentable: a fragment arrives
with its value or not at all.

This is deliberately *not* a query builder. There is no OR, no NOT, no comparison
vocabulary, no join DSL, no `IN` helper, no dialect handling. Two queries exist in this
codebase and neither needs any of it. What the caller writes is still SQL — the layer
only refuses to let a value be separated from the `?` it belongs to, and refuses to let
a value be spliced into the SQL text at all.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class Predicate:
    """A SQL fragment and the value it binds — kept together so they cannot drift."""

    sql: str
    value: object


@dataclass(frozen=True, slots=True)
class Query:
    """An immutable SELECT with its WHERE terms. Build with `where`, render with `build`."""

    select: str
    predicates: tuple[Predicate, ...] = ()
    order_by: str | None = None
    limit: int | None = None

    def where(self, sql: str, value: object) -> Query:
        """Return a new Query with one more predicate. Immutable — never mutates self."""
        return replace(self, predicates=(*self.predicates, Predicate(sql, value)))

    def build(self) -> tuple[str, list[object]]:
        """Render to (sql, params). Params are positional and always match the '?' count.

        No value reaches the SQL text: predicates carry their own `?`, and the limit is
        bound as one too, so there is no path by which caller data becomes SQL syntax.
        """
        parts = [self.select.strip()]
        params: list[object] = []

        if self.predicates:
            parts.append("WHERE " + " AND ".join(predicate.sql for predicate in self.predicates))
            params.extend(predicate.value for predicate in self.predicates)
        if self.order_by:
            parts.append(f"ORDER BY {self.order_by}")
        if self.limit is not None:
            parts.append("LIMIT ?")
            params.append(self.limit)

        return " ".join(parts), params
