#!/bin/sh
set -eu
ruff check .
ruff format --check .
mypy
pytest -q -p no:cacheprovider
