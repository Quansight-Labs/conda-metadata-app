from enum import StrEnum
from collections.abc import Iterable

from pydantic import AnyHttpUrl, BaseModel, field_validator, TypeAdapter, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict, PydanticBaseSettingsSource, TomlConfigSettingsSource


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
    CHANNELDATA = "channeldata"


class ArchSubdirList(StrEnum):
    subdirs: list[str]


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


class Channel(BaseModel):
    url: AnyHttpUrl
    rss_enabled: bool = False
    package_discovery: PackageDiscoveryChoice
    artifact_discovery: ArtifactDiscoveryChoice
    arch_subdir_discovery: ArchSubdirDiscoveryChoice | ArchSubdirList = ArchSubdirDiscoveryChoice.CHANNELDATA
    repodata_patches_package: str | None = None
    """
    A package that contains repodata patches, which is expected to have the format of `conda-forge-repodata-patches`.
    For conda-forge, this should be `conda-forge-repodata-patches`.
    The package is expected to be available in the channel.
    """
    feedstock_outputs_mapper_base_url: AnyHttpUrl | None = None
    """
    A base URL to map packages to feedstock names. Feedstock names do not include suffixes like `-feedstock`.
    If None, it is assumed that the feedstock name is the same as the package name.
    
    For conda-forge, this should be https://raw.githubusercontent.com/conda-forge/feedstock-outputs/main.
    Other channels must adhere to the conda-forge feedstock-outputs repository structure.
    """
    provenance_url_pattern: str | None = None
    """
    A URL pattern to link to the provenance of a package. The URL pattern should contain a `{}` placeholder
    for the feedstock (!) name (see feedstock_outputs_mapper_base_url).
    Each placeholder will be replaced with the feedstock name.
    
    For conda-forge, this should be https://github.com/conda-forge/{}-feedstock.
    """

    # noinspection PyNestedDecorators
    @field_validator("provenance_url_pattern")
    @classmethod
    def _validate_provenance_url_pattern(cls, provenance_url_pattern: str | None) -> str | None:
        if provenance_url_pattern is None:
            return None

        replaced_url = provenance_url_pattern.replace("{}", "test")

        # assert that replaced_url is a valid URL
        try:
            TypeAdapter(AnyHttpUrl).validate_python(replaced_url)
        except ValidationError:
            raise ValueError("provenance_url_pattern must be a valid URL pattern with a {} placeholder.")

        return provenance_url_pattern

    package_filter: PackageFilter = PackageFilter()


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


class AppConfig(BaseSettings):
    channels: dict[str, Channel]

    # noinspection PyNestedDecorators
    @field_validator("channels")
    @classmethod
    def validate_channels(cls, channels: dict[str, Channel]) -> dict[str, Channel]:
        if not channels:
            raise ValueError("At least one channel must be defined.")

        if any(channel_name.count("/") > 1 for channel_name in channels):
            raise ValueError("Channel names must not contain more than one slash.")
        return channels

    model_config = SettingsConfigDict(toml_file="app_config.toml")

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
