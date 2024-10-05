import json
import os
import re
import typing
from typing import Any
from collections.abc import Iterable
from contextlib import closing
from datetime import datetime
from difflib import unified_diff
from inspect import cleandoc
from io import StringIO
from tempfile import gettempdir

import zstandard as zstd
from conda_forge_metadata.types import ArtifactData
from rattler.platform import PlatformLiteral
from requests.auth import HTTPBasicAuth

from conda_metadata_app.app_config import (AppConfig, Channel, PackageDiscoveryChoice, ArchSubdirDiscoveryChoice,
                                           ArchSubdirList, ArtifactDiscoveryChoice, MetadataRetrieval, Secret)
from conda_metadata_app.version_order import VersionOrder

if not os.environ.get("CACHE_DIR"):
    from conda_oci_mirror import defaults

    os.environ["CACHE_DIR"] = defaults.CACHE_DIR = os.path.join(
        gettempdir(), "conda-oci-mirror-cache"
    )

import requests
import streamlit as st
from conda_forge_metadata.artifact_info.info_json import get_artifact_info_as_json
from conda_forge_metadata.feedstock_outputs import package_to_feedstock
from conda_package_streaming.package_streaming import stream_conda_component
from conda_package_streaming.url import conda_reader_for_url
from ruamel.yaml import YAML
from streamlit.logger import get_logger
from xml.etree import ElementTree as ET


yaml = YAML(typ="safe")
yaml.allow_duplicate_keys = True
yaml.default_flow_style = False
logger = get_logger(__name__)
st.set_page_config(
    page_title="conda metadata browser",
    page_icon="📦",
    initial_sidebar_state="expanded",
)

def bar_esc(s: str) -> str:
    "Escape vertical bars in tables"
    return s.replace("|", "\\|")


@st.cache_resource
def app_config() -> AppConfig:
    return AppConfig()


def get_channel_config(channel_name: str) -> Channel:
    try:
        return app_config().channels[channel_name]
    except KeyError:
        raise ValueError(f"Channel `{channel_name}` not found in the configuration!")


def _unwrap_secret(secret: str | Secret) -> str:
    if isinstance(secret, str):
        return secret
    return secret.get_value()


def _make_http_session(channel_name: str) -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = "conda-metadata-browser/0.1.0"
    channel_config = get_channel_config(channel_name)
    if channel_config.auth_username is not None:
        # use HTTP basic auth
        username = _unwrap_secret(channel_config.auth_username)
        password = _unwrap_secret(channel_config.auth_password)
        session.auth = HTTPBasicAuth(username, password)
        return session
    if quetz_token := channel_config.auth_quetz_token:
        # use Quetz token auth
        session.headers["X-API-Key"] = _unwrap_secret(quetz_token)
        return session
    if bearer_token := channel_config.auth_bearer_token:
        # use bearer token auth
        session.headers["Authorization"] = f"Bearer {_unwrap_secret(bearer_token)}"
        return session
    # no auth
    return session



@st.cache_resource(ttl="15m", max_entries=5)
def rss_data(channel_name: str) -> ET.ElementTree | None:
    """
    :returns None if the channel does not have an RSS feed
    """
    channel_config = get_channel_config(channel_name)
    if not channel_config.rss_enabled:
        return None
    rss_url = channel_config.rss_url
    r = _make_http_session(channel_name).get(rss_url)
    r.raise_for_status()
    return ET.ElementTree(ET.fromstring(r.text))


@st.cache_resource(ttl="15m", max_entries=10)
def get_channeldata(channel_name: str) -> dict:
    r = _make_http_session(channel_name).get(get_channel_config(channel_name).channeldata_url)
    r.raise_for_status()
    return r.json()


def _download_compressed_repodata(channel_name: str, arch_subdir: str) -> dict | None:
    """
    Try to download the compressed repodata.json file.
    If the file does not exist, return None.
    Other HTTP errors are raised.
    :returns the decompressed repodata.json, or None if the compressed file does not exist
    """
    channel_config = get_channel_config(channel_name)
    zstd_url = channel_config.get_zstd_repodata_url(arch_subdir)

    with _make_http_session(channel_name).get(zstd_url, stream=True) as r:
        if r.status_code == 404:
            return None
        r.raise_for_status()

        dctx = zstd.ZstdDecompressor()
        with dctx.stream_reader(r.raw) as reader:
            return json.load(reader)

