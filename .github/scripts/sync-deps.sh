#!/usr/bin/env bash

# Sync explicit dependencies from pixi.lock into requirements.txt
# Note: If you add any dependencies which do have a different name on PyPI,
# you need to adjust this script to address that.
set -euo pipefail

echo "# this file is auto-generated, please make changes in pixi.toml instead" > requirements.txt
pixi list -e default --explicit --json | jq -r '.[] | select(.name != "python") | "\(.name)==\(.version)"' >> requirements.txt
