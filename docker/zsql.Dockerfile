# zsql, from the published release: one static musl binary that is the whole
# product, the planner and the server, with the web app and the dialect files
# compiled in. Nothing is compiled here, so this is a download, not a build.
#
# Set ZSQL_VERSION in .env to move versions. Releases:
# https://github.com/stratasite/0sql/releases

FROM debian:bookworm-slim AS fetch
ARG ZSQL_VERSION
# Supplied by the builder; the release has a build for each of these.
ARG TARGETARCH
# One SHA256SUMS lists every build in the release; ours is checked against its line.
RUN set -eu; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl; \
    rm -rf /var/lib/apt/lists/*; \
    case "$TARGETARCH" in \
      amd64) asset=zsql-x86_64-linux ;; \
      arm64) asset=zsql-aarch64-linux ;; \
      *) echo "zsql has no release build for TARGETARCH=$TARGETARCH" >&2; exit 1 ;; \
    esac; \
    base="https://github.com/stratasite/0sql/releases/download/v${ZSQL_VERSION}"; \
    echo "fetching zsql v${ZSQL_VERSION} ($asset)"; \
    curl -fsSL --retry 3 -o /zsql "$base/$asset" || { \
      echo "No $asset in zsql release v${ZSQL_VERSION}." >&2; \
      echo "Check the tag exists: https://github.com/stratasite/0sql/releases" >&2; \
      exit 1; }; \
    curl -fsSL --retry 3 -o /SHA256SUMS "$base/SHA256SUMS"; \
    sum=$(awk -v a="$asset" '$2 ~ "^[*]?"a"$" {print $1}' /SHA256SUMS); \
    test -n "$sum" || { echo "$asset is not listed in SHA256SUMS" >&2; exit 1; }; \
    echo "$sum  /zsql" | sha256sum -c -; \
    chmod +x /zsql

FROM debian:bookworm-slim
COPY --from=fetch /zsql /usr/local/bin/zsql
# Served from inside the model directory: the project is deployed at start
# and redeployed as its files change. --open: listen beyond the loopback
# with accounts off, which is right for a private compose network only.
WORKDIR /model
ENTRYPOINT ["zsql"]
CMD ["serve", "--bind", "0.0.0.0:3699", "--open"]
