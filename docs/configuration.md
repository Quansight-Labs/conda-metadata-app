# Configure `app_config.toml`

The `app_config.toml` is a configuration file that is used to setup the `conda-metadata-app` which allows you to browse metadata of conda packages. 

## Global Configuration
### File Path Search
- `enable_filepath_search`: This feature flag decides whether the file path search feature should be enabled.

The file path search is reachable from the left sidebar.

It sends a filepath you enter to an API provided by Quansight, which contain a file matching the filepath.
This feature is useful for finding conda packages that contain a specific file.

```toml
enable_filepath_search = true  # set to false to disable
```

If enabled, the file path search looks like this:

![File Path Search](assets/filepath_search_enabled_dark.png#gh-dark-mode-only)
![File Path Search](assets/filepath_search_enabled_light.png#gh-light-mode-only)

Due to limitations of Streamlit, disabling this feature will not remove the search bar from the sidebar.
Instead, it will show a message that the feature is disabled:

![File Path Search Disabled](assets/filepath_search_disabled_dark.png#gh-dark-mode-only)
![File Path Search Disabled](assets/filepath_search_disabled_light.png#gh-light-mode-only)

## Configuring Channels

Channels are package hosts available for browsing via the application. Every channel is added under the `[channels]` section and have their unique subsections, defined as `[channels.<channel_name>]`, where `<channel_name>` is a custom name that you provide, which will appear in the app and act as the identifier for that channel.

```toml
[channels.conda-forge]
url = "https://conda.anaconda.org/conda-forge"
rss_enabled = true
package_discovery = "channeldata"
artifact_discovery = "anaconda"
...
```
In this example, `conda-forge` is the custom name that will appear in the app. Channel names containing `/` must be enclosed in double quotes:

```toml
[channels."pkgs/main"]
url = "https://repo.anaconda.com/pkgs/main"
rss_enabled = true
package_discovery = "channeldata"
artifact_discovery = "anaconda"
```
The above example shows a channel with `pkgs/main` as its custom name.

### Properties
#### Channel URL
`url`: The URL for the channel.
```toml
url = "https://conda.anaconda.org/conda-forge"
# or
url = "https://my-quetz-server.com/get/channel-name"
# or
url = "https://company-name.jfrog.io/artifactory/api/conda/channel-name"
# or
url = "https://repo.prefix.dev/channel-name"
```

#### RSS Feed
`rss_enabled`: If set to `true`, it enables the RSS feed for this channel.
```toml
rss_enabled = true
```

The RSS feed is shown by default if a channel in the left sidebar is selected. It displays
the latest artifacts uploaded to the channel and looks like this:

![RSS Feed](assets/rss_dark.png#gh-dark-mode-only)
![RSS Feed](assets/rss_light.png#gh-light-mode-only)

If you enable this feature, make sure your channel supplies an RSS feed at `<channel_url>/rss.xml`.

#### Package Discovery

`package_discovery`: This determines the method for discovering package names in the channel. Accepts two options:
- `channeldata`: Discover packages using channeldata.json
- `repodata`: Discover packages using repodata.json

```toml
package_discovery = "channeldata"
```

In general, `channeldata` is the preferred option as it is more efficient.
However, some conda package hosters (e.g., Artifactory) may not provide the `channeldata.json` file,
in which case you can use `repodata` to discover packages.

#### Artifact Discovery

`artifact_discovery`: Determines the method for discovering package artifacts in the channel. Accepts two options:
- `anaconda`: Discover artifacts using the Anaconda API. This only works if the package is hosted on Anaconda and the channel is public.
- `repodata`: Discover artifacts using repodata.json
```toml
artifact_discovery = "anaconda"
```

#### Arch Subdirectory (Platform) Discovery

`arch_subdir_discovery`: Determines how to discover all available architectures. Possible choices are:
- `channeldata`: Use channeldata.json to discover architectures.
 `all`: Try all well-known architectures.
- A list of architectures to try within a `subdirs` key.
```toml
arch_subdir_discovery = "all"
# or
arch_subdir_discovery = { subdirs = ["linux-64", "osx-64", "win-64"] }
```

Recommended to use `channeldata` if available, as it is more efficient.

#### Repodata Patches
  
`repodata_patches_package`: Specifies a package that includes patches for "repodata". It is expected to be available in the channel.
```toml
repodata_patches_package = "conda-forge-repodata-patches"
```

#### Package Name to Feedstock Mapping

`map_conda_forge_package_to_feedstock`: This is a flag used for enabling package name to feedstock name mapping for the `conda-forge` channel.
If false, the package name is used as the feedstock name. If true, conda-forge's [feedstock-outputs](https://github.com/conda-forge/feedstock-outputs)
is used to map the package name to the feedstock name.
```toml
map_conda_forge_package_to_feedstock = true
```

#### Provenance URL Pattern

`provenance_url_pattern`: This provides a URL pattern to link to the source of a package. The `{feedstock}` placeholder is replaced with the feedstock name.
```toml
provenance_url_pattern = "https://github.com/conda-forge/{feedstock}-feedstock"
```

#### Package Filter

`package_filter`: This property provides mechanism to filter packages by name or prefix.
```toml
# allows packages with names "r", "rpy2", "rstudio" and prefixes "r-", "_r-", "mro-"
package_filter = { allowed_names = ["r", "rpy2", "rstudio"], allowed_prefixes = ["r-", "_r-", "mro-"] }
```

#### Broken Label Support

`supports_broken_label`: This flag indicates that the channel supports a label called "broken" for releases that are not working (yanked).
```toml
supports_broken_label = true
```

#### Dashboards

`dashboards`: List of dashboards that should be linked from the package browser. Dashboards are defined in the global `dashboards` section (see below).
```toml
dashboards = ["anaconda", "ghcr"]
```

#### Authentication

`auth_username`, `auth_password`, `auth_quetz_token`, `auth_bearer_token`: Use these to configure authentication for the channel.

```toml
# HTTP Basic Authentication (for example Artifactory)
auth_username = "username"
auth_password = "password"
# or
# Quetz Token Authentication
auth_quetz_token = "token"
# or
# Bearer Token Authentication (for prefix.dev)
auth_bearer_token = "pfx_token"
```

You can also configure those values to read from an environment variable or a secrets file. For example:
```toml
auth_username = { env = "ARTIFACTORY_USERNAME" }    # read from environment variable
auth_password = { file = "/var/secrets/artifactory_password" }  # read password from file
```

#### Metadata Retrieval

`metadata_retrieval`: This key indicates how metadata for a package will be retrieved. It supports two options:
- `streamed`: Downloads the artifact in-memory. Currently only supports .conda packages.
- `oci_with_streamed_fallback`: Tries to use the OCI registry first, then falls back to streamed metadata.
```toml
metadata_retrieval = "streamed"
```

#### Override Extensions

`override_extensions`: This is used to define extensions of conda package that override auto detection.
Possible choices are `[".conda", ".tar.bz2"]`.
```toml
override_extensions = [".conda"]
```

## Configuring Dashboards

Dashboards are related applications that are linked from the package browser. They are defined in the `[dashboards]` section with a unique name and a `url_pattern`.

```toml
[dashboards]
anaconda = { url_pattern = "https://anaconda.org/{channel}/{name}/files?version={version}" }
ghcr = { url_pattern = "https://github.com/orgs/channel-mirrors/packages/container/package/{channel}%2F{subdir}%2F{name}" }
prefix = { url_pattern = "https://prefix.dev/channels/{channel}/packages/{name}" }
```
The `url_pattern` can contain placeholders that are encompassed in curly braces `{}`. These placeholders get replaced with the relevant value when generating the URLs. Current available placeholders include `{channel}`, `{name}`, `{version}`, and `{subdir}`. 

Note that the `app_config.toml` file is loaded by the application at runtime. Therefore, any changes made while the app is running will take effect only after you restart the application. Ensure to follow the correct syntax to avoid runtime errors.

## Complete Schema
Refer to [app_config.py](../app_config.py) for the complete configuration schema.

You can configure your IDE to use the [app_config.schema.json](../app_config.schema.json) file for auto-completion and validation of the `app_config.toml` file.