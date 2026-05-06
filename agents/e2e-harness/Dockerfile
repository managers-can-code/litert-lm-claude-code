# Dockerfile — Agent B end-to-end integration test harness
#
# Builds an environment with:
#   - litert-lm built from the local source (mounted at /src)
#   - Claude Code CLI (latest)
#   - A small Gemma 3n model (downloaded on first build)
#   - Python deps for the run_scenarios.py harness
#
# Build:   docker build -t litert-claude-e2e .
# Run:     docker run --rm -v $(pwd)/reports:/reports -v $(pwd)/../../..:/src litert-claude-e2e
#
# The container exits with code 0 on APPROVED, non-zero on REJECTED.

FROM ubuntu:24.04

ARG MODEL_URL=""
ARG MODEL_FILENAME="gemma-3n-e2b.litertlm"

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    curl \
    ca-certificates \
    git \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI (latest stable per Anthropic install instructions)
RUN npm install -g @anthropic-ai/claude-code

# Python harness deps
RUN pip3 install --break-system-packages \
    httpx==0.27.2 \
    sse-starlette==2.1.3 \
    pytest==8.3.3

# uv for installing litert-lm per the LiteRT-LM README convention
RUN pip3 install --break-system-packages uv==0.4.18

WORKDIR /work

# litert-lm install (from the mounted source at /src)
# NOTE: this assumes /src has been mounted by docker run -v
COPY install_litert_lm.sh /work/install_litert_lm.sh
RUN chmod +x /work/install_litert_lm.sh

# Model — replace MODEL_URL via --build-arg with a real URL or mount the model
# directory at /models. Default behavior assumes the user mounts /models.
RUN mkdir -p /models

COPY run_scenarios.py /work/run_scenarios.py
COPY entrypoint.sh /work/entrypoint.sh
RUN chmod +x /work/entrypoint.sh

ENV MODEL_PATH="/models/${MODEL_FILENAME}"
ENV ANTHROPIC_BASE_URL="http://localhost:9379"
ENV ANTHROPIC_AUTH_TOKEN="any-value-server-ignores-it"

ENTRYPOINT ["/work/entrypoint.sh"]