@st.cache_resource(ttl="15m", max_entries=50)
def get_repodata(channel_name: str, arch_subdir: str) -> dict:
    """
    Fetch the repodata.json for a channel and subdir.
    This function first tries to download the .zst-compressed version of the repodata, and if that does not exist,
    it falls back to the uncompressed.
    """
    decompressed_repodata = _download_compressed_repodata(channel_name, arch_subdir)
    channel_config = get_channel_config(channel_name)

    if decompressed_repodata is not None:
        return decompressed_repodata

    # fall back to the uncompressed version
    repodata_url = channel_config.get_repodata_url(arch_subdir)
    r = _make_http_session(channel_name).get(repodata_url)
    r.raise_for_status()

    return r.json()


def get_all_packages_sections_from_repodata(channel_name: str, arch_subdir: str) -> dict:
    """
    Contains the "packages" and "packages.conda" sections of the repodata.
    """
    sections = {}
    repodata = get_repodata(channel_name, arch_subdir)
    for key in ("packages", "packages.conda"):
        if key in repodata:
            sections.update(repodata[key])
    return sections


@st.cache_resource(ttl="15m", max_entries=1000)
def anaconda_api_data(package_name: str, channel_name: str) -> dict:
    if channel_name.startswith("pkgs/"):
        channel_name = channel_name.split("/", 1)[1]
    r = requests.get(f"https://api.anaconda.org/package/{channel_name}/{package_name}/files")
    r.raise_for_status()
    return r.json()


@st.cache_resource(ttl="1d", max_entries=10)
def repodata_patches(channel_name: str) -> dict[str, Any]:
    """
    This function assumes that the artifact discovery mode for the channel is "anaconda".
    """
    package_name = f"{channel_name}-repodata-patches"
    data = anaconda_api_data(package_name, channel_name)
    most_recent = sorted(data, key=lambda x: x["attrs"]["timestamp"], reverse=True)[0]
    filename, conda = conda_reader_for_url(f"https:{most_recent['download_url']}")

    patches = {}
    with closing(conda):
        for tar, member in stream_conda_component(filename, conda, component="pkg"):
            if member.name.endswith("patch_instructions.json"):
                patches[member.name.split("/")[0]] = json.load(tar.extractfile(member))
    return patches


@st.cache_resource(ttl="1d", max_entries=1000)
def provenance_urls(package_name: str, channel: str, data: dict | None = None) -> list[str]:
    if not package_name or not data:
        return [""]
    if data is not None:
        remote_url = data.get("rendered_recipe", {}).get("extra", {}).get("remote_url")
        if remote_url:
            if remote_url.startswith("git@github.com:"):
                remote_url = remote_url.replace("git@github.com:", "https://github.com/")
                if remote_url.endswith(".git"):
                    remote_url = remote_url[:-4]
            sha = data.get("rendered_recipe", {}).get("extra", {}).get("sha")
            if sha and remote_url.startswith("https://github.com/"):
                return [f"{remote_url}/commit/{sha}"]
            return remote_url

    url_pattern = get_channel_config(channel).provenance_url_pattern
    if not url_pattern:
        return [""]

    feedstock_names: list[str]
    if get_channel_config(channel).map_conda_forge_package_to_feedstock:
        # wrong type annotation in an old version of conda-forge-metadata
        # noinspection PyTypeChecker
        feedstock_names = package_to_feedstock(package_name)
    else:
        feedstock_names = [package_name]

    return [
        url_pattern.format(feedstock=feedstock_name)
        for feedstock_name in feedstock_names
    ]


def get_package_names(channel_name: str) -> list[str]:
    """
    Get all package names of a channel.
    """
    all_packages: Iterable[str]
    package_discovery_choice = get_channel_config(channel_name).package_discovery

    if package_discovery_choice == PackageDiscoveryChoice.CHANNEL_DATA:
        all_packages = get_channeldata(channel_name)["packages"].keys()
    elif package_discovery_choice == PackageDiscoveryChoice.REPODATA:
        all_subdirs = get_all_arch_subdirs(channel_name)
        all_packages: set[str] = set()

        for subdir in all_subdirs:
            all_packages.update(
                pkg["name"] for pkg in get_all_packages_sections_from_repodata(channel_name, subdir).values()
            )
    else:
        raise RuntimeError("Invalid package discovery choice. This is an implementation error.")

    names = get_channel_config(channel_name).package_filter.apply_filter(all_packages)
    return sorted(
        names,
        key=lambda x: f"zzzzzzz{x}" if x.startswith("_") else x,
    )


