# Redash + 0sql

Redash, unchanged, with one query runner added: you type a line in business
terms, [0sql](https://github.com/stratasite/0sql) plans the SQL against a
semantic model, Redash runs it and draws the chart.

```
item category, net paid, wholesale cost, discount rate, year = 2002
```

![three saved lines, three charts](docs/dashboard.jpg)

Nothing is compiled and neither project is vendored here. Both arrive
prebuilt, at the versions the demo runs:

| | |
|---|---|
| [getredash/redash](https://github.com/getredash/redash) | the official `redash/redash:26.9.0` image, unchanged |
| [stratasite/0sql](https://github.com/stratasite/0sql) | the published `zsql` release binary, v0.6.0 by default |

Everything in this repository is the glue:

| | |
|---|---|
| [`redash_zsql.py`](redash_zsql.py) | the query runner, one file, enabled with `REDASH_ADDITIONAL_QUERY_RUNNERS` |
| [`model/`](model) | the semantic model the demo queries: TPC-DS's store channel, 5 tables, 1 policy |
| [`compose.yaml`](compose.yaml), [`docker/`](docker) | Redash, zsql, Redis and Postgres as one stack |
| [`test_redash_zsql.py`](test_redash_zsql.py) | the runner's checks, which need no Redash installed |
| [`demo.sh`](demo.sh) | from nothing to a dashboard, one command |

## Run it

Needs Docker with Compose v2, python3 and openssl. Both images are pulled,
not built, so the first run is a download and a TPC-DS generation rather than
a compile.

```sh
git clone https://github.com/stratasite/redash-0sql
cd redash-0sql
./demo.sh
```

`ZSQL_VERSION` in `.env` picks the zsql
[release](https://github.com/stratasite/0sql/releases) to pull; the image is
tagged with it, so changing it swaps versions on the next `docker compose up`.

Then open http://localhost:5055 and sign in:

| who | sign-in | sees |
|---|---|---|
| admin | `admin@example.com` / `admin` | everything |
| Dana | `dana@example.com` / `demo` | cost and profit for Texas only, same queries |

The script generates a TPC-DS warehouse in DuckDB (DuckDB ships the
generator, so nothing is downloaded but an extension), seeds a dashboard with
three saved queries, and prints the links. `docker compose down -v` resets.

## What you get in Redash

**A new data source type, "0sql".** Point it at a 0sql server and a project.
The schema sidebar lists the model's dimensions and measures per table, and
autocompletes them, instead of raw warehouse columns.

**Queries in business terms.** Anything the 0sql shorthand takes:

```
quarter, net paid
customer state, net paid, customer state top 10 by net paid
item category, yoy(net paid), year between 2000 and 2002
Cost Share = [Wholesale Cost]@m / [Net Paid]@m, brand   # needs ZSQL_VERSION 0.6.1
```

Measures like `discount rate` or `average ticket` are defined once, in the model.
Joins are found, not written. A name that does not resolve comes back as an
error naming the nearest fields. A 0sql spec as a JSON object is accepted in
place of a line, which is what an agent would send.

**The SQL, every time.** The planned statement shows on the query page under
the results, so nothing is hidden. Prefix a line with `explain` to get only
the SQL, as rows.

![the line, the SQL it became, the rows](docs/query-page.jpg)

**Row-level security, which Redash does not have.** The model carries one
policy: measures tagged `finance` (wholesale cost, net profit, margin) are filtered to the customer states on the
caller's groups. The runner sends the Redash user as the 0sql security
context, and a Redash group named `state:TX` reaches the policy as that tag.
Dana, in that group, runs the same saved query as the admin and gets Texas.
Redash admins plan as system admins and bypass it.

![the same saved query, run by Dana](docs/rls.png)

```yaml
# model/security.yml
policies:
  - name: Profit By Customer State
    mode: filter_data
    triggers: { field_tags: [finance] }
    context_dimension: Customer State
    permission_resolution: { source: groups, value_from: tag, tag_key: state, unresolved: deny }
    bypass: { system_admin: true, project_admin: true }
```

## How the runner works

0sql never connects to a warehouse: it is given the shape of the tables and
returns SQL. The runner does the rest, in Redash's own terms.

| Redash hook | what the runner does |
|---|---|
| `run_query(query, user)` | POST the line (or spec) and the user's context to `/sql`, run the SQL, return rows with the SQL as the result log |
| `get_schema()` | GET `/fields`, grouped by table |
| `test_connection()` | GET `/tables`, open the warehouse |
| `should_annotate_query = False` | Redash would otherwise prefix a `/* user_id */` comment the shorthand rejects |

The security context from a Redash user:

```python
{"email": user.email,
 "system_admin": "admin" in user.permissions,
 "groups": [{"name": g.name, "tags": [g.name] if ":" in g.name else []} for g in groups]}
```

The warehouse is reached one of two ways, chosen in the data source form:
a DuckDB file the runner opens read-only (the demo), or **another Redash data
source** the SQL is handed to. The second is how it meets an existing Redash:
keep the Postgres, Snowflake or BigQuery connection you have, add a 0sql data
source that runs through it, and write the model for those tables.

## Use it with your own warehouse

1. Write a model for your tables: copy [`model/`](model) and edit. The
   [semantic model docs](https://0sql.io/docs/semantic-model/) cover
   dimensions, measures, joins and policies; `datasources.yml` names your
   dialect (13 are supported).
2. Serve it: `zsql serve` inside the model directory, or point the compose
   file's `model` volume at it.
3. In Redash, add a 0sql data source with the project uid, and either a
   DuckDB file or the name of the Redash data source to run the SQL through.

## Known limits

- Redash caches results per query text, shared by everyone who can see the
  query. A saved query's cached result was planned for whoever ran it last;
  a regional user opening it sees that result until they refresh. Per-user
  cache keys need a change in Redash, not in the runner.
- The editor highlights the line as plain text. Field names autocomplete
  from the schema; decorators and predicates do not.
- The Redash CLI's `users create --groups` wants a trailing comma in this
  version; `demo.sh` works around it.

## Layout

```
model/             the TPC-DS store model: project.yml, datasources.yml, security.yml, models/
redash_zsql.py     the query runner
test_redash_zsql.py  its checks; needs no Redash, `python3 test_redash_zsql.py`
seed_warehouse.py  TPC-DS into DuckDB, run inside the Redash image
docker/            redash.Dockerfile (official image + runner), zsql.Dockerfile (release binary)
compose.yaml       the stack; .env is written by demo.sh
demo.sh            start, seed, dashboard
```

MIT, like both projects it joins.
