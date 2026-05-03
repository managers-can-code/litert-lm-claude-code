#!/usr/bin/env bash
# Install litert-lm from /src (the mounted source directory).
# Per the LiteRT-LM README convention, distribution is via `uv tool install`.

set -euo pipefail

if [ ! -d /src ]; then
  echo "ERROR: /src is not mounted. Mount the LiteRT-LM source via docker run -v <path>:/src" >&2
  exit 1
fi

cd /src
uv tool install . || pip3 install --break-system-packages -e .

# Verify install
which litert-lm || { echo "ERROR: litert-lm not on PATH after install" >&2; exit 1; }
litert-lm --version
