"""Check that a non-SQL warehouse is refused with a message naming the problem.

    python test_redash_zsql.py

Redash is not installed outside its image, so the two modules redash_zsql
imports are stubbed; nothing here reaches a server, a database or a file.
"""

import sys
import types

# -- stub enough of Redash to import the runner ----------------------------

sys.modules.setdefault("requests", types.ModuleType("requests"))

qr = types.ModuleType("redash.query_runner")


class BaseQueryRunner:
    def __init__(self, configuration):
        self.configuration = configuration
        self.syntax = "sql"


qr.BaseQueryRunner = BaseQueryRunner
qr.register = lambda cls: cls

models = types.ModuleType("redash.models")
redash = types.ModuleType("redash")
redash.query_runner, redash.models = qr, models
sys.modules.update({"redash": redash, "redash.query_runner": qr, "redash.models": models})

import redash_zsql  # noqa: E402

# -- a fake Redash data source whose runner speaks a given syntax ----------


class FakeRunner:
    def __init__(self, syntax):
        self.syntax = syntax
        self.ran = None

    def run_query(self, sql, user):
        self.ran = sql
        return {"columns": [], "rows": []}, None


class FakeDataSource:
    def __init__(self, type_, syntax):
        self.type, self.query_runner = type_, FakeRunner(syntax)


def use(ds):
    """Point the stubbed DataSource.query.filter(...).first() at ds."""
    models.DataSource = types.SimpleNamespace(
        name=None,
        query=types.SimpleNamespace(filter=lambda *a: types.SimpleNamespace(first=lambda: ds)),
    )


def runner(**config):
    """A runner whose 0sql server call is stubbed; only the warehouse side is under test."""
    r = redash_zsql.Zsql({"server": "http://x", "project": "p", "branch": "main", "data_source": "wh", **config})
    r._get = lambda *a, **k: {}
    return r


def fails_with(fn, *needles):
    try:
        fn()
    except Exception as e:
        for n in needles:
            assert n in str(e), f"{n!r} missing from: {e}"
        return str(e)
    raise AssertionError("expected an exception, got none")


# -- a Mongo data source is refused, by name, type and syntax --------------

use(FakeDataSource("mongodb", "json"))
msg = fails_with(lambda: runner().execute("SELECT 1", None), "'wh'", "mongodb", "json", "not SQL")
print("mongo refused:", msg)

# the same refusal arrives on the connection test, before any query
fails_with(runner().test_connection, "mongodb", "not SQL")

# -- every other non-SQL runner is refused too -----------------------------

for type_, syntax in [("elasticsearch", "json"), ("python", "python"), ("csv", "yaml"), ("graphite", "custom")]:
    use(FakeDataSource(type_, syntax))
    fails_with(lambda: runner().execute("SELECT 1", None), type_, "not SQL")

# -- a SQL data source still runs, and gets the statement unchanged --------

ds = FakeDataSource("pg", "sql")
use(ds)
data, error = runner().execute("SELECT 1", None)
assert error is None and ds.query_runner.ran == "SELECT 1", (data, error, ds.query_runner.ran)

# a runner that declares no syntax at all is taken as SQL, as Redash does
ds = FakeDataSource("pg", "sql")
del ds.query_runner.syntax
use(ds)
runner().execute("SELECT 1", None)
assert ds.query_runner.ran == "SELECT 1"

# -- a missing or unnamed data source is refused the same way --------------

use(None)
fails_with(lambda: runner().execute("SELECT 1", None), "No Redash data source is named 'wh'")

bare = runner(data_source="")
fails_with(bare.test_connection, "needs the name of a Redash data source")

# -- the refusal reaches the user with the planned SQL attached ------------

use(FakeDataSource("mongodb", "json"))
r = runner()
r.plan = lambda query, user: "SELECT 1\nFROM t"
data, error = r.run_query("net paid", None)
assert data is None and "mongodb" in error and "SQL from 0sql:\nSELECT 1\nFROM t" in error, error

# an error returned as a tuple by a SQL runner carries the SQL too
ds = FakeDataSource("pg", "sql")
ds.query_runner.run_query = lambda sql, user: (None, "syntax error at or near \"FROM\"")
use(ds)
r = runner()
r.plan = lambda query, user: "SELECT 1\nFROM t"
data, error = r.run_query("net paid", None)
assert data is None and "syntax error" in error and "SQL from 0sql:" in error, error

print("ok")