@st.cache_resource(ttl="12h", max_entries=1000)
def _discover_arch_subdirs_exhaustively(channel_name: str) -> list[str]:
    """
    Call this function for ArchSubdirDiscoveryChoice.ALL.
    It uses rattler's list of possible platforms, and tries to find repodata.json for each of them.
    :returns the list of all arch subdirs for which repodata.json was found
    """
    all_subdirs = []
    for platform in typing.get_args(PlatformLiteral):
        repodata_url = get_channel_config(channel_name).get_repodata_url(platform)
        # make a HEAD request to check if the repodata exists
        r = _make_http_session(channel_name).head(repodata_url, allow_redirects=True)
        if r.status_code == 404:
            continue
        r.raise_for_status()
        all_subdirs.append(platform)
    return all_subdirs



def get_all_arch_subdirs(channel_name: str) -> list[str]:
    """
    Get all arch subdirs (e.g., noarch, osx-64, linux-64) of a channel.
    The arch subdirs are sorted, ascending.
    """
    discovery_choice = get_channel_config(channel_name).arch_subdir_discovery

    if discovery_choice == ArchSubdirDiscoveryChoice.CHANNELDATA:
        return sorted(get_channeldata(channel_name)["subdirs"])

    if discovery_choice == ArchSubdirDiscoveryChoice.ALL:
        return sorted(_discover_arch_subdirs_exhaustively(channel_name))

    if isinstance(discovery_choice, ArchSubdirList):
        return discovery_choice.subdirs

    raise RuntimeError(f"Invalid arch subdir discovery choice: {discovery_choice} This is an implementation error.")



def get_arch_subdirs_for_package(package_name: str, channel_name: str, with_broken: bool = False) -> list[str]:
    """
    Get the arch subdirs for a package.
    The arch subdirs are sorted, ascending.
    For same reason, we need to handle an empty package name. If the package name is empty, we return an empty list.
    """
    if not package_name:
        return []

    all_subdirs: list[str]
    arch_subdir_discovery_choice = get_channel_config(channel_name).arch_subdir_discovery

    if arch_subdir_discovery_choice == ArchSubdirDiscoveryChoice.CHANNELDATA:
        all_subdirs = get_channeldata(channel_name)["packages"][package_name]["subdirs"]
    elif arch_subdir_discovery_choice == ArchSubdirDiscoveryChoice.ALL or \
            isinstance(arch_subdir_discovery_choice, ArchSubdirList):
        all_subdirs = get_all_arch_subdirs(channel_name)
    else:
        raise RuntimeError("Invalid arch subdir discovery choice. This is an implementation error.")

    return sorted(
        subdir for subdir in all_subdirs
        if get_versions(channel_name, subdir, package_name, with_broken=with_broken)
    )



def _best_version_in_subdir(package_name: str, channel_name: str, with_broken: bool = False) \
        -> tuple[str, str] | tuple[None, None]:
    if not package_name:
        return None, None
    subdirs_plus_best_version = sorted(
        (
            (subdir, get_versions(channel_name, subdir, package_name, with_broken=with_broken)[0])
            for subdir in get_arch_subdirs_for_package(package_name, channel_name, with_broken=with_broken)
        ),
        key=lambda x: VersionOrder(x[1]),
        reverse=True,
    )
    if subdirs_plus_best_version:
        return subdirs_plus_best_version[0]
    return None, None


def get_versions(channel_name: str, subdir: str, package_name: str, with_broken: bool = False) -> list[str]:
    """
    Get the versions of a package in a channel and subdir.
    If package_name or subdir are empty, return an empty list.

    :param channel_name: the channel name
    :param subdir: the arch subdir
    :param package_name: the package name
    :param with_broken: whether to include broken packages
    :return: a sorted list of versions (descending version order)
    """
    if not package_name or not subdir:
        return []

    all_versions: set[str]
    discovery_choice = get_channel_config(channel_name).artifact_discovery

    if discovery_choice == ArtifactDiscoveryChoice.ANACONDA_API:
        api_data = anaconda_api_data(package_name, channel_name)
        all_versions = {
            pkg["version"]
            for pkg in api_data
            if pkg["attrs"]["subdir"] == subdir
            and "main" in pkg["labels"]
            and (with_broken or "broken" not in pkg["labels"])
        }
    elif discovery_choice == ArtifactDiscoveryChoice.REPODATA:
        repodata_pkg = get_all_packages_sections_from_repodata(channel_name, subdir)
        all_versions = {
            pkg["version"]
            for pkg in repodata_pkg.values()
            if pkg["name"] == package_name
        }
    else:
        raise RuntimeError("Invalid artifact discovery choice. This is an implementation error.")

    return sorted(
        all_versions,
        key=VersionOrder,
        reverse=True,
    )


