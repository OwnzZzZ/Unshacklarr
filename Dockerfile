# Unshacklarr: the web page, the schedule and Sonarr's imports. The downloads are Unshackle
# serve's job, in its own container (docker/unshackle.Dockerfile) or anywhere else.
FROM python:3.12-slim-trixie

# mkvtoolnix: joining an episode's parts and titling the files
RUN apt-get update && apt-get install -y --no-install-recommends mkvtoolnix tzdata \
    && rm -rf /var/lib/apt/lists/*

# uv installs exactly what uv.lock says, the versions the tests ran with, into the image's Python
COPY --from=ghcr.io/astral-sh/uv:0.12.22 /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/usr/local UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_CACHE=1

WORKDIR /app
# The dependencies first: a change to the code alone reuses this layer
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --locked --no-dev --no-install-project
COPY unshacklarr ./unshacklarr
RUN uv sync --locked --no-dev --no-editable

ENV UNSHACKLARR_DATA=/data DOWNLOADS=/downloads HOST=0.0.0.0 PORT=8788 PYTHONUNBUFFERED=1
EXPOSE 8788
VOLUME ["/data"]
ENTRYPOINT ["unshacklarr"]
