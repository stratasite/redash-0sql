"""0sql query runner for Redash.

A query is a 0sql shorthand line, such as

    item category, net paid, net profit, year = 2002

or a 0sql spec as a JSON object. The runner sends it to a 0sql server with the
Redash user as the security context, receives the SQL planned against the
semantic model, runs that SQL on the warehouse, and returns the rows. The
planned SQL is attached as the result log, which the Redash query page shows
under the results. A query prefixed with `explain` returns the SQL itself.

The warehouse is reached one of two ways, chosen in the data source form:
a DuckDB file the runner opens read-only, or another Redash data source the
SQL is handed to, so an existing Postgres, Snowflake or BigQuery connection
in Redash serves as the warehouse.

Row-level security comes from the model's policies and the Redash user:
a Redash group named `key:value` reaches the policy as the tag `key:value`,
and Redash admins plan as system admins.

Enable with REDASH_ADDITIONAL_QUERY_RUNNERS=redash_zsql.
"""

import datetime
import decimal
import json
import logging

import requests

from redash.query_runner import BaseQueryRunner, register

logger = logging.getLogger(__name__)

try:
    import duckdb
except ImportError:  # the DuckDB option is then absent; delegation still works
    duckdb = None

EXPLAIN = "explain "

# Result column types from the Python values DuckDB returns; its cursor
# description says only NUMBER for every numeric column.
PY_TYPES = {
    bool: "boolean",
    int: "integer",
    float: "float",
    decimal.Decimal: "float",
    datetime.datetime: "datetime",
    datetime.date: "date",
}


def column_type(values):
    for v in values:
        if v is not None:
            return PY_TYPES.get(type(v), "string")
    return "string"


def plain(value):
    """A cell value Redash can serialise."""
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return value


def security_context(user):
    """The 0sql security context for a Redash user; empty for no user."""
    if user is None:
        return {}
    from redash.models import Group

    groups = Group.query.filter(Group.id.in_(user.group_ids or []))
    return {
        "email": user.email,
        "system_admin": "admin" in user.permissions,
        "groups": [{"name": g.name, "tags": [g.name] if ":" in g.name else []} for g in groups],
    }


class Zsql(BaseQueryRunner):
    # Redash would otherwise prefix every query with a /* user_id */ comment,
    # which is not a shorthand line.
    should_annotate_query = False

    @classmethod
    def name(cls):
        return "0sql"

    @classmethod
    def configuration_schema(cls):
        return {
            "type": "object",
            "properties": {
                "server": {"type": "string", "title": "0sql server URL", "default": "http://zsql:3699"},
                "api_key": {"type": "string", "title": "API key (a server with accounts on)"},
                "project": {"type": "string", "title": "Project"},
                "branch": {"type": "string", "title": "Branch", "default": "main"},
                "duckdb_file": {"type": "string", "title": "Run the SQL on this DuckDB file"},
                "data_source": {"type": "string", "title": "…or through this Redash data source"},
            },
            "order": ["server", "api_key", "project", "branch", "duckdb_file", "data_source"],
            "required": ["server", "project", "branch"],
            "secret": ["api_key"],
        }

    def __init__(self, configuration):
        super().__init__(configuration)
        self.syntax = "text"

    # -- the 0sql server ----------------------------------------------------

    def _get(self, path, **kwargs):
        c = self.configuration
        url = f"{c['server'].rstrip('/')}/projects/{c['project']}/branches/{c['branch']}{path}"
        headers = {"Authorization": c["api_key"]} if c.get("api_key") else {}
        r = requests.request("POST" if "json" in kwargs else "GET", url, headers=headers, timeout=15, **kwargs)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code >= 400:
            raise Exception(body.get("error", {}).get("message") or r.text)
        return body

    def plan(self, query, user):
        """The SQL for a shorthand line or a spec object, planned for the user."""
        q = query.strip()
        body = {"spec": json.loads(q)} if q.startswith("{") else {"expr": q}
        body["context"] = security_context(user)
        return self._get("/sql", json=body)["sql"]

    # -- the warehouse ------------------------------------------------------

    def execute(self, sql, user):
        """Rows for the SQL, from DuckDB or from the named Redash data source."""
        if self.configuration.get("duckdb_file"):
            return self._execute_duckdb(sql)
        name = self.configuration.get("data_source")
        if not name:
            return None, "The 0sql data source needs a DuckDB file or a Redash data source to run the SQL on."
        from redash.models import DataSource

        ds = DataSource.query.filter(DataSource.name == name).first()
        if ds is None:
            return None, f"No Redash data source is named {name!r}."
        return ds.query_runner.run_query(sql, user)

    def _execute_duckdb(self, sql):
        if duckdb is None:
            return None, "The duckdb package is not installed in this Redash image."
        con = duckdb.connect(self.configuration["duckdb_file"], read_only=True)
        try:
            cur = con.execute(sql)
            raw = cur.fetchall()
            columns = self.fetch_columns([(d[0], column_type(r[i] for r in raw)) for i, d in enumerate(cur.description)])
            names = [c["name"] for c in columns]
            return {"columns": columns, "rows": [dict(zip(names, map(plain, r))) for r in raw]}, None
        finally:
            con.close()

    # -- Redash hooks -------------------------------------------------------

    def test_connection(self):
        self._get("/tables")
        if self.configuration.get("duckdb_file"):
            duckdb.connect(self.configuration["duckdb_file"], read_only=True).close()

    def get_schema(self, get_stats=False):
        """The model's dimensions and measures per table, for the sidebar and autocomplete."""
        schema = {}
        for f in self._get("/fields")["fields"]:
            if f.get("hidden"):
                continue
            for table in f["tables"]:
                schema.setdefault(table, {"name": table, "columns": []})["columns"].append(f["name"])
        return list(schema.values())

    def run_query(self, query, user):
        explain = query.strip().lower().startswith(EXPLAIN)
        if explain:
            query = query.strip()[len(EXPLAIN):]
        try:
            sql = self.plan(query, user)
        except Exception as e:
            return None, f"0sql: {e}"
        logger.info("0sql planned %r as: %s", query, sql)
        if explain:
            columns = self.fetch_columns([("SQL", "string")])
            return {"columns": columns, "rows": [{"SQL": line} for line in sql.splitlines()]}, None
        try:
            data, error = self.execute(sql, user)
        except Exception as e:
            return None, f"{e}\n\nSQL from 0sql:\n{sql}"
        if isinstance(data, dict):
            data["log"] = sql.splitlines()
        return data, error


register(Zsql)
