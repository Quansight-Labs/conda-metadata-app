import json
import os
import re
from contextlib import closing
from datetime import datetime
from difflib import unified_diff
from inspect import cleandoc
from io import StringIO
from tempfile import gettempdir

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

from version_order import VersionOrder

yaml = YAML(typ="safe")
yaml.allow_duplicate_keys = True
yaml.default_flow_style = False
logger = get_logger(__name__)
st.set_page_config(
    page_title="conda metadata browser",
    page_icon="📦",
    initial_sidebar_state="expanded",
)
ONE_DAY = 60 * 60 * 24
TWO_HOURS = 60 * 60 * 4
THIRTY_MINS = 60 * 30
FIFTEEN_MINS = 60 * 15
CHANNELS = ["conda-forge", "bioconda", "pkgs/main", "pkgs/r"]
# pkgs/msys2 does not seem to offer .conda artifacts; leave out for now


def bar_esc(s):
    "Escape vertical bars in tables"
    return s.replace("|", "\\|")


@st.cache_resource(ttl=FIFTEEN_MINS, max_entries=5)
def rssdata(channel="conda-forge"):
    if channel.startswith("pkgs/"):
        r = requests.get(f"https://repo.anaconda.com/{channel}/rss.xml")
    else:
        r = requests.get(f"https://conda.anaconda.org/{channel}/rss.xml")
    r.raise_for_status()
    return ET.ElementTree(ET.fromstring(r.text))


@st.cache_resource(ttl=FIFTEEN_MINS, max_entries=10)
def channeldata(channel="conda-forge"):
    if channel.startswith("pkgs/"):
        r = requests.get(f"https://repo.anaconda.com/{channel}/channeldata.json")
    else:
        r = requests.get(f"https://conda.anaconda.org/{channel}/channeldata.json")
    r.raise_for_status()
    return r.json()


@st.cache_resource(ttl=FIFTEEN_MINS, max_entries=1000)
def api_data(package_name, channel="conda-forge"):
    if channel.startswith("pkgs/"):
        channel = channel.split("/", 1)[1]
    r = requests.get(f"https://api.anaconda.org/package/{channel}/{package_name}/files")
    r.raise_for_status()
    return r.json()


@st.cache_resource(ttl=ONE_DAY, max_entries=10)
def repodata_patches(channel="conda-forge"):
    package_name = f"{channel}-repodata-patches"
    data = api_data(package_name, channel)
    most_recent = sorted(data, key=lambda x: x["attrs"]["timestamp"], reverse=True)[0]
    filename, conda = conda_reader_for_url(f"https:{most_recent['download_url']}")

    patches = {}
    with closing(conda):
        for tar, member in stream_conda_component(filename, conda, component="pkg"):
            if member.name.endswith("patch_instructions.json"):
                patches[member.name.split("/")[0]] = json.load(tar.extractfile(member))
    return patches


@st.cache_resource(ttl=ONE_DAY, max_entries=1000)
def provenance_urls(package_name, channel="conda-forge", data=None):
    if not package_name or not data:
        return ""
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
    if channel == "conda-forge":
        feedstocks = package_to_feedstock(package_name)
        return [f"https://github.com/conda-forge/{f}-feedstock" for f in feedstocks]
    elif channel == "bioconda":
        return [
            f"https://github.com/bioconda/bioconda-recipes/tree/master/recipes/{package_name}"
        ]
    elif channel.startswith("pkgs/"):
        return [f"https://github.com/AnacondaRecipes/{package_name}-feedstock"]
    return ""


def package_names(channel="conda-forge"):
    names = [""]
    for name in channeldata(channel)["packages"].keys():
        if channel == "pkgs/r":
            if name not in ("r", "rpy2", "rstudio") and not name.startswith(("r-", "_r-", "mro-")):
                continue
        elif channel == "pkgs/msys2" and not name.startswith(("m2-", "m2w64-", "msys2-")):
            continue
        names.append(name)
    return sorted(
        names,
        key=lambda x: f"zzzzzzz{x}" if x.startswith("_") else x,
    )


def subdirs(package_name, channel="conda-forge", with_broken=False):
    if not package_name:
        return []
    return sorted(
        [
            subdir
            for subdir in channeldata(channel)["packages"][package_name]["subdirs"]
            if versions(package_name, subdir, channel, with_broken=with_broken)
        ]
    )


def _best_version_in_subdir(package_name, channel="conda-forge", with_broken=False):
    if not package_name:
        return None, None
    subdirs_plus_best_version = sorted(
        [
            (subdir, versions(package_name, subdir, channel, with_broken=with_broken)[0])
            for subdir in subdirs(package_name, channel, with_broken=with_broken)
        ],
        key=lambda x: VersionOrder(x[1]),
        reverse=True,
    )
    if subdirs_plus_best_version:
        return subdirs_plus_best_version[0]
    return None, None


