# zsql, built from the ./0sql submodule. One static binary: planner and server.
FROM rust:1.98.1-bookworm AS build
WORKDIR /src
COPY 0sql/ .
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/src/target \
    cargo build --release --locked -p zsql-cli && cp target/release/zsql /zsql

FROM debian:bookworm-slim
COPY --from=build /zsql /usr/local/bin/zsql
# Served from inside the model directory: the project is deployed at start
# and redeployed as its files change. --open: listen beyond the loopback
# with accounts off, which is right for a private compose network only.
WORKDIR /model
ENTRYPOINT ["zsql"]
CMD ["serve", "--bind", "0.0.0.0:3699", "--open"]