def _build_mapping_from_anaconda_api(package_name: str, subdir: str, version: str, channel: str,
                                     with_broken: bool = False) -> dict[str, int]:
    """
    Returns a mapping from build string to build number.
    """
    data = anaconda_api_data(package_name, channel)
    return {
        pkg["attrs"]["build"]: pkg["attrs"]["build_number"]
        for pkg in data
        if pkg["attrs"]["subdir"] == subdir
           and pkg["version"] == version
           and "main" in pkg["labels"]
           and (with_broken or "broken" not in pkg["labels"])
    }


def _build_mapping_from_repodata(package_name: str, subdir: str, version: str, channel: str) -> dict[str, int]:
    """
    Note: This function cannot consider labels as they are not present in the repodata.
    Returns a mapping from build string to build number.
    """
    repodata_packages = get_all_packages_sections_from_repodata(channel, subdir)

    return {
        pkg["build"]: pkg["build_number"]
        for pkg in repodata_packages.values()
        if pkg["name"] == package_name
           and pkg["version"] == version
    }


def builds(package_name: str, subdir: str, version: str, channel: str, with_broken: bool = False) -> list[str]:
    if not package_name or not subdir or not version:
        return []

    build_str_to_num: dict[str, int]
    discovery_choice = get_channel_config(channel).artifact_discovery

    if discovery_choice == ArtifactDiscoveryChoice.ANACONDA_API:
        build_str_to_num = _build_mapping_from_anaconda_api(package_name, subdir, version, channel, with_broken)
    elif discovery_choice == ArtifactDiscoveryChoice.REPODATA:
        build_str_to_num = _build_mapping_from_repodata(package_name, subdir, version, channel)
    else:
        raise RuntimeError("Invalid artifact discovery choice. This is an implementation error.")

    return [
        k
        for k, _ in sorted(
            build_str_to_num.items(), key=lambda kv: (kv[1], kv[0]), reverse=True
        )
    ]


def _extensions_from_anaconda_api(package_name: str, subdir: str, version: str, build: str, channel: str,
                                  with_broken: bool = False) -> set[str]:
    data = anaconda_api_data(package_name, channel)
    return {
        ("conda" if pkg["basename"].endswith(".conda") else "tar.bz2")
        for pkg in data
        if pkg["attrs"]["subdir"] == subdir
           and pkg["version"] == version
           and pkg["attrs"]["build"] == build
           and "main" in pkg["labels"]
           and (with_broken or "broken" not in pkg["labels"])
    }


def _extensions_from_repodata(package_name: str, subdir: str, version: str, build: str, channel: str) -> set[str]:
    """
    with_broken cannot be considered here as repodata does not include yanked packages.
    """
    repodata_packages = get_all_packages_sections_from_repodata(channel, subdir)
    return {
        ("conda" if filename.endswith(".conda") else "tar.bz2")
        for filename, pkg in repodata_packages.items()
        if pkg["name"] == package_name
           and pkg["version"] == version
           and pkg["build"] == build
    }


def extensions(package_name: str, subdir: str, version: str, build: str, channel: str,
               with_broken: bool = False) -> list[str]:
    if not package_name or not subdir or not version or not build:
        return []
    if override_extensions := get_channel_config(channel).override_extensions:
        return override_extensions

    discovery_choice = get_channel_config(channel).artifact_discovery

    if discovery_choice == ArtifactDiscoveryChoice.ANACONDA_API:
        return sorted(_extensions_from_anaconda_api(package_name, subdir, version, build, channel, with_broken))
    if discovery_choice == ArtifactDiscoveryChoice.REPODATA:
        return sorted(_extensions_from_repodata(package_name, subdir, version, build, channel))
    raise RuntimeError("Invalid artifact discovery choice. This is an implementation error.")


def _is_broken(package_name: str, subdir: str, version: str, build: str, extension: str, channel: str) -> bool:
    channel_config = get_channel_config(channel)
    if channel_config.artifact_discovery != ArtifactDiscoveryChoice.ANACONDA_API or not channel_config.supports_broken_label:
        return False  # we don't know
    data = anaconda_api_data(package_name, channel)
    for pkg in data:
        if (
            pkg["attrs"]["subdir"] == subdir
            and pkg["version"] == version
            and pkg["attrs"]["build"] == build
            and pkg["basename"].endswith(extension)
        ):
            return "broken" in pkg["labels"]
    return False


def patched_repodata(channel: str, subdir: str, artifact: str) -> tuple[dict, bool]:
    """
    This function assumes that the artifact discovery mode for the channel is "anaconda".
    """
    patches = repodata_patches(channel)[subdir]
    key = "packages.conda" if artifact.endswith(".conda") else "packages"
    patched_data = patches[key].get(artifact, {})
    yanked = artifact in patches["remove"]
    return patched_data, yanked