def versions(package_name, subdir, channel="conda-forge", with_broken=False):
    if not package_name or not subdir:
        return []
    data = api_data(package_name, channel)
    return sorted(
        {
            pkg["version"]: None
            for pkg in data
            if pkg["attrs"]["subdir"] == subdir
            and "main" in pkg["labels"]
            and (with_broken or "broken" not in pkg["labels"])
        },
        key=VersionOrder,
        reverse=True,
    )


def builds(package_name, subdir, version, channel="conda-forge", with_broken=False):
    if not package_name or not subdir or not version:
        return []
    data = api_data(package_name, channel)
    build_str_to_num = {
        pkg["attrs"]["build"]: pkg["attrs"]["build_number"]
        for pkg in data
        if pkg["attrs"]["subdir"] == subdir
        and pkg["version"] == version
        and "main" in pkg["labels"]
        and (with_broken or "broken" not in pkg["labels"])
    }
    return [
        k
        for k, _ in sorted(
            build_str_to_num.items(), key=lambda kv: (kv[1], kv[0]), reverse=True
        )
    ]


def extensions(package_name, subdir, version, build, channel="conda-forge", with_broken=False):
    if not package_name or not subdir or not version or not build:
        return []
    if channel.startswith("pkgs/"):
        return ["conda"]
    data = api_data(package_name, channel)
    return sorted(
        {
            ("conda" if pkg["basename"].endswith(".conda") else "tar.bz2"): None
            for pkg in data
            if pkg["attrs"]["subdir"] == subdir
            and pkg["version"] == version
            and pkg["attrs"]["build"] == build
            and "main" in pkg["labels"]
            and (with_broken or "broken" not in pkg["labels"])
        }
    )


def _is_broken(package_name, subdir, version, build, extension, channel="conda-forge"):
    if channel != "conda-forge":
        return False  #  we don't know
    data = api_data(package_name, channel)
    for pkg in data:
        if (
            pkg["attrs"]["subdir"] == subdir
            and pkg["version"] == version
            and pkg["attrs"]["build"] == build
            and pkg["basename"].endswith(extension)
        ):
            return "broken" in pkg["labels"]
    return False


def patched_repodata(channel, subdir, artifact):
    patches = repodata_patches(channel)[subdir]
    key = "packages.conda" if artifact.endswith(".conda") else "packages"
    patched_data = patches[key].get(artifact, {})
    yanked = artifact in patches["remove"]
    return patched_data, yanked


def artifact_metadata(channel, subdir, artifact):
    if channel.startswith("pkgs/"):
        return get_artifact_info_as_json(
            channel=f"https://repo.anaconda.com/{channel}",
            subdir=subdir,
            artifact=artifact,
            backend="streamed",
            skip_files_suffixes=(),
        )
    data = get_artifact_info_as_json(
        channel=channel,
        subdir=subdir,
        artifact=artifact,
        backend="oci",
        skip_files_suffixes=(),
    )
    if (data and data.get("name")) or artifact.endswith(".tar.bz2"):
        return data
    # .conda artifacts can be streamed directly from an anaconda.org channel
    return get_artifact_info_as_json(
        channel=channel,
        subdir=subdir,
        artifact=artifact,
        backend="streamed",
        skip_files_suffixes=(),
    )


def is_archived_repo(repo_url_or_owner_repo):
    owner, repo = repo_url_or_owner_repo.split("/")[-2:]
    if owner != "conda-forge":
        return False
    r = requests.get(f"https://api.github.com/repos/{owner}/{repo}")
    if r.ok:
        return r.json().get("archived", False)
    return False


def parse_url_params():
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
        if query in CHANNELS:  # channel only
            channel = query
        elif "/" in query:
            try:
                components = query.split("/")
                if len(components) == 2:  # cannot be pkgs/main because we checked above
                    channel, artifact = components
                    subdir = None
                elif len(components) == 3:
                    channel, subdir, artifact = components
                    if f"{channel}/{subdir}" in CHANNELS:
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
        if _package_name in package_names(url_params["channel"]):
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

