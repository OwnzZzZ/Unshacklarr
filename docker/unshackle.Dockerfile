# unshackle serve, from Unshackle's own repository: the downloads Unshacklarr asks for.
# Its config, cookies and CDMs are not baked in: mount them at /config/unshackle
# (unshackle.yaml with a serve: api_secret, Cookies/, WVDs/, PRDs/).
FROM python:3.12-slim-trixie

COPY --from=ghcr.io/astral-sh/uv:0.12.22 /uv /usr/local/bin/uv

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl ffmpeg git libmediainfo0v5 mkvtoolnix tzdata unzip \
    && rm -rf /var/lib/apt/lists/*

# Each tool at a pinned version, checked against the SHA-256 it had when pinned: this container holds
# the CDM keys. A new version needs its new sum (sha256sum of the downloaded file).
ARG SHAKA=v3.9.3
ARG SHAKA_SHA256=7a3cf35ad146fd7810b4ededab363c8a3e6121d1b2c8391f53863126186f9ee6
ARG DOVI=2.3.4
ARG DOVI_SHA256=1844258e13c26607b32224bf1fa82b595d3b35949f5467405fda560daad32b3f
ARG HDR10PLUS=1.7.2
ARG HDR10PLUS_SHA256=06385f37a639d61ba21d4be3150c863846933bc3b58110e094d8fc8f1c2249f2
ARG BENTO4=1-6-0-641
ARG BENTO4_SHA256=d48dc6b164941212e5614237b4d9aeff81d4d111ee8b1508892764078a0870e8
RUN set -eux; cd /tmp; \
    curl -fsSLo shaka "https://github.com/shaka-project/shaka-packager/releases/download/$SHAKA/packager-linux-x64"; \
    curl -fsSLo dovi.tgz "https://github.com/quietvoid/dovi_tool/releases/download/$DOVI/dovi_tool-$DOVI-x86_64-unknown-linux-musl.tar.gz"; \
    curl -fsSLo hdr.tgz "https://github.com/quietvoid/hdr10plus_tool/releases/download/$HDR10PLUS/hdr10plus_tool-$HDR10PLUS-x86_64-unknown-linux-musl.tar.gz"; \
    curl -fsSLo bento.zip "https://www.bok.net/Bento4/binaries/Bento4-SDK-$BENTO4.x86_64-unknown-linux.zip"; \
    printf '%s  %s\n' "$SHAKA_SHA256" shaka "$DOVI_SHA256" dovi.tgz "$HDR10PLUS_SHA256" hdr.tgz "$BENTO4_SHA256" bento.zip | sha256sum -c -; \
    install -m 755 shaka /usr/local/bin/shaka-packager; \
    tar xzf dovi.tgz -C /usr/local/bin; \
    tar xzf hdr.tgz -C /usr/local/bin; \
    unzip -j bento.zip '*/bin/mp4decrypt' -d /usr/local/bin; \
    rm shaka dovi.tgz hdr.tgz bento.zip; \
    chmod +x /usr/local/bin/*

# Any fork works: --build-arg UNSHACKLE_REPO=… UNSHACKLE_REF=…. Unshackle itself lacks a few routes
# Unshacklarr can use (CDM tests, remote servers, a service's questions): see the README.
ARG UNSHACKLE_REPO=https://github.com/unshackle-dl/unshackle.git
ARG UNSHACKLE_REF=main
RUN git clone --depth 1 --branch "$UNSHACKLE_REF" "$UNSHACKLE_REPO" /app
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
RUN uv sync --frozen --no-dev

# Unshackle finds its config in $XDG_CONFIG_HOME/unshackle/unshackle.yaml
ENV PATH=/opt/venv/bin:$PATH XDG_CONFIG_HOME=/config HOME=/data
EXPOSE 8786
ENTRYPOINT ["unshackle"]
CMD ["serve", "--host", "0.0.0.0", "--api-only"]
