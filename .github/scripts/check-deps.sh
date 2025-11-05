#!/usr/bin/env bash

# Check that all dependencies in pixi.toml are also in pyproject.toml.
# Note: If you add any dependencies which do have a different name on PyPI,
# you need to adjust this script to address that.
set -euo pipefail

contains_dependency_all=true

while read -r dependency; do
    contains_dependency=$(yq -r ".project.dependencies | map(. == \"${dependency}\") | any" pyproject.toml)
    if [[ $contains_dependency == "false" ]]; then
        echo "${dependency} not found in pyproject.toml"
        contains_dependency_all=false
    fi
done < <(yq -r '.dependencies | to_entries | .[] | select(.key != "python") | "\(.key)\(.value)"' pixi.toml)

if [[ $contains_dependency_all == "false" ]]; then
    exit 1
fi