def artifact_metadata(channel: str, subdir: str, artifact: str) -> ArtifactData | None:
    channel_config = get_channel_config(channel)

    if channel_config.metadata_retrieval == MetadataRetrieval.OCI_WITH_STREAMED_FALLBACK:
        # OCI requests are never authenticated for now
        metadata = get_artifact_info_as_json(
            channel=channel,
            subdir=subdir,
            artifact=artifact,
            backend="oci",
            skip_files_suffixes=(),
        )

        if (metadata and metadata.get("name")) or artifact.endswith(".tar.bz2"):
            return metadata

    if artifact.endswith(".tar.bz2"):
        return None

    # Use streamed metadata as a fallback
    authenticated_session = _make_http_session(channel)

    return get_artifact_info_as_json(
        channel=str(channel_config.url),
        subdir=subdir,
        artifact=artifact,
        backend="streamed",
        skip_files_suffixes=(),
        session=authenticated_session
    )


def is_archived_repo(repo_url_or_owner_repo) -> bool:
    owner, repo = repo_url_or_owner_repo.split("/")[-2:]
    if owner != "conda-forge":
        return False
    r = requests.get(f"https://api.github.com/repos/{owner}/{repo}")
    if r.ok:
        return r.json().get("archived", False)
    return False


def parse_url_params() -> tuple[dict[str, Any], bool]:
    """
    Allowed query params:
    - q: channel
    - q: channel/package_name
    - q: channel/subdir::package_name-version-build
    - q: channel/subdir/package_name-version-build.extension
    - with_broken: true or false
    """
    channel, subdir, artifact, package_name, version, build, extension = [None] * 7
    with_broken = False
    path = None
    url_params = st.query_params.to_dict()
    ok = True
    if "with_broken" in url_params:
        with_broken = url_params.pop("with_broken") == "true"
    if "q" in url_params:
        query = url_params["q"]
        if query in app_config().channels:  # channel only
            channel = query
        elif "/" in query:
            try:
                components = query.split("/")
                if len(components) == 2:  # cannot be a channel name with a slash (e.g., pkgs/main)
                    channel, artifact = components
                    subdir = None
                elif len(components) == 3:
                    channel, subdir, artifact = components
                    if f"{channel}/{subdir}" in app_config().channels:
                        channel = f"{channel}/{subdir}"
                        subdir = None
                elif len(components) == 4:
                    *channel, subdir, artifact = components
                    channel = "/".join(channel)
                else:
                    raise ValueError("Invalid number of URL components")
            except Exception as exc:
                logger.error(exc)
                ok = False
            else:
                if artifact:
                    if artifact.endswith(".conda"):
                        extension = "conda"
                        rest_of_artifact = artifact[:-len(".conda")]
                    elif artifact.endswith(".tar.bz2"):
                        extension = "tar.bz2"
                        rest_of_artifact = artifact[:-len(".tar.bz2")]
                    elif artifact.endswith("."):
                        extension = None
                        rest_of_artifact = artifact.rstrip(".")
                    else:
                        extension = None
                        rest_of_artifact = artifact
                    try:
                        package_name, version, build = rest_of_artifact.rsplit("-", 2)
                        if not version[0].isdigit():
                            package_name = rest_of_artifact
                            version, build = None, None
                    except Exception:
                        package_name = rest_of_artifact
    elif "path" in url_params:
        path = url_params["path"]
    elif url_params:
        ok = False
    return {
        "channel": channel,
        "subdir": subdir,
        "artifact": artifact,
        "package_name": package_name,
        "version": version,
        "build": build,
        "extension": extension,
        "path": path,
        "with_broken": with_broken
    }, ok


url_params, url_ok = parse_url_params()
if not url_ok:
    st.error(
        f"Invalid URL params: `{url_params}`.\n\n"
        "Allowed syntaxes: \n"
        "- `/?q=channel`.\n"
        "- `/?q=channel/package_name`.\n"
        "- `/?q=channel/subdir/package_name`.\n"
        "- `/?q=channel/subdir/package_name-version-build`.\n"
        "- `/?q=channel/subdir/package_name-version-build.extension`.\n"
    )
    st.query_params.clear()
    st.stop()
