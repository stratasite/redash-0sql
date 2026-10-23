# redash-zsql

A [Redash](https://github.com/getredash/redash) query runner that plans SQL
from a [0sql](https://github.com/stratasite/0sql) semantic model, so a query
is written in business terms instead of SQL:

```
contact channel, aht mins, escalation rate
```

0sql resolves the names against the model, finds the joins and returns the
SQL. Redash runs that SQL on a data source you already have, and shows the
planned statement under the results.

## Install

```dockerfile
FROM redash/redash:26.9.0
RUN pip install --no-cache-dir "git+https://github.com/stratasite/redash-0sql.git@v0.1.0"
ENV REDASH_ADDITIONAL_QUERY_RUNNERS=redash_zsql
```

Redash loads query runners from `REDASH_ADDITIONAL_QUERY_RUNNERS` rather than
from entry points, so the install and the variable are both needed. Set it on
the server, the worker and the scheduler, then restart those three.

You also need a 0sql server reachable from Redash, serving your model:
`zsql serve` inside the model directory.

## Use

In Redash, add a data source of type **0sql semantic layer**:

| Field | What it is |
|---|---|
| 0sql server URL | where `zsql serve` is listening |
| Project, Branch | the model to plan against |
| Warehouse | the name of the Redash data source that runs the SQL |

This runner opens no database and knows no dialect. It names an existing
Redash data source and hands it the statement, so your Postgres, Snowflake,
BigQuery or DuckDB connection is what executes. Existing data sources,
dashboards and groups are untouched.

## Row-level security

The model's policies are compiled into the SQL at plan time, using the Redash
user as the security context. A Redash group named `region:AMER` reaches a
policy as the tag `region:AMER`, so one saved query answers per user. Redash
admins plan as system admins and bypass it.

## More

A runnable demo, with a semantic model and a warehouse, is at
[stratasite/redash-0sql](https://github.com/stratasite/redash-0sql).

MIT.