with st.sidebar:
    st.title(
        "conda metadata browser",
        help="Web UI to browse the metadata of conda packages uploaded in public channels.",
    )
    with_broken = st.checkbox(
        "Include artifacts marked broken",
        value=False,
        key="with_broken",
        help="Include broken packages in the list of versions and builds.",
    )
    with_patches = st.checkbox(
        "Show patched metadata",
        value=False,
        key="with_patches",
        help="Requires extra API calls. Slow! Only for conda-forge",
    )
    show_archived = st.checkbox(
        "Highlight provenance if archived",
        value=False,
        key="show_archived",
        help="If the source feedstock is archived, the text will be struck through. "
        "Requires extra API calls. Slow! Only for conda-forge",
    )
    channel = st.selectbox(
            "Select a channel:",
            CHANNELS,
            key="channel",
            # Use the user provided channel (via query params) if possible.
            index=CHANNELS.index(url_params["channel"]) if url_params["channel"] in CHANNELS else 0,
        )
    _available_package_names = package_names(channel)
    package_name = st.selectbox(
        "Enter a package name:",
        options=_available_package_names,
        key="package_name",
        help=f"Choose one package out of the {len(_available_package_names) - 1:,} available ones. "
        "Underscore-leading names are sorted last."
    )
    _available_subdirs = subdirs(package_name, channel, with_broken=with_broken)
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
        options=versions(package_name, subdir, channel, with_broken=with_broken),
        key="version",
    )
    # Add a small message if a newer version is available in a different subdir, and
    # the currently chosen version is the newest in the current subdir
    if (
        _best_version
        and version
        and version == versions(package_name, subdir, channel, with_broken=with_broken)[0]
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
        if not data:
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
        provenance = ", ".join(provenance)
    except Exception as exc:
        provenance = "N/A"
        logger.error(exc, exc_info=True)

    if with_patches and channel == "conda-forge":
        patched_data, yanked = patched_repodata(channel, subdir, artifact)
    else:
        patched_data = {}
        yanked = _is_broken(package_name, subdir, version, build, extension, channel)

    st.markdown(f'## {"❌ " if yanked else ""}{data["name"]} {data["version"]}')
    if yanked:
        st.error(
            "This artifact has been removed from the index and it's only available via URL."
        )
    about = data.get("about") or data.get("rendered_recipe", {}).get("about", {})
    dashboard_urls  = [f"[anaconda](https://anaconda.org/{channel.split('/', 1)[-1]}/{data['name']}/files?version={data['version']})"]
    if not channel.startswith("pkgs/"):
        dashboard_urls += [
            f"[ghcr](https://github.com/orgs/channel-mirrors/packages/container/package/{channel}%2F{subdir}%2F{data['name']})",
            f"[prefix](https://prefix.dev/channels/{channel}/packages/{data['name']})",
        ]
    dashboard_urls = " · ".join(dashboard_urls)
    build_str = data.get("index", {}).get("build", "*N/A*")
    if build_str == "*N/A*":
        download = "*N/A*"
    elif channel.startswith("pkgs/"):
        download = f"[artifact download](https://repo.anaconda.com/{channel}/{subdir}/{data['name']}-{data['version']}-{build_str}.{extension})"
    else:
        download = f"[artifact download](https://conda.anaconda.org/{channel}/{subdir}/{data['name']}-{data['version']}-{build_str}.{extension})"
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
            > {" ".join(about.get("summary", "*N/A*").splitlines())}

            | **Channel** | **Subdir** | **Build** | **Extension** |
            | :---: | :---: | :---: | :---: |
            | `{channel}` | `{subdir}` | `{bar_esc(build_str)}` | `{extension}` |
            | **License** | **Uploaded** | **Maintainers** | **Provenance** |
            | `{bar_esc(about.get("license", "*N/A*"))}` | {uploaded} | {maintainers} | {provenance} |
            | **Links:** | {download} | {project_urls} | {dashboard_urls} |
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
        data = rssdata(channel)
    except Exception as exc:
        logger.error(exc, exc_info=True)
        st.error(f"Could not obtain RSS data for {channel}! `{exc.__class__.__name__}: {exc}`")
        st.stop()

    table = [
        "| **#** | **Package** | **Version** | **Platform(s)** | **Published** |",
        "| :---: | :---: | :---: | :---: | :---: |",
    ]
    for n, item in enumerate(data.findall("channel/item"), 1):
        title = item.find("title").text
        name, version, platforms = title.split(" ", 2)
        platforms = platforms[1:-1]
        published = item.find("pubDate").text
        more_url = f"/?q={channel}/{name}"
        table.append(f"| {n} | [{name}]({more_url})| {version} | {platforms} | {published}")
    st.markdown(f"## Latest {n} updates in [{channel}](https://anaconda.org/{channel.split('/', 1)[-1]})")
    st.markdown(f"> Last update: {data.find('channel/pubDate').text}.")
    st.markdown("\n".join(table))
elif isinstance(data, str) and data.startswith("error:"):
    st.error(data[6:])
elif not data:
    st.info("Nothing to show. Did you fill all fields in the 'Packages' tab?")
