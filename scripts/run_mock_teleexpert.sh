#!/usr/bin/env bash
set -euo pipefail

exec uvicorn scripts.mock_teleexpert:app --host 127.0.0.1 --port "${MOCK_TELEXPERT_PORT:-8001}"
