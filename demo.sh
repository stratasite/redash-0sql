#!/usr/bin/env bash
# Redash with 0sql, from nothing to a dashboard:
#   pulls the zsql release binary and the official Redash image plus the runner,
#   builds the customer-service warehouse in DuckDB from the submodule's Parquet,
#   creates an admin and a regional user, and seeds three saved queries with
#   charts on a dashboard.
# Idempotent: run it again to pick up where it stopped.
# Needs docker (compose v2), python3 and openssl, and the customer-service
# submodule: git submodule update --init (demo.sh checks).
set -euo pipefail
cd "$(dirname "$0")"

ADMIN_EMAIL=admin@example.com   ADMIN_PASSWORD=admin
REGIONAL_EMAIL=dana@example.com REGIONAL_PASSWORD=demo
REGIONAL_GROUP=region:AMER        # the policy reads it as the tag region:AMER

if [ ! -f customer-service/project.yml ]; then
  echo "The customer-service submodule is not checked out. Run:"
  echo "  git submodule update --init"; exit 1
fi

if [ ! -f .env ]; then
  sed -e "s/^REDASH_COOKIE_SECRET=.*/REDASH_COOKIE_SECRET=$(openssl rand -hex 24)/" \
      -e "s/^REDASH_SECRET_KEY=.*/REDASH_SECRET_KEY=$(openssl rand -hex 24)/" .env.example > .env
fi
set -a; source .env; set +a
REDASH="http://localhost:${REDASH_PORT:-5055}"
ZSQL="http://localhost:${ZSQL_PORT:-3699}"

json()   { python3 -c "import sys,json;d=json.load(sys.stdin);print($1)"; }
api()    { curl -sf -H "Authorization: Key $KEY" -H 'content-type: application/json' "$@"; }
manage() { docker compose run --rm -T server manage "$@" 2>&1 | grep -v '^\[' || true; }
sql()    { docker compose exec -T postgres psql -U postgres -Atc "$1"; }
wait_for() { until curl -sf "$1" >/dev/null; do sleep 2; done; }

step() { printf '\n== %s\n' "$1"; }

step "pulling and starting"
docker compose up -d --build
wait_for "$ZSQL/healthz"; wait_for "$REDASH/ping"

step "warehouse: customer-service Parquet into DuckDB"
docker compose run --rm -T --no-deps worker python /app/seed_warehouse.py /warehouse/customer_service.duckdb | grep -v '^\['

step "redash database and users"
docker compose run --rm -T --no-deps server create_db >/dev/null 2>&1 || true
sql "select 1 from users where email='$ADMIN_EMAIL'" | grep -q 1 \
  || manage users create_root "$ADMIN_EMAIL" Admin --password "$ADMIN_PASSWORD" >/dev/null
KEY=$(sql "select api_key from users where email='$ADMIN_EMAIL'")

step "data source: 0sql on the customer-service model"
DS=$(api "$REDASH/api/data_sources" | json "d[0]['id'] if d else ''" 2>/dev/null) || {
  echo "Redash cannot read its data sources. If REDASH_SECRET_KEY changed since the database was created,"
  echo "reset with: docker compose down -v && ./demo.sh"; exit 1; }
if [ -z "$DS" ]; then
  BODY=$(python3 -c 'import json;print(json.dumps({"name":"0sql: Customer Service","type":"zsql","options":{"server":"http://zsql:3699","project":"customer-service","branch":"main","duckdb_file":"/warehouse/customer_service.duckdb"}}))')
  DS=$(api -X POST "$REDASH/api/data_sources" -d "$BODY" | json "d['id']")
fi
api -X POST "$REDASH/api/data_sources/$DS/test" | json "d['message']"
api "$REDASH/api/data_sources/$DS/schema?refresh=true" >/dev/null

step "regional user: Redash group $REGIONAL_GROUP, the policy's tag"
sql "select 1 from groups where name='$REGIONAL_GROUP'" | grep -q 1 \
  || api -X POST "$REDASH/api/groups" -d "{\"name\":\"$REGIONAL_GROUP\"}" >/dev/null
