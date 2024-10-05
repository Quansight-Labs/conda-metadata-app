#!/usr/bin/env bash

set -eux

pixi run -e build build-wheel
pixi install -e default
pixi run -e default postinstall-production
echo "#!/bin/sh" > /entrypoint.sh
pixi shell-hook -e default >> /entrypoint.sh

echo 'streamlit run --server.headless=true --global.developmentMode=false --browser.gatherUsageStats=false --server.port=8080 app_proxy.py' >> /entrypoint.sh
