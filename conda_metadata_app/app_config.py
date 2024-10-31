import functools
import json
import os
from collections.abc import Iterable
from enum import StrEnum
from typing import Self, Literal

from pydantic import AnyHttpUrl, BaseModel, field_validator, TypeAdapter, ValidationError, model_validator, ConfigDict
from pydantic_settings import BaseSettings, SettingsConfigDict, PydanticBaseSettingsSource, TomlConfigSettingsSource


class HashableBaseModel(BaseModel):
    def __hash__(self):
        return hash((type(self),) + tuple(self.__dict__.values()))


class PackageDiscoveryChoice(StrEnum):
    CHANNEL_DATA = "channeldata"
    """
    Discover packages using channeldata.json
    """
    REPODATA = "repodata"
    """
    Discover packages using repodata.json
    """


class ArtifactDiscoveryChoice(StrEnum):
    ANACONDA_API = "anaconda"
    """
    Discover artifacts using the Anaconda API. Obviously, this only works if the package is hosted on Anaconda.
    Only public channels are supported.
    """
    REPODATA = "repodata"
    """
    Discover artifacts using repodata.json
    """


class ArchSubdirDiscoveryChoice(StrEnum):
    ALL = "all"
    """
    Try to find repodata.json for a well-known list of architecture subdirectories imported from rattler,
    and use those subdirs for which repodata.json is found.
    """
    CHANNELDATA = "channeldata"
    """
    Find all architecture subdirectories in channeldata.json.
    """


class ArchSubdirList(BaseModel):
    subdirs: list[str]
    """
    A list of architecture subdirectories.
    """

    model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True)


class PackageFilter(BaseModel):
    """
    By default, no packages are filtered. By setting allowed_names or allowed_prefixes, only packages that match
    the criteria will be considered.
    """
    allowed_names: list[str] = []
    """
    Whitelist of package names. Only packages with names in this list will be considered.
    """
    allowed_prefixes: list[str] = []
    """
    Whitelist of package prefixes.
    Only packages with names that start with one of the prefixes in this list will be considered.
    """

    def apply_filter(self, package_names: Iterable[str]) -> Iterable[str]:
        """
        Apply the filter to a list of package names.
        """
        if not self.allowed_names and not self.allowed_prefixes:
            return package_names
        return filter(
            lambda package_name: package_name in self.allowed_names
            or any(package_name.startswith(prefix) for prefix in self.allowed_prefixes),
            package_names,
        )

    model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True)


class MetadataRetrieval(StrEnum):
    STREAMED = "streamed"
    """
    Currently only supports .conda packages.
    """
    OCI_WITH_STREAMED_FALLBACK = "oci_with_streamed_fallback"
    """
    Try to use the OCI registry first, then fall back to streamed metadata.
    Note that the OCI registry is currently only supported for public channels, and uses the name (!) of
    the channel you have configured.
    """


class EnvSecret(HashableBaseModel):
    env: str
    """
    Read the secret from the environment variable with this name.
    """

    @functools.cache
    def get_value(self) -> str:
        return os.environ[self.env]

    @model_validator(mode="after")
    def _check_env_exists(self) -> Self:
        try:
            # also populate the cache
            self.get_value()
        except KeyError:
            raise ValueError(f"Secret environment variable {self.env} does not exist.")
        return self


class FileSecret(HashableBaseModel):
    file: str
    """
    Read the secret from the file with this name.
    """

    @functools.cache
    def get_value(self) -> str:
        with open(self.file) as f:
            return f.read().strip()

    @model_validator(mode="after")
    def _check_file_exists(self) -> Self:
        try:
            # also populate the cache
            self.get_value()
        except FileNotFoundError:
            raise ValueError(f"Secret file {self.file} does not exist.")
        return self


Secret = EnvSecret | FileSecret


