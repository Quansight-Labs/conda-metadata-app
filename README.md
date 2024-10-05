# conda-metadata-app
A streamlit app to query metadata from conda packages.

> [!TIP]
> A public instance of this app is available at [**conda-metadata-app.streamlit.app**](https://conda-metadata-app.streamlit.app).

![Main Screenshot](docs/assets/main_screenshot_dark.png#gh-dark-mode-only)
![Main Screenshot](docs/assets/main_screenshot_light.png#gh-light-mode-only)

## Development
Use `pixi run dev` to run the app in development mode, and `pixi run deploy` to run a production version of the app.

If you modified the configuration schema (see below), use `pixi run schema` to update the schema.

### Dependencies
Please note that this project defines dependencies in both a pixi project file (`pixi.toml`) and

## Custom Configuration

Refer to the [Configuration Documentation](docs/configuration.md) for more information on how to customize the app.