elif url_params["artifact"] and "channel" not in st.session_state:
    # Initialize state from URL params, only on first run
    # These state keys match the sidebar widgets keys below
    st.session_state.channel = url_params["channel"]
    if url_params["subdir"] is not None:
        st.session_state.subdir = url_params["subdir"]
    if url_params["package_name"] is not None:
        _package_name = url_params["package_name"]
        if _package_name in get_package_names(url_params["channel"]):
            st.session_state.package_name = url_params["package_name"]
        else:
            st.error(f"Package `{_package_name}` not yet available in {url_params['channel']}!")
    if url_params["version"] is not None:
        st.session_state.version = url_params["version"]
    if url_params["build"] is not None:
        st.session_state.build = url_params["build"]
    if url_params["extension"] is not None:
        st.session_state.extension = url_params["extension"]
    if url_params["with_broken"]:
        st.session_state.with_broken = url_params["with_broken"]

_patched_metadata_channels = [
    channel
    for channel in app_config().channels
    if get_channel_config(channel).repodata_patches_package
]
_with_patches_help_extra = f" Only for {_patched_metadata_channels[0]}." \
    if len(_patched_metadata_channels) == 1 else ""
_with_patches_help: str
if _patched_metadata_channels:
    _with_patches_help = "Requires extra API calls. Slow!" + _with_patches_help_extra


with st.sidebar:
    st.title(
        "conda metadata browser",
        help="Web UI to browse the metadata of conda packages.",
    )
    with_broken = st.checkbox(
        "Include artifacts marked broken",
        value=False,
        key="with_broken",
        help="Include broken packages in the list of versions and builds. Does not have any effect if the artifact discovery is set to 'repodata'.",
    )

    with_patches: bool = False
    if _patched_metadata_channels:
        with_patches = st.checkbox(
            "Show patched metadata",
            value=False,
            key="with_patches",
            help=_with_patches_help,
        )
    show_archived = st.checkbox(
        "Highlight provenance if archived",
        value=False,
        key="show_archived",
        help="If the source feedstock is archived, the text will be struck through. "
        "Requires extra API calls. Slow! Only for conda-forge",
    )
    _all_channels = list(app_config().channels.keys())
    channel = st.selectbox(
        "Select a channel:",
        _all_channels,
        key="channel",
        # Use the user provided channel (via query params) if possible.
        index=_all_channels.index(url_params["channel"]) if url_params["channel"] in _all_channels else 0,
    )

    if get_channel_config(channel).artifact_discovery == ArtifactDiscoveryChoice.REPODATA and with_broken:
        st.warning(
            "The inclusion of broken artifacts option is ignored for channels that use repodata for artifact discovery."
        )

    _available_package_names = [""] + get_package_names(channel)    # empty string means: show RSS feed
    package_name = st.selectbox(
        "Enter a package name:",
        options=_available_package_names,
        key="package_name",
        help=f"Choose one package out of the {len(_available_package_names) - 1:,} available ones. "
        "Underscore-leading names are sorted last."
    )
    _available_subdirs = get_arch_subdirs_for_package(package_name, channel, with_broken=with_broken)
    _best_subdir, _best_version = _best_version_in_subdir(
        package_name, channel, with_broken=with_broken
    )
    if _best_subdir and not getattr(st.session_state, "subdir", None):
        st.session_state.subdir = _best_subdir
    if _best_version and not getattr(st.session_state, "version", None):
        st.session_state.version = _best_version

    subdir = st.selectbox(
        "Select a subdir:",
        options=_available_subdirs,
        key="subdir",
    )

    version = st.selectbox(
        "Select a version:",
        options=get_versions(channel, subdir, package_name, with_broken=with_broken),
        key="version",
    )
    # Add a small message if a newer version is available in a different subdir, and
    # the currently chosen version is the newest in the current subdir
    if (
        _best_version
        and version
        and version == get_versions(channel, subdir, package_name, with_broken=with_broken)[0]
        and VersionOrder(_best_version) > VersionOrder(version)
        and _best_subdir != subdir
    ):
        st.markdown(
            f"<sup>ℹ️ v{_best_version} is available for {_best_subdir}</sup>",
            unsafe_allow_html=True,
        )
    _build_options = builds(package_name, subdir, version, channel, with_broken=with_broken)
    if _build_options and not getattr(st.session_state, "build", None):
        st.session_state.build = _build_options[0]
    build = st.selectbox(
        "Select a build:",
        options=_build_options,
        key="build",
    )
    _extension_options = extensions(
        package_name, subdir, version, build, channel, with_broken=with_broken
    )
    if _extension_options and not getattr(st.session_state, "extension", None):
        st.session_state.extension = _extension_options[0]
    extension = st.selectbox(
        "Select an extension:",
        options=_extension_options,
        key="extension",
    )


def input_value_so_far():
    value = ""
    if channel:
        value = f"{channel}/"
    if package_name:
        if subdir:
            value += f"{subdir}::"
        value += f"{package_name}-"
        if version:
            value += f"{version}-"
            if build:
                value += f"{build}."
                if extension:
                    value += extension
    return value


