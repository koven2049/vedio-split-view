ARG REGISTRY=docker.io
ARG APT_MIRROR=deb.debian.org
FROM ${REGISTRY}/library/python:3.13-slim

ARG APT_MIRROR
ARG PYPI_INDEX=https://pypi.org/simple/
ARG PYPI_TRUSTED_HOST=

RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
    sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list 2>/dev/null; \
    apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl nodejs unzip && \
    rm -rf /var/lib/apt/lists/*

# Deno: yt-dlp EJS requires Deno >=2.6.6 (Node 20 from apt is too old, EJS marks it "unsupported").
# Without a JS runtime, YouTube's n-challenge can't be solved → bot detection.
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh -s v2.8.3 && \
    deno --version

WORKDIR /app

# Layer 1: install dependencies only (cached if pyproject.toml unchanged).
# Install the build backend explicitly and disable PEP 517 build isolation:
# isolated pip subprocesses may fall back to files.pythonhosted.org, which is
# unreachable from some production networks despite the mirror configured here.
COPY pyproject.toml .
RUN pip_args="--index-url ${PYPI_INDEX}"; \
    if [ -n "${PYPI_TRUSTED_HOST}" ]; then pip_args="${pip_args} --trusted-host ${PYPI_TRUSTED_HOST}"; fi; \
    pip install --no-cache-dir ${pip_args} hatchling setuptools==80.9.0 && \
    mkdir -p src/video_split && \
    echo '__version__ = "0.0.0"' > src/video_split/__init__.py && \
    pip install --no-cache-dir --no-build-isolation ${pip_args} . && \
    rm -rf src/

# Layer 2: copy source and install package code only (fast, no dependency download).
COPY src/ src/
RUN pip install --no-cache-dir --no-build-isolation --no-deps .

RUN mkdir -p /data/tmp

ENV PORT=8080

EXPOSE $PORT

CMD ["sh", "-c", "uvicorn video_split.main:app --host 0.0.0.0 --port $PORT"]
