# syntax=docker/dockerfile:1
FROM rust:1.99.1-bookworm AS installers
RUN apt-get update && apt-get install -y --no-install-recommends git pkg-config libxkbcommon-dev libwayland-dev libx11-dev libxcb-render0-dev libxcb-shape0-dev libxcb-xfixes0-dev && rm -rf /var/lib/apt/lists/*
ARG INSTALLERS_REPOSITORY=https://github.com/rftd/ACBrInstall.git
ARG INSTALLERS_REF=97a72d9d33791a13ff077c36a76295844bae5089
WORKDIR /build/installers
RUN git init && git remote add origin "$INSTALLERS_REPOSITORY" && git fetch --depth 1 origin "$INSTALLERS_REF" && git checkout --detach FETCH_HEAD && test "$(git rev-parse HEAD)" = "$INSTALLERS_REF"
RUN --mount=type=cache,target=/usr/local/cargo/registry cargo build --release --locked -p acbrinstall -p pascalinstall
FROM debian:bookworm AS base
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 ACBR_SOURCES=/opt/acbr PASCAL_ENVIRONMENT=/opt/pascal XDG_CONFIG_HOME=/opt/user/config XDG_STATE_HOME=/opt/user/state HOME=/opt/user
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates python3 git subversion unzip zip make binutils gcc g++ patch libgtk2.0-dev libx11-dev libgdk-pixbuf2.0-dev libcairo2-dev libpango1.0-dev libxtst-dev libatk1.0-dev libglib2.0-dev libxkbcommon0 libwayland-client0 libxcb-render0 libxcb-shape0 libxcb-xfixes0 libgl1 libegl1 && rm -rf /var/lib/apt/lists/* && mkdir -p /opt/user /opt/build-state
COPY --from=installers /build/installers/target/release/acbrinstall /usr/local/bin/acbrinstall
COPY --from=installers /build/installers/target/release/pascalinstall /usr/local/bin/pascalinstall
COPY --from=installers /build/installers/LICENSE /usr/share/licenses/acbrinstall/LICENSE
WORKDIR /opt/generator
COPY acbrlibgenerator/ acbrlibgenerator/
COPY scripts/ scripts/
COPY config/ config/
COPY LICENSE README.md ./
RUN python3 scripts/acbrbuild.py --help && acbrinstall --version && pascalinstall --version
FROM base AS builder
RUN python3 scripts/bootstrap.py --profile linux64 --environment /opt/pascal --sources /opt/acbr --installers /usr/local/bin --state /opt/build-state
WORKDIR /workspace
ENTRYPOINT ["python3", "/opt/generator/scripts/acbrbuild.py"]
CMD ["inspect"]
