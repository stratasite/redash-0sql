"""0sql query runner for Redash.

A query is a 0sql shorthand line, such as

    contact channel, aht mins, escalation rate

or a 0sql spec as a JSON object. The runner sends it to a 0sql server with the
Redash user as the security context, receives the SQL planned against the
semantic model, and hands that SQL to the Redash data source named in the
form. The planned SQL is attached as the result log, which the Redash query
page shows under the results. A query prefixed with `explain` returns the SQL
itself.

The warehouse is whatever Redash already connects to. This runner opens no
database of its own and knows no dialect: 0sql plans for the adapter named in
the model, and the Postgres, Snowflake, BigQuery or DuckDB data source you
already have runs the statement.

Row-level security comes from the model's policies and the Redash user:
a Redash group named `key:value` reaches the policy as the tag `key:value`,
and Redash admins plan as system admins.

Enable with REDASH_ADDITIONAL_QUERY_RUNNERS=redash_zsql.
"""

import json
import logging

import requests

from redash.query_runner import BaseQueryRunner, register

logger = logging.getLogger(__name__)

EXPLAIN = "explain "


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
        # 0sql plans SQL and never opens a warehouse. Redash has no slot for a
        # semantic layer, so this registers as a data source type; the name and
        # the field labels below say which half does what.
        return "0sql semantic layer"

    @classmethod
    def configuration_schema(cls):
        return {
            "type": "object",
            "properties": {
                "server": {"type": "string", "title": "0sql server URL", "default": "http://zsql:3699"},
                "api_key": {"type": "string", "title": "API key (a server with accounts on)"},
                "project": {"type": "string", "title": "Model project"},
                "branch": {"type": "string", "title": "Model branch", "default": "main"},
                "data_source": {"type": "string", "title": "Warehouse: the Redash data source that runs the SQL"},
            },
            "order": ["server", "api_key", "project", "branch", "data_source"],
            "required": ["server", "project", "branch", "data_source"],
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

    def _target(self):
        """The runner of the Redash data source that runs the SQL.

        Raises with a message naming the problem, so a misconfigured data source
        fails on the connection test rather than on the first query.
        """
        name = self.configuration.get("data_source")
        if not name:
            raise Exception("The 0sql data source needs the name of a Redash data source to run the SQL on.")
        from redash.models import DataSource

        ds = DataSource.query.filter(DataSource.name == name).first()
        if ds is None:
            raise Exception(f"No Redash data source is named {name!r}.")
        runner = ds.query_runner
        # BaseQueryRunner declares "sql"; MongoDB, Elasticsearch, JQL and the
        # YAML- and Python-based runners override it and cannot take a statement.
        syntax = getattr(runner, "syntax", "sql")
        if syntax != "sql":
            raise Exception(
                f"{name!r} is a {ds.type} data source, which takes {syntax}, not SQL. "
                "0sql plans SQL, so name a SQL data source instead."
            )
        return runner

    def execute(self, sql, user):
        """Rows for the SQL, from the Redash data source that owns the warehouse."""
        return self._target().run_query(sql, user)

    # -- Redash hooks -------------------------------------------------------

    def test_connection(self):
        self._get("/tables")
        self._target()

    def get_schema(self, get_stats=False):
        """The model's dimensions and measures per table, for the sidebar and autocomplete.

        A column carries its kind as the `type` Redash shows beside the name, so
        the sidebar reads as the model does: measure or dimension, not varchar.
        """
        schema = {}
        for f in self._get("/fields")["fields"]:
            if f.get("hidden"):
                continue
            column = {"name": f["name"], "type": f.get("kind") or ""}
            for table in f["tables"]:
                schema.setdefault(table, {"name": table, "columns": []})["columns"].append(column)
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
        if error:
            return None, f"{error}\n\nSQL from 0sql:\n{sql}"
        if isinstance(data, dict):
            data["log"] = sql.splitlines()
        return data, error


register(Zsql)
