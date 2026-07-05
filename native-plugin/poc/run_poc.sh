#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="${SCRIPT_DIR}/pipe_transport_poc.cpp"
BIN="${SCRIPT_DIR}/pipe_transport_poc"

if [[ ! -x /usr/bin/clang++ ]]; then
    echo "[run_poc] /usr/bin/clang++ not found"
    exit 1
fi

echo "[run_poc] build: clang++ -std=c++17 -O1 -pthread"
/usr/bin/clang++ -std=c++17 -O1 -pthread "$SRC" -o "$BIN"

echo "[run_poc] run: $BIN"
"$BIN"
