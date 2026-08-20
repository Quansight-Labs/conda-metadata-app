#!/usr/bin/env bash

set -eux

pixi run -e build build-wheel
pixi install -e prod --locked
pixi run -e prod postinstall-production
echo "#!/bin/sh" > /entrypoint.sh
pixi shell-hook -e prod -s bash >> /entrypoint.sh
echo 'exec "$@"' >> /entrypoint.sh

pixi run -e prod save-version-info
