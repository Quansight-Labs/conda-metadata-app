#!/usr/bin/env bash

# Bash Script to sync explicit dependencies from pixi.lock into requirements.txt
set -euo pipefail

echo "# please sync the dependencies with pyproject.toml and pixi.toml" > requirements.txt
pixi list --explicit --json | yq -r '.[] | select(.name != "python") | "\(.name)==\(.version)"' >> requirements.txt
