# The official Redash image, unchanged, plus the 0sql query runner and DuckDB.
# Nothing is built from source; this layer only adds the runner to the image.
# Upstream: https://github.com/getredash/redash
FROM redash/redash:26.9.0
USER root
RUN pip install --no-cache-dir duckdb==1.3.2 \
 && mkdir /warehouse && chown redash /warehouse   # the volume takes this ownership
COPY redash_zsql.py seed_warehouse.py /app/
COPY docker/zsql.png /app/client/dist/images/db-logos/zsql.png
ENV PYTHONPATH=/app REDASH_ADDITIONAL_QUERY_RUNNERS=redash_zsql
USER redash