def disable_button(query):
    if re.match(
        r"^[a-z0-9-]+/[a-z0-9-]+::[a-z0-9-]+-[0-9.]+-[a-z0-9_]+.[a-z0-9]+$", query
    ):
        return False
    if all([channel, subdir, package_name, version, build, extension]):
        return False
    return True


c1, c2 = st.columns([1, 0.15])
with c1:
    query = st.text_input(
            label="Search artifact metadata:",
            placeholder="channel/subdir::package_name-version-build.ext",
            value=input_value_so_far(),
            label_visibility="collapsed",
            key="query_input",
            disabled=True,
        )
with c2:
    submitted = st.button(
        "Submit",
        key="form",
        disabled=disable_button(query),
        use_container_width=True,
    )


if submitted or all([channel, subdir, package_name, version, build]):
    channel_subdir, artifact = query.split("::")
    channel, subdir = channel_subdir.rsplit("/", 1)
    st.query_params.clear()
    st.query_params.q = f"{channel}/{subdir}/{artifact}"
    if with_broken:
        st.query_params.with_broken = str(with_broken).lower()
    with st.spinner("Fetching metadata..."):
        data = artifact_metadata(
            channel=channel,
            subdir=subdir,
            artifact=artifact,
        )
        if not data and artifact.endswith(".tar.bz2") and get_channel_config(channel).metadata_retrieval == MetadataRetrieval.STREAMED:
            st.warning(f"Cannot retrieve metadata of an tar.bz2 artifact for non-OCI channels.")
            st.stop()
        elif not data:
            logger.error(f"No metadata found for `{query}`.")
            st.error(f"No metadata found for `{query}`.")
            st.stop()
elif channel and not package_name and not subdir and not version and not build and not extension:
    st.query_params.clear()
    st.query_params.q = channel
    data = "show_latest"
elif channel and package_name and not subdir and not version and not with_broken:
    data = (
        f"error:No artifacts found for `{package_name}` but broken packages might be omitted. "
        "Toggle the option in the sidebar to show potential matches."
    )
else:
    data = ""

