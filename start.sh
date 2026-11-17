#!/usr/bin/env bash
set -e

npm run build --prefix dashboard
exec ./.venv-intel/bin/python -m backend.app
