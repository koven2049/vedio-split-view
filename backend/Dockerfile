ARG REGISTRY=docker.io
FROM ${REGISTRY}/library/python:3.13-slim

RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null; \
    apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl nodejs unzip && \
    rm -rf /var/lib/apt/lists/*

# Deno: yt-dlp EJS requires Deno >=2.6.6 (Node 20 from apt is too old, EJS marks it "unsupported").
# Without a JS runtime, YouTube's n-challenge can't be solved → bot detection.
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh -s v2.8.3 && \
    deno --version

WORKDIR /app

# Layer 1: install dependencies only (cached if pyproject.toml unchanged)
COPY pyproject.toml .
RUN mkdir -p src/video_split && \
    echo '__version__ = "0.0.0"' > src/video_split/__init__.py && \
    pip install --no-cache-dir -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com . && \
    rm -rf src/

# Layer 2: copy source and install package code only (fast, no dependency download)
COPY src/ src/
RUN pip install --no-cache-dir --no-deps .

RUN mkdir -p /data/tmp

ENV PORT=8080

EXPOSE $PORT

CMD ["sh", "-c", "uvicorn video_split.main:app --host 0.0.0.0 --port $PORT"]
