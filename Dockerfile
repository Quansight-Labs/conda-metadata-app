FROM ghcr.io/prefix-dev/pixi:0.31.0 AS build

RUN apt update
RUN apt install -y git

COPY . /app
WORKDIR /app

RUN ./docker/install.sh

FROM ubuntu:24.04 AS production

COPY --from=build /app/.pixi/envs/default /app/.pixi/envs/default
COPY --from=build /app/app_proxy.py /app
COPY --from=build /app/app_config.toml /app
COPY --from=build --chmod=0555 /entrypoint.sh /

WORKDIR /app
EXPOSE 8080
CMD ["/entrypoint.sh"]