GIDS=$(sql "select string_agg(id::text, ',') from groups where name in ('default','$REGIONAL_GROUP')")
sql "select 1 from users where email='$REGIONAL_EMAIL'" | grep -q 1 \
  || manage users create "$REGIONAL_EMAIL" Dana --password "$REGIONAL_PASSWORD" --groups "$GIDS," >/dev/null  # trailing comma: a redash cli quirk

step "saved queries, charts, dashboard"
if [ "$(api "$REDASH/api/dashboards" | json "d['count']")" = 0 ]; then
  chart() {  # name, line, chart title, chart options
    local body q v
    body=$(python3 -c 'import json,sys;print(json.dumps({"name":sys.argv[1],"data_source_id":int(sys.argv[2]),"query":sys.argv[3],"options":{"parameters":[]}}))' "$1" "$DS" "$2")
    q=$(api -X POST "$REDASH/api/queries" -d "$body" | json "d['id']")
    api -X POST "$REDASH/api/queries/$q" -d '{"is_draft":false}' >/dev/null
    api -X POST "$REDASH/api/queries/$q/results" -d '{"max_age":0,"parameters":{}}' >/dev/null
    api -X POST "$REDASH/api/visualizations" -d "{\"query_id\":$q,\"type\":\"CHART\",\"name\":\"$3\",\"options\":$4}" | json "d['id']"
  }
  AXES='"sortX":true,"xAxis":{"type":"category","labels":{"enabled":true}},"yAxis":[{"type":"linear"},{"type":"linear","opposite":true}]'
  V1=$(chart "Contacts by month" "month(contact date), contacts" "Contacts by month" \
       "{\"globalSeriesType\":\"column\",$AXES,\"legend\":{\"enabled\":false},\"seriesOptions\":{},\"columnMapping\":{\"Month(Contact Date)\":\"x\",\"Contacts\":\"y\"}}")
  V2=$(chart "Handle time and escalation by channel" "contact channel, aht mins, escalation rate" "By channel" \
       "{\"globalSeriesType\":\"column\",$AXES,\"legend\":{\"enabled\":true},\"seriesOptions\":{\"Escalation Rate\":{\"yAxis\":1,\"type\":\"line\"}},\"columnMapping\":{\"Contact Channel\":\"x\",\"AHT Mins\":\"y\",\"Escalation Rate\":\"y\"}}")
  V3=$(chart "Cost per contact by region" "call center region, cost per contact usd, contacts" "Cost per contact by region" \
       "{\"globalSeriesType\":\"column\",$AXES,\"legend\":{\"enabled\":true},\"seriesOptions\":{\"Contacts\":{\"yAxis\":1,\"type\":\"line\"}},\"columnMapping\":{\"Call Center Region\":\"x\",\"Cost Per Contact USD\":\"y\",\"Contacts\":\"y\"}}")
  D=$(api -X POST "$REDASH/api/dashboards" -d '{"name":"0sql on Redash"}' | json "d['id']")
  col=0
  for v in $V1 $V2 $V3; do
    api -X POST "$REDASH/api/widgets" -d "{\"dashboard_id\":$D,\"visualization_id\":$v,\"width\":1,\"text\":\"\",\"options\":{\"position\":{\"col\":$col,\"row\":0,\"sizeX\":2,\"sizeY\":8}}}" >/dev/null
    col=$((col + 2))
  done
  api -X POST "$REDASH/api/dashboards/$D" -d '{"is_draft":false}' >/dev/null
fi
D=$(api "$REDASH/api/dashboards" | json "d['results'][0]['id']")

cat <<MSG

Redash      $REDASH
  admin     $ADMIN_EMAIL / $ADMIN_PASSWORD        plans as system admin: every region
  regional  $REGIONAL_EMAIL / $REGIONAL_PASSWORD   group $REGIONAL_GROUP: cost measures filtered to AMER
  dashboard $REDASH/dashboards/$D-0sql-on-redash
0sql        $ZSQL   (try: curl $ZSQL/projects/customer-service/branches/main/fields)
MSG
