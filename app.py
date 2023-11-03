import json
import os
import re
from contextlib import closing
from datetime import datetime
from difflib import unified_diff
from inspect import cleandoc
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
from streamlit.logger import get_logger

from version_order import VersionOrder


logger = get_logger(__name__)
st.set_page_config(
    page_title="conda metadata browser",
    page_icon="📦",
    initial_sidebar_state="expanded",
)
ONE_DAY = 60 * 60 * 24
TWO_HOURS = 60 * 60 * 4
CHANNELS = ["conda-forge", "bioconda", "pkgs/main", "pkgs/r", "pkgs/msys2"]


def bar_esc(s):
    "Escape vertical bars in tables"
    return s.replace("|", "\\|")


@st.cache_data(ttl=TWO_HOURS, max_entries=100)
def channeldata(channel="conda-forge"):
    if channel.startswith("pkgs/"):
        r = requests.get(f"https://repo.anaconda.com/{channel}/channeldata.json")
    else:
        r = requests.get(f"https://conda.anaconda.org/{channel}/channeldata.json")
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=TWO_HOURS, max_entries=100)
def api_data(package_name, channel="conda-forge"):
    if channel.startswith("pkgs/"):
        channel = channel.split("/", 1)[1]
    r = requests.get(f"https://api.anaconda.org/package/{channel}/{package_name}/files")
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=ONE_DAY, max_entries=2)
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


@st.cache_data(ttl=ONE_DAY, max_entries=1000)
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
    names = []
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


def subdirs(package_name, channel="conda-forge"):
    if not package_name:
        return []
    return sorted(channeldata(channel)["packages"][package_name]["subdirs"])


def _best_version_in_subdir(package_name, channel="conda-forge"):
    if not package_name:
        return None, None
    return max(
        [
            (subdir, versions(package_name, subdir, channel)[0])
            for subdir in subdirs(package_name, channel)
        ],
        key=lambda x: VersionOrder(x[1]),
    )


def versions(package_name, subdir, channel="conda-forge"):
    if not package_name or not subdir:
        return []
    data = api_data(package_name, channel)
    return sorted(
        {
            pkg["version"]: None
            for pkg in data
            if pkg["attrs"]["subdir"] == subdir and "main" in pkg["labels"]
        },
        key=VersionOrder,
        reverse=True,
    )


def builds(package_name, subdir, version, channel="conda-forge"):
    if not package_name or not subdir or not version:
        return []
    data = api_data(package_name, channel)
    build_str_to_num = {
        pkg["attrs"]["build"]: pkg["attrs"]["build_number"]
        for pkg in data
        if pkg["attrs"]["subdir"] == subdir and pkg["version"] == version
    }
    return [
        k
        for k, _ in sorted(
            build_str_to_num.items(), key=lambda kv: (kv[1], kv[0]), reverse=True
        )
    ]


def extensions(package_name, subdir, version, build, channel="conda-forge"):
    if not package_name or not subdir or not version or not build:
        return []
    if channel.startswith("pkgs/"):
        return ["conda"]
    data = api_data(package_name, channel)
    return list(
        {
            ("conda" if pkg["basename"].endswith(".conda") else "tar.bz2"): None
            for pkg in data
            if pkg["attrs"]["subdir"] == subdir
            and pkg["version"] == version
            and pkg["attrs"]["build"] == build
        }
    )


@st.cache_data(show_spinner=False)
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
        )
    data = get_artifact_info_as_json(
        channel=channel,
        subdir=subdir,
        artifact=artifact,
        backend="oci",
    )
    if (data and data.get("name")) or artifact.endswith(".tar.bz2"):
        return data
    # .conda artifacts can be streamed directly from an anaconda.org channel
    return get_artifact_info_as_json(
        channel=channel,
        subdir=subdir,
        artifact=artifact,
        backend="streamed",
    )


def is_archived_feedstock(feedstock):
    owner, repo = feedstock.split("/")[-2:]
    if owner != "conda-forge":
        return False
    r = requests.get(f"https://api.github.com/repos/{owner}/{repo}")
    if r.ok:
        return r.json().get("archived", False)
    return False


def parse_url_params():
    channel, subdir, artifact, package_name, version, build, extension = [None] * 7
    url_params = st.experimental_get_query_params()
    ok = True
    if "q" in url_params:
        query = url_params["q"][0]
        try:
            channel, subdir, artifact = query.rsplit("/", 2)
        except Exception as exc:
            logger.error(exc)
            ok = False
        else:
            if artifact:
                if artifact.endswith(".conda"):
                    extension = "conda"
                elif artifact.endswith(".tar.bz2"):
                    extension = "tar.bz2"
                else:
                    extension = None
                    channel, subdir, artifact = [None] * 3
                    ok = False
                if extension:
                    try:
                        package_name, version, build = artifact[
                            : -len(f".{extension}")
                        ].rsplit("-", 2)
                    except Exception as exc:
                        logger.error(exc)
                        ok = False
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
    }, ok


url_params, url_ok = parse_url_params()
if not url_ok:
    st.experimental_set_query_params()
    st.error(
        f"Invalid URL params: `{url_params}`.\n\n"
        "Use syntax `/?q=channel/subdir/package_name-version-build.extension`."
    )