class Channel(BaseModel):
    url: AnyHttpUrl
    """
    The URL of the channel. For conda-forge, this should be https://conda.anaconda.org/conda-forge.
    """
    rss_enabled: bool = False
    """
    Whether to enable the RSS feed for this channel.
    If true, it is assumed that the channel has an RSS feed at {url}/rss.xml.
    """
    package_discovery: PackageDiscoveryChoice
    """
    How to discover names of packages in the channel.
    """
    artifact_discovery: ArtifactDiscoveryChoice
    """
    How to discover artifacts in the channel, given a package name.
    """
    arch_subdir_discovery: ArchSubdirDiscoveryChoice | ArchSubdirList = ArchSubdirDiscoveryChoice.CHANNELDATA
    """
    How to discover architecture subdirectories in the channel.
    Use an ArchSubdirList to specify a list of subdirectories.
    """
    repodata_patches_package: str | None = None
    """
    A package that contains repodata patches, which is expected to have the format of `conda-forge-repodata-patches`.
    For conda-forge, this should be `conda-forge-repodata-patches`.
    The package is expected to be available in the channel.
    """
    map_conda_forge_package_to_feedstock: bool = False
    """
    Enable this for conda-forge to map package names to feedstock names.
    This is used for provenance URLs (see below).
    
    If this is False, the package name is used as the feedstock name.
    """
    provenance_url_pattern: str | None = None
    """
    A URL pattern to link to the provenance of a package. The URL pattern should contain a `{feedstock}` placeholder
    for the feedstock (!) name (see map_conda_forge_package_to_feedstock).
    Each placeholder will be replaced with the feedstock name.
    
    For conda-forge, this should be https://github.com/conda-forge/{feedstock}-feedstock.
    A remote URL present in the metadata always takes precedence over this URL pattern.
    """

    # noinspection PyNestedDecorators
    @field_validator("provenance_url_pattern")
    @classmethod
    def _validate_provenance_url_pattern(cls, provenance_url_pattern: str | None) -> str | None:
        if provenance_url_pattern is None:
            return None

        replaced_url = provenance_url_pattern.format(feedstock="test")

        # assert that replaced_url is a valid URL
        try:
            TypeAdapter(AnyHttpUrl).validate_python(replaced_url)
        except ValidationError:
            raise ValueError("provenance_url_pattern must be a valid URL pattern with a {} placeholder.")

        return provenance_url_pattern

    package_filter: PackageFilter = PackageFilter()
    """
    Filter packages by name or prefix.
    """
    supports_broken_label: bool = False
    """
    Set this to true if the channel supports a label called "broken" indicating yanked releases.
    Currently, this is only respected if artifact_discovery is set to "anaconda".
    """

    @model_validator(mode="after")
    def check_supports_broken_label_artifact_discovery(self) -> Self:
        if self.supports_broken_label and self.artifact_discovery != ArtifactDiscoveryChoice.ANACONDA_API:
            raise ValueError("supports_broken_label is only supported for Anaconda API artifact discovery.")
        return self

    dashboards: list[str] = []
    """
    Must match keys in the AppConfig.dashboards dictionary.
    """

    metadata_retrieval: MetadataRetrieval
    """
    How to retrieve metadata for a package.
    """

    override_extensions: list[Literal[".conda"] | Literal[".tar.bz2"]] | None = None
    """
    Set this to a list of conda package extensions to override the auto detection of extensions.
    """

    auth_username: str | Secret | None = None
    """
    The username for HTTP basic authentication.
    """

    auth_password: str | Secret | None = None
    """
    The password for HTTP basic authentication.
    """

    auth_quetz_token: str | Secret | None = None
    """
    The Quetz token for authentication. Adds an X-API-Key header to requests.
    """

    auth_bearer_token: str | Secret | None = None
    """
    The bearer token for authentication. Adds an Authorization: Bearer header to requests.
    Use this for private prefix.dev channels.
    """

    @model_validator(mode="after")
    def _check_single_or_no_auth(self) -> Self:
        basic_auth_fields_set = [
            self.auth_username is not None,
            self.auth_password is not None,
        ]
        if 0 < sum(basic_auth_fields_set) < 2:
            raise ValueError("auth_username and auth_password must be set together.")

        basic_auth_set = any(basic_auth_fields_set)
        auth_fields_set = [
            basic_auth_set,
            self.auth_quetz_token is not None,
            self.auth_bearer_token is not None,
        ]

        if sum(auth_fields_set) > 1:
            raise ValueError("Only one authentication method can be set at a time.")

        return self


    @property
    def rss_url(self) -> str:
        return f"{self.url}/rss.xml"

    @property
    def channeldata_url(self) -> str:
        return f"{self.url}/channeldata.json"

    def get_repodata_url(self, arch_subdir: str) -> str:
        return f"{self.url}/{arch_subdir}/repodata.json"

    def get_zstd_repodata_url(self, arch_subdir: str) -> str:
        return self.get_repodata_url(arch_subdir) + ".zst"

    def get_artifact_download_url(self, arch_subdir: str, package_name: str, version: str, build_string: str, extension: str) -> str:
        return f"{self.url}/{arch_subdir}/{package_name}-{version}-{build_string}.{extension}"

    model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True)


class Dashboard(BaseModel):
    url_pattern: str
    """
    The URL pattern of the dashboard. The URL pattern can contain the following placeholders within curly {} braces:
    
    - `channel`: The channel name. If the channel name contains a slash, only the second part is used.
    - `name`: The name of the package.
    - `version`: The version of the package.
    - `subdir`: The architecture subdirectory.
    """

    # noinspection PyNestedDecorators
    @field_validator("url_pattern")
    @classmethod
    def _validate_url_pattern(cls, url_pattern: str) -> str:
        replaced_url = url_pattern.format(
            channel="test-channel",
            name="test-name",
            version="1.2.3",
            subdir="noarch",
        )

        # assert that replaced_url is a valid URL
        try:
            TypeAdapter(AnyHttpUrl).validate_python(replaced_url)
        except ValidationError:
            raise ValueError("url_pattern must be a valid URL pattern with placeholders.")

        return url_pattern

    model_config = ConfigDict(extra="forbid", use_attribute_docstrings=True)



class AppConfig(BaseSettings):
    channels: dict[str, Channel]
    """
    All channels that should be available. The key is the channel name.
    """

    # noinspection PyNestedDecorators
    @field_validator("channels")
    @classmethod
    def validate_channels(cls, channels: dict[str, Channel]) -> dict[str, Channel]:
        if not channels:
            raise ValueError("At least one channel must be defined.")

        if any(channel_name.count("/") > 1 for channel_name in channels):
            raise ValueError("Channel names must not contain more than one slash.")
        return channels

    dashboards: dict[str, Dashboard] = {}
    """
    Dashboards are other applications that can be linked to from the package browser.
    """

    enable_filepath_search: bool = True
    """
    Whether to enable the file path search feature.
    The file path search feature sends information about your query to an API provided by Quansight.
    """

    @model_validator(mode="after")
    def _validate_dashboards(self) -> Self:
        for channel in self.channels.values():
            for dashboard_name in channel.dashboards:
                if dashboard_name not in self.dashboards:
                    raise ValueError(f"Dashboard {dashboard_name} is not defined.")
        return self

    model_config = SettingsConfigDict(toml_file="app_config.toml", extra="forbid", use_attribute_docstrings=True)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (TomlConfigSettingsSource(settings_cls),)


def export_json_schema() -> None:
    with open("app_config.schema.json", "w") as f:
        json.dump(AppConfig.model_json_schema(), f, indent=2)


if __name__ == '__main__':
    export_json_schema()
