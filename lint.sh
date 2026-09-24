#!/bin/bash
set -e
poetry run pyright --warnings
poetry run pylint .
poetry run ruff check --no-cache
poetry run ruff check --no-cache --select I --diff
poetry run ruff format --diff