elif url_params["artifact"] and "channel" not in st.session_state:
    # Initialize state from URL params, only on first run
    # These state keys match the sidebar widgets keys below
    st.session_state.channel = url_params["channel"]
    st.session_state.subdir = url_params["subdir"]
    st.session_state.package_name = url_params["package_name"]
    st.session_state.version = url_params["version"]
    st.session_state.build = url_params["build"]
    st.session_state.extension = url_params["extension"]

with st.sidebar:
    st.title(
        "conda metadata browser",
        help="Web UI to browse the conda package metadata exposed at "
        "https://github.com/orgs/channel-mirrors/packages.\n\n "
        "If you need programmatic usage, check the [REST API]"
        "(https://condametadata-1-n5494491.deta.app).",
    )
    channel = st.selectbox(
        "Select a channel:",
        CHANNELS,
        key="channel",
    )
    package_name = st.selectbox(
        "Enter a package name:",
        options=package_names(channel),
        key="package_name",
    )
    _available_subdirs = subdirs(package_name, channel)
    _best_subdir, _best_version = _best_version_in_subdir(package_name, channel)
    _subdir_index = _available_subdirs.index(_best_subdir) if _best_subdir else 0
    subdir = st.selectbox(
        "Select a subdir:",
        options=_available_subdirs,
        index=_subdir_index,
        key="subdir",
    )
    version = st.selectbox(
        "Select a version:",
        options=versions(package_name, subdir, channel),
        key="version",
    )
    # Add a small message if a newer version is available in noarch or native subdir
    if (
        _best_version
        and version
        and VersionOrder(_best_version) > VersionOrder(version)
        and _best_subdir != subdir
        and "noarch" in (_best_subdir, subdir)
    ):
        st.markdown(
            f"<sup>ℹ️ v{_best_version} is available for {_best_subdir}</sup>",
            unsafe_allow_html=True,
        )

    build = st.selectbox(
        "Select a build:",
        options=builds(package_name, subdir, version, channel),
        key="build",
    )
    extension = st.selectbox(
        "Select an extension:",
        options=extensions(package_name, subdir, version, build, channel),
        key="extension",
    )
    if channel == "conda-forge":
        with_patches = st.checkbox(
            "Show patches and broken packages",
            value=False,
            key="with_patches",
            help="Requires extra API calls. Slow!",
        )
        mark_archived_feedstocks = st.checkbox(
            "Mark archived feedstocks",
            value=False,
            key="mark_archived_feedstocks",
            help="Requires extra API calls. Slow!",
        )
    else:
        with_patches = False
        mark_archived_feedstocks = False


def input_value_so_far():
    value = ""
    if channel:
        value = f"{channel}/"
    if package_name:
        if subdir:
            value = f"{value}{subdir}::"
        value = f"{value}{package_name}-"
        if version:
            value = f"{value}{version}-"
            if build:
                value = f"{value}{build}."
                if extension:
                    value = f"{value}{extension}"
    return value


def disable_button(query):
    if re.match(
        r"^[a-z0-9-]+/[a-z0-9-]+::[a-z0-9-]+-[0-9.]+-[a-z0-9_]+.[a-z0-9]+$", query
    ):
        return False
    if all([channel, subdir, package_name, version, build, extension]):
        return False
    return True


c1, c2 = st.columns([1, 0.25])
with c1:
    query = st.text_input(
        label="Search artifact metadata:",
        placeholder="channel/subdir::package_name-version-build.ext",
        value=input_value_so_far(),
        label_visibility="collapsed",
        key="query_input",
    )
with c2:
    submitted = st.button(
        "Submit",
        key="form",
        disabled=disable_button(query),
        use_container_width=True,
    )

if submitted or all([channel, subdir, package_name, version, build, extension]):
    channel_subdir, artifact = query.split("::")
    channel, subdir = channel_subdir.rsplit("/", 1)
    st.experimental_set_query_params(q=f"{channel}/{subdir}/{artifact}")
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
else:
    data = ""

if data:
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
                provenance.append(f"[{name} @ `{commit[:7]}`]({url})")
                continue
            name = url.split("/")[-1]
            if mark_archived_feedstocks:
                if is_archived_feedstock(url):
                    name = f"~~{name}~~"
            provenance.append(f"[{name}]({url})")
        provenance = ", ".join(provenance)
    except Exception as exc:
        provenance = "N/A"
        logger.error(exc, exc_info=True)

    if with_patches:
        patched_data, yanked = patched_repodata(channel, subdir, artifact)
    else:
        patched_data, yanked = {}, False

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
    project_urls = " · ".join(
        [
            f"[{url}]({about.get(url)})"
            for url in ("home", "dev_url", "doc_url")
            if about.get(url)
        ]
    )
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
                text = "\n".join([f"{key}:\n- {'- '.join(value)}" for key, value in run_exports.items()])
                st.code(text, language="yaml", line_numbers=True)

        st.markdown(" ")

    if data.get("files"):
        st.write("### Files")
        all_files = "\n".join(data["files"])
        st.code(all_files, language="text", line_numbers=True)

    st.write("### Raw JSON")
    st.json(data, expanded=False)
