#!/usr/bin/env bash

# Bash Script to sync explicit dependencies from pixi.lock into requirements.txt
# Note: If you add any dependencies which do have a different name on PyPI,
# you need to adjust this script to address that.
set -euo pipefail

echo "# please sync the dependencies with pyproject.toml and pixi.toml" > requirements.txt
pixi list -e default --explicit --json | jq -r '.[] | select(.name != "python") | "\(.name)==\(.version)"' >> requirements.txt