if isinstance(data, dict):
    uploaded = "N/A"
    try:
        timestamp = data.get("index", {}).get("timestamp", 0) / 1000
        if timestamp:
            uploaded = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    except Exception as exc:
        logger.error(exc, exc_info=True)

    try:
        provenance = []
        for url in provenance_urls(package_name, channel, data):
            if "/commit/" in url:
                parts = url.split("/")
                commit = parts[-1]
                name = parts[-3]
                url_text = f"{name} @ `{commit[:7]}`"
            else:
                name = url_text = url.split("/")[-1]
            if (
                show_archived 
                and channel == "conda-forge" 
                and is_archived_repo(f"conda-forge/{name}")
            ):
                url_text = f"~~{url_text}~~"
            provenance.append(f"[{url_text}]({url})")
        provenance = ", ".join(provenance) if provenance else "*N/A*"
    except Exception as exc:
        provenance = "*N/A*"
        logger.error(exc, exc_info=True)

    with_patches_requested = with_patches and get_channel_config(channel).repodata_patches_package
    patches_supported = get_channel_config(channel).artifact_discovery == ArtifactDiscoveryChoice.ANACONDA_API

    if with_patches_requested and not patches_supported:
        st.error(
            "Patched metadata is currently only available for channels with artifact discovery mode `anaconda`."
            "Showing the original metadata."
        )

    if with_patches_requested and patches_supported:
        patched_data, yanked = patched_repodata(channel, subdir, artifact)
    else:
        patched_data = {}
        yanked = _is_broken(package_name, subdir, version, build, extension, channel)

    st.markdown(f'## {"❌ " if yanked else ""}{data["name"]} {data["version"]}')
    if yanked:
        st.error(
            "This artifact has been removed from the index and is only available via URL."
        )
    about = data.get("about") or data.get("rendered_recipe", {}).get("about", {})

    dashboard_urls = {
        dashboard_name: app_config().dashboards[dashboard_name].url_pattern.format(
            channel=channel.split('/', 1)[-1], subdir=subdir, name=data["name"], version=data["version"]
        )
        for dashboard_name in get_channel_config(channel).dashboards
    }
    dashboard_markdown_links = [
        f"[{name}]({url})" for name, url in dashboard_urls.items()
    ]
    dashboard_markdown_links = " · ".join(dashboard_markdown_links) if dashboard_markdown_links else "-"
    build_str = data.get("index", {}).get("build", "*N/A*")
    if build_str == "*N/A*":
        download = "*N/A*"
    else:
        _download_url = get_channel_config(channel).get_artifact_download_url(
            arch_subdir=subdir,
            package_name=data['name'],
            version=data['version'],
            build_string=build_str,
            extension=extension
        )
        download = f"[artifact download]({_download_url})"
    maintainers = []
    for user in (
        data.get("rendered_recipe", {})
        .get("extra", {})
        .get("recipe-maintainers", ["*N/A*"])
    ):
        if user == "*N/A*":
            maintainers.append(user)
        elif "/" in user:  # this is a team
            org, team = user.split("/")
            maintainers.append(f"[{user}](https://github.com/orgs/{org}/teams/{team})")
        else:
            maintainers.append(f"[{user}](https://github.com/{user})")
    maintainers = ", ".join(maintainers)
    project_urls = []
    for urltype in ("home", "dev_url", "doc_url"):
        urls = about.get(urltype)
        if urls is None:
            continue
        urls = [u.strip() for u in urls.split(",")]
        for i, url in enumerate(urls, 1):
            urlname = urltype if i == 1 else f"{urltype} {i}"
            project_urls.append(f"[{urlname}]({url})")
    project_urls = " · ".join(project_urls)
    st.write(
        cleandoc(
            f"""
            > {" ".join(about.get("summary", "*(Summary N/A)*").splitlines())}

            | **Channel** | **Subdir** | **Build** | **Extension** |
            | :---: | :---: | :---: | :---: |
            | `{channel}` | `{subdir}` | `{bar_esc(build_str)}` | `{extension}` |
            | **License** | **Uploaded** | **Maintainers** | **Provenance** |
            | `{bar_esc(about.get("license", "*N/A*"))}` | {uploaded} | {maintainers} | {provenance} |
            | **Links:** | {download} | {project_urls} | {dashboard_markdown_links} |
            """
        )
    )
    st.markdown(" ")
    dependencies = data.get("index", {}).get("depends", ())
    constraints = data.get("index", {}).get("constrains", ())
    run_exports = data.get("rendered_recipe", {}).get("build", {}).get("run_exports", ())
    if dependencies or constraints or run_exports:
        c1, c2 = st.columns([1, 1])
        for title, key, specs, col in [
            ("Dependencies", "depends", dependencies, c1),
            ("Constraints", "constrains", constraints, c2),
        ]:
            if specs:
                with col:
                    st.write(f"### {title}")
                    patched_specs = patched_data.get(key, {})
                    if patched_specs:
                        specs = list(unified_diff(specs, patched_specs, n=100))[3:]
                    specs = "\n".join([s.strip() for s in specs])
                    st.code(specs, language="diff", line_numbers=True)
        if run_exports:
            with c2:
                st.write("### Run exports")
                if not hasattr(run_exports, "items"):
                    run_exports = {"weak": run_exports}
                memfile = StringIO()
                yaml.dump(run_exports, memfile)
                memfile.seek(0)
                st.code(memfile.getvalue(), language="yaml", line_numbers=True)

        st.markdown(" ")

    if data.get("files"):
        st.write("### Files")
        all_files = "\n".join(data["files"])
        st.code(all_files, language="text", line_numbers=True)

    st.write("### Raw JSON")
    st.json(data, expanded=False)
elif data == "show_latest":
    try:
        rss_ret = rss_data(channel)
    except Exception as exc:
        logger.error(exc, exc_info=True)
        st.error(f"Could not obtain RSS data for {channel}! `{exc.__class__.__name__}: {exc}`")
        st.stop()

    if not rss_ret:
        st.info(f"No RSS feed available for {channel}.")
        st.stop()

    table = [
        "| **#** | **Package** | **Version** | **Platform(s)** | **Published** |",
        "| :---: | :---: | :---: | :---: | :---: |",
    ]
    for n, item in enumerate(rss_ret.findall("channel/item"), 1):
        title = item.find("title").text
        name, version, platforms = title.split(" ", 2)
        platforms = platforms[1:-1]
        published = item.find("pubDate").text
        more_url = f"/?q={channel}/{name}"
        table.append(f"| {n} | [{name}]({more_url})| {version} | {platforms} | {published}")
    st.markdown(f"## Latest {n} updates in [{channel}](https://anaconda.org/{channel.split('/', 1)[-1]})")
    st.markdown(f"> Last update: {rss_ret.find('channel/pubDate').text}.")
    st.markdown("\n".join(table))
elif isinstance(data, str) and data.startswith("error:"):
    st.error(data[6:])
elif not data:
    st.info("Nothing to show. Did you fill all fields in the 'Packages' tab?")
