# conda-metadata-app
A streamlit app to query metadata from conda packages.

> [!TIP]
> A public instance of this app is available at [**conda-metadata-app.streamlit.app**](https://conda-metadata-app.streamlit.app).

![Main Screenshot](docs/assets/main_screenshot_dark.png#gh-dark-mode-only)
![Main Screenshot](docs/assets/main_screenshot_light.png#gh-light-mode-only)

## Development
Start by running `pixi run postinstall` to install the project locally.

Use `pixi run dev` to run the app in development mode, and `pixi run deploy` to run a production version of the app.

If you modified the configuration schema (see below), use `pixi run schema` to update the schema.

### Dependencies
Please note that this project defines dependencies in both a pixi project file (`pixi.toml`) and a `requirements.txt` file.
The pixi project is used for local development and the Docker image, while the `requirements.txt` file is used for the
public Streamlit cloud deployment.

## Custom Configuration

Refer to the [Configuration Documentation](docs/configuration.md) for more information on how to customize the app.

## Docker Deployment
A public Docker image of this app is available. To run the Docker app, execute the following command:

```bash
docker run -p 8080:8080 ghcr.io/ytausch/conda-metadata-app:latest
```

By default, the image uses the default configuration located at [app_config.toml](app_config.toml).

To supply a custom configuration, mount a file to `/app/app_config.toml`:

```bash
docker run -p 8080:8080 -v /path/to/app_config.toml:/app/app_config.toml ghcr.io/ytausch/conda-metadata-app:latest
```
Note that if you use environment variables or secret files for credentials, you will need to set/mount those as well.
