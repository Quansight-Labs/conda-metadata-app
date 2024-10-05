#!/usr/bin/env bash

set -eux

pixi run -e build build-wheel
pixi install -e default --locked
pixi run -e default postinstall-production
echo "#!/bin/sh" > /entrypoint.sh
pixi shell-hook -e default -s bash >> /entrypoint.sh
echo 'exec "$@"' >> /entrypoint.sh
