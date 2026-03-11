ARG REGISTRY=docker.io
FROM ${REGISTRY}/library/python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg curl nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Layer 1: install dependencies only (cached if pyproject.toml unchanged)
COPY pyproject.toml .
RUN mkdir -p src/video_split && \
    echo '__version__ = "0.0.0"' > src/video_split/__init__.py && \
    pip install --no-cache-dir . && \
    rm -rf src/

# Layer 2: copy source and install package code only (fast, no dependency download)
COPY src/ src/
RUN pip install --no-cache-dir --no-deps .

RUN mkdir -p /data/tmp

EXPOSE 8080

CMD ["uvicorn", "video_split.main:app", "--host", "0.0.0.0", "--port", "8080"]
