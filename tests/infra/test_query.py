"""The point of the layer is that a fragment and its value cannot come apart."""

from comicload.infra.storage.query import Query

SELECT = "SELECT i.id FROM issue i"


def test_where_returns_a_new_query_and_leaves_the_original_alone():
    original = Query(select=SELECT)
    extended = original.where("i.number = ?", "12")

    assert original.predicates == ()
    assert extended is not original
    assert len(extended.predicates) == 1


def test_no_predicates_emits_no_where_at_all():
    sql, params = Query(select=SELECT).build()

    assert "WHERE" not in sql
    assert sql == SELECT
    assert params == []


def test_predicates_are_joined_with_and_in_insertion_order():
    sql, params = (
        Query(select=SELECT)
        .where("s.name = ?", "Alex + Ada")
        .where("i.number = ?", "2")
        .where("p.name = ?", "Image Comics")
        .build()
    )

    assert "WHERE s.name = ? AND i.number = ? AND p.name = ?" in sql
    assert params == ["Alex + Ada", "2", "Image Comics"]


def test_placeholder_count_always_equals_param_count():
    query = Query(select=SELECT)
    for count in (0, 1, 3):
        while len(query.predicates) < count:
            query = query.where("i.number = ?", str(len(query.predicates)))
        sql, params = query.build()
        assert sql.count("?") == len(params) == count


def test_order_by_and_limit_are_omitted_when_unset():
    sql, params = Query(select=SELECT).where("i.number = ?", "2").build()

    assert "ORDER BY" not in sql
    assert "LIMIT" not in sql
    assert params == ["2"]


def test_order_by_and_limit_are_appended_in_sql_order():
    query = Query(select=SELECT, order_by="i.id", limit=25)
    sql, params = query.where("i.number = ?", "2").build()

    assert sql.index("WHERE") < sql.index("ORDER BY") < sql.index("LIMIT")
    assert sql.endswith("ORDER BY i.id LIMIT ?")
    assert params == ["2", 25]
    assert sql.count("?") == len(params)


def test_a_predicate_value_never_reaches_the_sql_text():
    """The value goes in the params list or nowhere — never spliced into the statement."""
    hostile = "Bobby'); DROP TABLE issue;--"
    sql, params = Query(select=SELECT).where("s.name = ?", hostile).build()

    assert hostile not in sql
    assert "DROP" not in sql
    assert params == [hostile]
